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

from .analysis import (
    build_projection,
    format_id,
    local_analyzer_manifest,
    select_analyzer,
)
from .config import SyncConfig
from .errors import SyncError
from .events import SourceEventMonitor
from .paths import capture_snapshot, cleanup_abandoned_captures, resolve_moved_root
from .reconcile import reconcile_all
from .remote import RemoteClient
from .state import SyncState, canonical, now_iso
from .work import SYNC_OPERATIONS, WorkExecutor

LOGGER = logging.getLogger(__name__)
ANALYZER_REFRESH_BATCH = 20
REMOTE_RETENTION_BATCH = 50


class SyncDaemon:
    def __init__(self, config: SyncConfig, token: str) -> None:
        self.config = config
        self.token = token
        self.state = SyncState(config)
        self.remote = RemoteClient(config, token)
        self.work = WorkExecutor(config, self.state)
        self.source_change_lock = asyncio.Lock()
        self.stopping = asyncio.Event()
        self._retention_failures: set[str] = set()

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
        analyzer_manifest: dict[str, dict[str, str]] = {}
        try:
            analyzer_manifest = await asyncio.to_thread(
                local_analyzer_manifest, self.config.document_files_python
            )
        except SyncError:
            LOGGER.exception(
                "Document Files descriptors are unavailable; automatic adapter refresh is disabled"
            )
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
                    reconciled = False
                    try:
                        await asyncio.to_thread(
                            cleanup_abandoned_captures,
                            self.config.data_root / "staging",
                        )
                        await asyncio.to_thread(reconcile_all, self.state)
                        await asyncio.to_thread(monitor.refresh, self.state)
                        reconciled = True
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        LOGGER.exception("Source reconciliation failed; retrying later")
                    if reconciled:
                        await self._maintain_remote_retention()
                    next_full_reconcile = (
                        asyncio.get_running_loop().time()
                        + self.config.full_reconcile_seconds
                    )

                if analyzer_manifest:
                    await asyncio.to_thread(
                        self.state.enqueue_outdated_analyzers,
                        analyzer_manifest,
                        ANALYZER_REFRESH_BATCH,
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

    async def _maintain_remote_retention(self) -> None:
        corpus_ids = sorted(
            {
                connection.corpus_id
                for connection in self.config.connections
                if "source" in connection.roles
                and connection.access_scope == "remote_allowed"
                and connection.corpus_id is not None
            }
        )

        async def maintain(corpus_id: str) -> None:
            try:
                await self.remote.maintain_corpus(
                    corpus_id,
                    remove_projection_ids=[],
                    remove_document_ids=[],
                    remove_upload_ids=[],
                    apply_retention_limit=REMOTE_RETENTION_BATCH,
                )
            except asyncio.CancelledError:
                raise
            except SyncError as error:
                if corpus_id not in self._retention_failures:
                    LOGGER.warning(
                        "Remote retention paused for Corpus %s: %s",
                        corpus_id,
                        error.code,
                    )
                    self._retention_failures.add(corpus_id)
            except Exception:
                if corpus_id not in self._retention_failures:
                    LOGGER.exception("Remote retention paused for Corpus %s", corpus_id)
                    self._retention_failures.add(corpus_id)
            else:
                if corpus_id in self._retention_failures:
                    LOGGER.info("Remote retention resumed for Corpus %s", corpus_id)
                    self._retention_failures.remove(corpus_id)

        await asyncio.gather(*(maintain(corpus_id) for corpus_id in corpus_ids))

    async def _process_changes(self) -> None:
        for change in self.state.due_changes(limit=20):
            if self.stopping.is_set():
                return
            async with self.source_change_lock:
                current = self.state.queued_change(
                    change["space_id"],
                    change["connection_id"],
                    change["document_id"],
                )
                if current is None:
                    continue
                try:
                    await self._process_change(current)
                except SyncError as error:
                    self.state.fail_change(
                        current["connection_key"], current["document_id"], error.code
                    )
                except Exception:
                    LOGGER.exception(
                        "Unexpected failure while processing a Source change"
                    )
                    self.state.fail_change(
                        current["connection_key"],
                        current["document_id"],
                        "unexpected_local_failure",
                    )

    async def _process_change(self, change: dict[str, Any]) -> None:
        force_refresh = change["event_kind"] in {"refresh", "analyzer_refresh"}
        corpus_id = change.get("corpus_id")
        if not isinstance(corpus_id, str) or change["access_scope"] != "remote_allowed":
            # A local-only Source remains entirely local and does not block the bounded queue.
            self.state.complete_missing(change["connection_key"], change["document_id"])
            return
        if change["event_kind"] == "deleted":
            try:
                await self.remote.update_source_state(
                    corpus_id,
                    change["document_id"],
                    "unavailable",
                    now_iso(),
                    change["relative_path_nfc"],
                    logical_size=int(change["size"]),
                    modified_ns=int(change["modified_ns"]),
                    residency_state="resident",
                )
            except SyncError as error:
                # Absence already satisfies deletion. This also drains stale
                # pre-migration queue entries whose provisional local IDs were
                # never canonical remote document IDs.
                if error.code != "document_not_found":
                    raise
            self.state.complete_missing(change["connection_key"], change["document_id"])
            return
        remote_document_missing = False
        if change["event_kind"] == "moved" and change.get("last_revision_sha256"):
            try:
                await self.remote.update_source_state(
                    corpus_id,
                    change["document_id"],
                    "available",
                    now_iso(),
                    change["relative_path_nfc"],
                    logical_size=int(change["size"]),
                    modified_ns=int(change["modified_ns"]),
                    residency_state="resident",
                    eligibility_state="supported",
                )
            except SyncError as error:
                if error.code != "document_not_found":
                    raise
                remote_document_missing = True
            else:
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
        with capture_snapshot(
            root,
            (int(change["root_device"]), int(change["root_inode"])),
            change["local_relative_path"],
            self.config.data_root / "staging",
            int(change["max_transfer_bytes"]),
        ) as snapshot:
            if not force_refresh and snapshot.sha256 == change.get(
                "last_revision_sha256"
            ):
                previous_projection = change.get("last_projection_id")
                if isinstance(previous_projection, str):
                    if not remote_document_missing:
                        try:
                            await self.remote.update_source_state(
                                corpus_id,
                                change["document_id"],
                                "available",
                                now_iso(),
                                change["relative_path_nfc"],
                                logical_size=snapshot.byte_size,
                                modified_ns=snapshot.modified_ns,
                                residency_state="resident",
                                eligibility_state="supported",
                            )
                        except SyncError as error:
                            if error.code != "document_not_found":
                                raise
                            remote_document_missing = True
                    if not remote_document_missing:
                        self.state.complete_change(
                            change["connection_key"],
                            change["document_id"],
                            snapshot.sha256,
                            previous_projection,
                        )
                        return
                else:
                    self.state.complete_unsupported(
                        change["connection_key"],
                        change["document_id"],
                        snapshot.sha256,
                    )
                    return
            if (
                not remote_document_missing
                and change.get("last_revision_sha256")
                and snapshot.sha256 != change.get("last_revision_sha256")
            ):
                try:
                    await self.remote.update_source_state(
                        corpus_id,
                        change["document_id"],
                        "changed",
                        now_iso(),
                        change["relative_path_nfc"],
                        logical_size=snapshot.byte_size,
                        modified_ns=snapshot.modified_ns,
                        residency_state="resident",
                        eligibility_state="supported",
                    )
                except SyncError as error:
                    if error.code != "document_not_found":
                        raise
            try:
                selected_format = format_id(change["relative_path_nfc"])
                result = await select_analyzer(
                    self.state, self.remote, change, snapshot, selected_format
                )
            except SyncError as error:
                if error.code != "unsupported_format":
                    raise
                previous_projection = change.get("last_projection_id")
                if force_refresh:
                    if isinstance(previous_projection, str):
                        self.state.complete_change(
                            change["connection_key"],
                            change["document_id"],
                            snapshot.sha256,
                            previous_projection,
                        )
                    else:
                        self.state.complete_unsupported(
                            change["connection_key"],
                            change["document_id"],
                            snapshot.sha256,
                        )
                    raise
                self.state.complete_unsupported(
                    change["connection_key"], change["document_id"], snapshot.sha256
                )
                return
            revision_id = None
            if snapshot.sha256 == change.get("last_revision_sha256"):
                revision_id = await self.remote.resolve_revision(
                    corpus_id,
                    change["document_id"],
                    snapshot.sha256,
                    snapshot.byte_size,
                )
            header, units = build_projection(
                change=change,
                snapshot=snapshot,
                selected_format=selected_format,
                result=result,
                revision_id=revision_id,
            )
            committed = await self.remote.upload_projection(corpus_id, header, units)
        projection_id = committed.get("projectionId")
        if not isinstance(projection_id, str):
            raise SyncError(
                "remote_protocol_error", "projection commit identity is missing"
            )
        previous_projection = change.get("last_projection_id")
        if (
            isinstance(previous_projection, str)
            and previous_projection != projection_id
        ):
            await self.remote.maintain_corpus(
                corpus_id,
                remove_projection_ids=[previous_projection],
                remove_document_ids=[],
                remove_upload_ids=[],
            )
        self.state.complete_change(
            change["connection_key"],
            change["document_id"],
            snapshot.sha256,
            projection_id,
            adapter_id=header["projection"]["adapterId"],
            adapter_version=header["projection"]["adapterVersion"],
            config_hash=header["projection"]["configHash"],
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
                        "capabilities": list(SYNC_OPERATIONS),
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
                if isinstance(value, dict) and value.get("type") == "job_ack":
                    if (
                        not isinstance(value.get("jobId"), str)
                        or type(value.get("accepted")) is not bool
                    ):
                        await websocket.close(4002, "invalid broker acknowledgement")
                        return
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
            if job["operation"] == "source.refresh":
                scope = job["scope"]
                request = job["request"]
                if not isinstance(scope, dict) or not isinstance(request, dict):
                    raise SyncError("invalid_job", "job Source request is invalid")
                async with self.source_change_lock:
                    result = await asyncio.to_thread(
                        self.work.execute, job["operation"], scope, request
                    )
                    space_id = scope.get("spaceId")
                    connection_id = scope.get("connectionId")
                    document_id = request.get("document_id")
                    if not all(
                        isinstance(value, str)
                        for value in (space_id, connection_id, document_id)
                    ):
                        raise SyncError("invalid_job", "job Source request is invalid")
                    change = self.state.queued_change(
                        space_id, connection_id, document_id
                    )
                    if change is None:
                        raise SyncError(
                            "refresh_not_queued", "Source refresh was not queued"
                        )
                    try:
                        await self._process_change(change)
                    except SyncError as error:
                        self.state.fail_change(
                            change["connection_key"], document_id, error.code
                        )
                        raise
                    result = {
                        **result,
                        **self.state.refresh_result(
                            space_id, connection_id, document_id
                        ),
                    }
            else:
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
