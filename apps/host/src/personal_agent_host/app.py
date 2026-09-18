"""ASGI application: bearer check in front of the streamable-HTTP MCP app."""

from __future__ import annotations

import hmac
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.routing import Mount

from personal_agent_host.config import HostConfig, read_token
from personal_agent_host.jobs import JobManager
from personal_agent_host.server import create_server

MCP_PATH = "/mcp"


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


def build_app(config: HostConfig) -> Starlette:
    jobs = JobManager(config)
    server = create_server(config, jobs)
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

    @asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        await jobs.start()
        async with server.session_manager.run():
            yield

    outer = Starlette(routes=[Mount("/", app=mcp_app)], lifespan=lifespan)
    return BearerGuard(outer, read_token(config))  # type: ignore[return-value]
