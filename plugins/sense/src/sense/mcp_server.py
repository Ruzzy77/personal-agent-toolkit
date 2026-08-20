"""Local MCP surface for Sense."""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Callable
from importlib import resources
from pathlib import Path
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from . import __version__
from .errors import SenseError
from .model import (
    MAX_CHANGES,
    SectionChange,
    ToolError,
    ToolFailure,
    ToolResponse,
    ToolSuccess,
)
from .service import ReadView, SenseService

SERVER_INSTRUCTIONS = (
    "Use Sense only when durable guidance could change an important choice or when the user asks "
    "to inspect or change it. Current requests and current sources take priority. Read the index "
    "first, then only relevant sections. Change Sense only at the user's request. When the assistant "
    "drafts wording or several sections change, show the final wording in the conversation before "
    "one atomic update. A section token protects against conflicting edits. Sensitive persistence "
    "and permanent deletion remain local."
)

READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
PROFILE_WRITE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
GUIDANCE_UI_URI = "ui://sense/guidance-v1.html"
GUIDANCE_UI_RESOURCE = (
    resources.files("sense").joinpath("ui").joinpath("guidance").joinpath("index.html")
)


def _safe_call(operation: Callable[[], Any]) -> ToolResponse:
    try:
        result = operation()
        return ToolResponse(ToolSuccess(result=result))
    except SenseError as exc:
        return ToolResponse(
            ToolFailure(
                error=ToolError(
                    code=exc.code,
                    message=str(exc),
                    details=exc.details,
                )
            )
        )
    except (TypeError, ValueError) as exc:
        return ToolResponse(
            ToolFailure(
                error=ToolError(
                    code="invalid_request",
                    message=str(exc),
                    details={},
                )
            )
        )
    except Exception:  # noqa: BLE001 - public responses must not expose internals
        return ToolResponse(
            ToolFailure(
                error=ToolError(
                    code="unexpected_error",
                    message="unexpected Sense operation failure",
                    details={},
                )
            )
        )


def create_server(
    data_root: Path | None = None,
    *,
    prepare: bool = True,
) -> MCPServer:
    service = SenseService(data_root, prepare=prepare)
    server = MCPServer(
        "Sense",
        version=__version__,
        instructions=SERVER_INSTRUCTIONS,
    )

    @server.resource(
        GUIDANCE_UI_URI,
        name="sense-guidance",
        title="Sense Guidance",
        description="Read-only view of the current guidance kept in Sense.",
        mime_type="text/html;profile=mcp-app",
        meta={
            "ui": {
                "prefersBorder": True,
                "csp": {
                    "connectDomains": [],
                    "resourceDomains": [],
                },
            }
        },
    )
    def sense_guidance_resource() -> str:
        return GUIDANCE_UI_RESOURCE.read_text(encoding="utf-8")

    @server.tool(
        name="sense_read",
        title="Read Sense",
        description=(
            "Read Sense only when retained guidance could change the current choice or when the user "
            "asks what it contains. Start with view=index and then request only relevant sections. "
            "Sensitive text is absent from the index but can be read by explicit section id."
        ),
        annotations=READ_ONLY,
        meta={"ui": {"visibility": ["model"]}},
    )
    def sense_read(
        view: ReadView = "index",
        section_ids: Annotated[
            list[Annotated[str, Field(min_length=1, max_length=64)]] | None,
            Field(max_length=12),
        ] = None,
    ) -> ToolResponse:
        return _safe_call(
            lambda: service.read(
                view=view,
                section_ids=section_ids,
            )
        )

    @server.tool(
        name="sense_overview",
        title="Show Sense",
        description=(
            "Show the complete ordinary guidance when the user asks to review Sense. The read-only "
            "view omits sensitive sections."
        ),
        annotations=READ_ONLY,
        meta={
            "ui": {
                "resourceUri": GUIDANCE_UI_URI,
                "visibility": ["model"],
            },
            "openai/outputTemplate": GUIDANCE_UI_URI,
            "openai/toolInvocation/invoking": "Sense 내용을 불러오는 중…",
            "openai/toolInvocation/invoked": "Sense 내용을 열었습니다.",
        },
    )
    def sense_overview() -> ToolResponse:
        return _safe_call(service.overview)

    @server.tool(
        name="sense_revise",
        title="Revise Sense",
        description=(
            "Replace one or more complete ordinary Sense sections atomically after the user asks to "
            "save the final wording. Read every affected section first and use its section_sha256. "
            "An exact repeated final state is a no-op. Do not retry a section conflict automatically. "
            "Sensitive changes require a local command."
        ),
        annotations=PROFILE_WRITE,
        meta={"ui": {"visibility": ["model"]}},
    )
    def sense_revise(
        changes: Annotated[
            list[SectionChange],
            Field(min_length=1, max_length=MAX_CHANGES),
        ],
    ) -> ToolResponse:
        return _safe_call(
            lambda: service.revise(
                changes=changes,
                trusted_user_action=False,
            )
        )

    return server


mcp = create_server(prepare=False)


def main() -> None:
    server = create_server()
    transport = os.environ.get("SENSE_MCP_TRANSPORT", "stdio")
    if transport == "stdio":
        server.run(transport="stdio")
        return
    if transport != "streamable-http":
        raise ValueError("SENSE_MCP_TRANSPORT must be stdio or streamable-http")
    host = os.environ.get("SENSE_MCP_HOST", "127.0.0.1")
    try:
        loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    if not loopback:
        raise ValueError(
            "SENSE_MCP_HOST must be loopback until an authenticated OAuth resource server "
            "is configured"
        )
    server.run(
        transport="streamable-http",
        host=host,
        port=int(os.environ.get("SENSE_MCP_PORT", "8000")),
        streamable_http_path=os.environ.get("SENSE_MCP_PATH", "/sense/mcp"),
        stateless_http=True,
        json_response=True,
    )


if __name__ == "__main__":
    main()
