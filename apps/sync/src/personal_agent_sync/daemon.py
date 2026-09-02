"""Long-running outbound Sync session and local Source reconciliation."""

from __future__ import annotations

import asyncio
import json
import logging
import random
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from .analysis import build_projection, format_id, select_analyzer
from .config import SyncConfig
from .errors import SyncError
from .events import SourceEventMonitor
from .paths import capture_snapshot, resolve_moved_root
from .reconcile import reconcile_all
from .remote import RemoteClient
from .state import SyncState, canonical, now_iso
from .work import WorkExecutor

LOGGER = logging.getLogger(__name__)


class SyncDaemon:
    def __init__(self, config: SyncConfig, token: str) -> None:
        self.config = config
        self.token = token
        self.state = SyncState(config)
        self.remote = RemoteClient(config, token)
        self.work = WorkExecutor(config, self.state)
        self.stopping = asyncio.Event()

    async def close(self) -> None:
        self.stopping.set()
        await self.remote.close()

    async def run(self) -> None:
        source_task = asyncio.create_task(self._source_loop(), name="source-reconcile")
        broker_task = asyncio.create_task(self._broker_loop(), name="remote-broker")
        try:
            done, _pending = await asyncio.wait(
                {source_task, broker_task}, return_when=asyncio.FIRST_EXCEPTION
            )
            for task in done:
                error = task.exception()
                if error is not None:
                    raise error
            await self.stopping.wait()
        finally:
            self.stopping.set()
            for task in (source_task, broker_task):
                task.cancel()
            await asyncio.gather(source_task, broker_task, return_exceptions=True)
            await self.remote.close()

    async def _source_loop(self) -> None:
        changed = asyncio.Event()
        monitor = SourceEventMonitor(asyncio.get_running_loop(), changed)
        next_full_reconcile = 0.0
        try:
            while not self.stopping.is_set():
                now = asyncio.get_running_loop().time()
                try:
                    roots_changed = await asyncio.to_thread(monitor.refresh, self.state)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    roots_changed = False
                    LOGGER.exception("Source event monitor could not be refreshed")
                if roots_changed:
                    next_full_reconcile = 0.0
                if now >= next_full_reconcile:
                    try:
                        await asyncio.to_thread(reconcile_all, self.state)
                        await asyncio.to_thread(monitor.refresh, self.state)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        LOGGER.exception("Source reconciliation failed; retrying later")
                    next_full_reconcile = (
                        asyncio.get_running_loop().time()
                        + self.config.full_reconcile_seconds
                    )

                await self._process_changes()
                timeout = min(
                    self.config.reconcile_seconds,
                    max(
                        0.1,
                        next_full_reconcile - asyncio.get_running_loop().time(),
                    ),
                )
                try:
                    await asyncio.wait_for(changed.wait(), timeout)
                except TimeoutError:
                    continue

                changed.clear()
                try:
                    await asyncio.wait_for(
                        self.stopping.wait(), self.config.event_debounce_seconds
                    )
                    continue
                except TimeoutError:
                    pass
                changed.clear()
                try:
                    await asyncio.to_thread(reconcile_all, self.state)
                    await asyncio.to_thread(monitor.refresh, self.state)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    LOGGER.exception("Source reconciliation failed; retrying later")
                next_full_reconcile = (
                    asyncio.get_running_loop().time()
                    + self.config.full_reconcile_seconds
                )
        finally:
            await asyncio.to_thread(monitor.close)

    async def _process_changes(self) -> None:
        for change in self.state.due_changes(limit=20):
            if self.stopping.is_set():
                return
            try:
                await self._process_change(change)
            except SyncError as error:
                self.state.fail_change(
                    change["connection_key"], change["document_id"], error.code
                )
            except Exception:
                LOGGER.exception("Unexpected failure while processing a Source change")
                self.state.fail_change(
                    change["connection_key"],
                    change["document_id"],
                    "unexpected_local_failure",
                )

    async def _process_change(self, change: dict[str, Any]) -> None:
        corpus_id = change.get("corpus_id")
        if not isinstance(corpus_id, str) or change["access_scope"] != "remote_allowed":
            # A local-only Source remains entirely local and does not block the bounded queue.
            self.state.complete_missing(change["connection_key"], change["document_id"])
            return
        if change["event_kind"] == "deleted":
            await self.remote.update_source_state(
                corpus_id,
                change["document_id"],
                "unavailable",
                now_iso(),
            )
            self.state.complete_missing(change["connection_key"], change["document_id"])
            return
        if change["event_kind"] == "moved" and change.get("last_revision_sha256"):
            await self.remote.update_source_state(
                corpus_id,
                change["document_id"],
                "available",
                now_iso(),
                change["relative_path_nfc"],
            )
            self.state.complete_change(
                change["connection_key"],
                change["document_id"],
                change["last_revision_sha256"],
                change["last_projection_id"],
            )
            return

        root = resolve_moved_root(
            Path(change["root_path"]),
            int(change["root_device"]),
            int(change["root_inode"]),
        )
        if root is None:
            raise SyncError("source_unavailable", "Connection root is unavailable")
        if change.get("last_revision_sha256"):
            await self.remote.update_source_state(
                corpus_id,
                change["document_id"],
                "changed",
                now_iso(),
                change["relative_path_nfc"],
            )
        selected_format = format_id(change["relative_path_nfc"])
        with capture_snapshot(
            root,
            (int(change["root_device"]), int(change["root_inode"])),
            change["local_relative_path"],
            self.config.data_root / "staging",
            int(change["max_transfer_bytes"]),
        ) as snapshot:
            result = await select_analyzer(
                self.state, self.remote, change, snapshot, selected_format
            )
            header, units = build_projection(
                change=change,
                snapshot=snapshot,
                selected_format=selected_format,
                result=result,
            )
            committed = await self.remote.upload_projection(corpus_id, header, units)
        projection_id = committed.get("projectionId")
        if not isinstance(projection_id, str):
            raise SyncError(
                "remote_protocol_error", "projection commit identity is missing"
            )
        self.state.complete_change(
            change["connection_key"],
            change["document_id"],
            snapshot.sha256,
            projection_id,
        )

    async def _broker_loop(self) -> None:
        delay = 1.0
        while not self.stopping.is_set():
            try:
                await self._broker_session()
                delay = 1.0
            except asyncio.CancelledError:
                raise
            except (ConnectionClosed, OSError, TimeoutError, SyncError):
                wait = min(60.0, delay) + random.random()
                delay = min(60.0, delay * 2)
                try:
                    await asyncio.wait_for(self.stopping.wait(), wait)
                except TimeoutError:
                    pass

    async def _broker_session(self) -> None:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "X-Personal-Agent-Device": self.config.device_id,
        }
        async with connect(
            self.config.websocket_url,
            additional_headers=headers,
            max_size=16 * 1024 * 1024,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=10,
        ) as websocket:
            await websocket.send(
                canonical(
                    {
                        "type": "hello",
                        "protocolVersion": 1,
                        "displayName": self.config.display_name,
                        "capabilities": [
                            "work.file.list",
                            "work.file.read",
                            "work.file.write",
                            "work.file.delete",
                            "work.file.select_current",
                            "work.file.restore",
                            "source.reconcile",
                            "document.analyze.local",
                            "document.analyze.remote",
                        ],
                    }
                )
            )
            async for message in websocket:
                if not isinstance(message, str):
                    await websocket.close(4002, "text messages required")
                    return
                try:
                    value = json.loads(message)
                except json.JSONDecodeError:
                    await websocket.close(4002, "invalid JSON")
                    return
                if isinstance(value, dict) and value.get("type") == "hello_ack":
                    continue
                if not isinstance(value, dict) or value.get("type") != "job":
                    await websocket.close(4002, "invalid broker message")
                    return
                result = await self._execute_job(value)
                await websocket.send(
                    canonical({"type": "job_result", "jobId": value["jobId"], **result})
                )

    async def _execute_job(self, job: dict[str, Any]) -> dict[str, Any]:
        required = {
            "jobId",
            "operation",
            "scope",
            "request",
            "maximumResponseBytes",
            "expiresAt",
        }
        if not required.issubset(job) or not isinstance(job.get("jobId"), str):
            return {
                "ok": False,
                "error": {"code": "invalid_job", "message": "job is invalid"},
            }
        cached = self.state.completed_job(job["jobId"], job)
        if cached is not None:
            return cached
        try:
            expires = datetime.fromisoformat(str(job["expiresAt"]))
            if expires <= datetime.now(UTC):
                raise SyncError("job_expired", "job deadline has passed")
            result = await asyncio.to_thread(
                self.work.execute, job["operation"], job["scope"], job["request"]
            )
            response: dict[str, Any] = {"ok": True, "result": result}
        except SyncError as error:
            response = {
                "ok": False,
                "error": {"code": error.code, "message": str(error)},
            }
        except Exception:
            LOGGER.exception("Unexpected failure while executing a broker job")
            response = {
                "ok": False,
                "error": {
                    "code": "local_operation_failed",
                    "message": "the local Work operation failed",
                },
            }
        maximum = job.get("maximumResponseBytes")
        if not isinstance(maximum, int) or maximum < 1:
            response = {
                "ok": False,
                "error": {"code": "invalid_job", "message": "job limit is invalid"},
            }
        elif len(canonical(response).encode()) > maximum:
            response = {
                "ok": False,
                "error": {
                    "code": "response_too_large",
                    "message": "local result exceeds the job response budget",
                },
            }
        self.state.remember_job(job["jobId"], job, response)
        return response
