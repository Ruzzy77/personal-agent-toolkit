"""ASGI application: bearer check in front of the streamable-HTTP MCP app."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.transport_security import TransportSecuritySettings
from personal_agent_sync.credentials import read_token as read_sync_token
from personal_agent_sync.daemon import SyncDaemon
from personal_agent_sync.errors import SyncError
from starlette.applications import Starlette
from starlette.routing import Mount

from personal_agent_host.config import HostConfig, read_token
from personal_agent_host.jobs import JobManager
from personal_agent_host.server import create_server
from personal_agent_host.transfer_http import TransferHTTP
from personal_agent_host.transfers import Transfers
from personal_agent_host.workspace_files import WorkspaceFiles

MCP_PATH = "/mcp"
log = logging.getLogger("personal_agent_host")


class BearerGuard:
    def __init__(self, app: Any, token: str) -> None:
        self.app = app
        self.token = token.encode("utf-8")

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        header = b""
        for name, value in scope.get("headers", []):
            if name == b"authorization":
                header = value
                break
        presented = header[7:] if header[:7].lower() == b"bearer " else b""
        if not presented or not hmac.compare_digest(presented, self.token):
            body = json.dumps(
                {"error": "unauthorized", "message": "a valid bearer token is required"}
            ).encode("utf-8")
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"www-authenticate", b"Bearer"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)


def start_sync(config: HostConfig) -> tuple[SyncDaemon, asyncio.Task[None]] | None:
    """Run the Sync loop in-process once this device holds a Sync credential."""

    try:
        token = read_sync_token(config.sync.device_id)
    except SyncError as exc:
        log.warning("Sync loop not started: %s", exc)
        return None
    daemon = SyncDaemon(config.sync, token)
    task = asyncio.create_task(daemon.run(), name="sync-loop")

    def report(done: asyncio.Task[None]) -> None:
        if not done.cancelled() and done.exception() is not None:
            log.error("Sync loop stopped: %s", done.exception())

    task.add_done_callback(report)
    return daemon, task


def build_app(config: HostConfig) -> Starlette:
    jobs = JobManager(config)
    transfers = Transfers(config)
    workspace = WorkspaceFiles(config)
    server = create_server(config, jobs, transfers, workspace)
    listen = f"{config.listen_host}:{config.listen_port}"
    security = TransportSecuritySettings(
        allowed_hosts=[
            *config.allowed_hosts,
            listen,
            config.listen_host,
            "localhost",
            f"localhost:{config.listen_port}",
        ],
    )
    mcp_app = server.streamable_http_app(
        streamable_http_path=MCP_PATH,
        json_response=True,
        stateless_http=True,
        transport_security=security,
        host=config.listen_host,
    )

    async def expire_files() -> None:
        while True:
            await asyncio.sleep(3600)
            try:
                await asyncio.to_thread(transfers.expire)
                await asyncio.to_thread(workspace.expire)
            except Exception:
                log.exception("File retention cleanup failed")

    @asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        await jobs.start()
        await asyncio.to_thread(workspace.expire)
        retention = asyncio.create_task(expire_files(), name="file-retention")
        sync = start_sync(config)
        try:
            async with server.session_manager.run():
                yield
        finally:
            retention.cancel()
            await asyncio.gather(retention, return_exceptions=True)
            if sync is not None:
                sync[0].stopping.set()
                sync[1].cancel()
                await asyncio.gather(sync[1], return_exceptions=True)

    outer = Starlette(
        routes=[*TransferHTTP(transfers).routes(), Mount("/", app=mcp_app)],
        lifespan=lifespan,
    )
    return BearerGuard(outer, read_token(config))  # type: ignore[return-value]
