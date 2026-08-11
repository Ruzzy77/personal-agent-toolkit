"""Local MCP surface for the Sense work profile."""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Callable
from importlib import resources
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from . import __version__
from .errors import SenseError
from .model import (
    ProfileSection,
    ToolError,
    ToolFailure,
    ToolResponse,
    ToolSuccess,
)
from .service import ControlAction, ReadView, SenseService

SERVER_INSTRUCTIONS = (
    "Sense keeps one private work profile that AI tools can use when working with the user. "
    "It contains ways of working and lessons that remain useful across different tasks, not "
    "project facts or raw conversation history. Follow the current user request first, and verify "
    "project facts in the project's current sources and executed results. While the profile is a "
    "preview, read it only when the user explicitly asks. After trusted activation, read it for "
    "work that needs interpretation or a consequential choice. Do not repeat its wording "
    "mechanically; use it "
    "to form an independent view. Only revise the profile when a completed result or explicit "
    "correction should change choices in other kinds of work. Work-specific facts, unresolved "
    "questions, gaps, and source-linked interpretation belong with the project or Corpus. The "
    "user's topic-, task-, or responsibility-specific concept understanding and explanation "
    "effects belong to Hypes, not Sense or Corpus. "
    "A preview profile is intentionally read-only until the user reviews and activates it."
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
    idempotentHint=False,
    openWorldHint=False,
)
PROFILE_CONTROL = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)

SectionId = Annotated[str, Field(min_length=1, max_length=64)]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
McpControlAction = Literal[
    "inspect",
    "export",
    "preview_forget",
    "preview_remove_database",
]
PROFILE_UI_URI = "ui://sense/work-profile-v1.html"
PROFILE_UI_RESOURCE = (
    resources.files("sense")
    .joinpath("ui")
    .joinpath("work_profile")
    .joinpath("index.html")
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
    except Exception:
        return ToolResponse(
            ToolFailure(
                error=ToolError(
                    code="unexpected_error",
                    message="unexpected Sense operation failure",
                    details={},
                )
            )
        )


def create_server(data_root: Path | None = None) -> MCPServer:
    service = SenseService(data_root)
    server = MCPServer(
        "Sense",
        version=__version__,
        instructions=SERVER_INSTRUCTIONS,
    )

    @server.resource(
        PROFILE_UI_URI,
        name="sense-work-profile",
        title="Sense Work Profile",
        description="Read-only review screen for the shared work profile.",
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
    def sense_work_profile_resource() -> str:
        return PROFILE_UI_RESOURCE.read_text(encoding="utf-8")

    @server.tool(
        name="sense_read",
        title="Read Work Profile",
        description=(
            "Read the private shared work profile. Start with view=index, then request only the "
            "relevant section ids. Use sense_overview when the user asks to review all non-sensitive "
            "sections in one screen. Use view=full only for an explicit structured inspection or "
            "repair that the review screen cannot support. The index never returns sensitive section "
            "content. Source locators "
            "are omitted unless include_sources=true is needed for a user-requested inspection or "
            "repair. This does not modify the profile."
        ),
        annotations=READ_ONLY,
        meta={"ui": {"visibility": ["model"]}},
    )
    def sense_read(
        view: ReadView = "index",
        section_ids: Annotated[
            list[SectionId] | None,
            Field(max_length=12),
        ] = None,
        include_sources: bool = False,
    ) -> ToolResponse:
        return _safe_call(
            lambda: service.read(
                view=view,
                section_ids=section_ids,
                include_sources=include_sources,
            )
        )

    @server.tool(
        name="sense_overview",
        title="Show Work Profile",
        description=(
            "Open a read-only review screen of the non-sensitive parts of the shared work profile "
            "when the user asks what Sense currently uses. The screen shows where each part applies, "
            "when to review it, and the kinds of sources behind it. It does not expose source "
            "locators, digests, or sensitive section content, and it does not modify the profile."
        ),
        annotations=READ_ONLY,
        meta={
            "ui": {
                "resourceUri": PROFILE_UI_URI,
                "visibility": ["model"],
            },
            "openai/outputTemplate": PROFILE_UI_URI,
            "openai/toolInvocation/invoking": "작업 프로필을 불러오는 중…",
            "openai/toolInvocation/invoked": "작업 프로필을 열었습니다.",
        },
    )
    def sense_overview() -> ToolResponse:
        return _safe_call(service.overview)

    @server.tool(
        name="sense_revise",
        title="Revise Work Profile",
        description=(
            "Replace one whole profile section after an explicit user correction or completed "
            "work shows that future choices in other kinds of work should change. Use the revision "
            "and section digest returned by sense_read. The previous understanding and the "
            "description of what should differ next time are required but are not stored. A "
            "preview profile rejects all writes. This MCP call cannot authorize sensitive content "
            "or broader use; those changes require a trusted local review surface. Do not store "
            "concept mastery or helpful explanation patterns here; Hypes owns that state."
        ),
        annotations=PROFILE_WRITE,
        meta={"ui": {"visibility": ["model"]}},
    )
    def sense_revise(
        expected_revision: Annotated[int, Field(ge=1)],
        section_id: SectionId,
        previous_section_sha256: Sha256,
        previous_understanding: Annotated[str, Field(min_length=1, max_length=2000)],
        changed_future_judgment: Annotated[str, Field(min_length=1, max_length=2000)],
        new_section: ProfileSection,
    ) -> ToolResponse:
        return _safe_call(
            lambda: service.revise(
                expected_revision=expected_revision,
                section_id=section_id,
                previous_section_sha256=previous_section_sha256,
                previous_understanding=previous_understanding,
                changed_future_judgment=changed_future_judgment,
                new_section=new_section,
                trusted_user_action=False,
            )
        )

    @server.tool(
        name="sense_control",
        title="Manage Work Profile",
        description=(
            "Inspect or export the full work profile, or show exactly what would be removed by "
            "forgetting a section or deleting the Sense database. This tool cannot activate the "
            "profile, forget a section, or remove data because a confirmation flag supplied by a "
            "model does not prove the user's approval. Those actions require a trusted local "
            "review surface."
        ),
        annotations=PROFILE_CONTROL,
        meta={"ui": {"visibility": ["model"]}},
    )
    def sense_control(
        action: McpControlAction,
        section_id: SectionId | None = None,
        replacement_section: ProfileSection | None = None,
    ) -> ToolResponse:
        return _safe_call(
            lambda: service.control(
                action=action,
                section_id=section_id,
                replacement_section=replacement_section,
                trusted_user_action=False,
            )
        )

    @server.tool(
        name="sense_status",
        title="Check Work Profile Status",
        description=(
            "Show which Sense build is running, whether the profile is a preview or active, which "
            "revision is current, how many older revisions remain, and whether the private files "
            "have safe permissions. This checks only the local installation. Compare actual reads "
            "from each AI tool when checking whether they use the same profile."
        ),
        annotations=READ_ONLY,
        meta={"ui": {"visibility": ["model"]}},
    )
    def sense_status() -> ToolResponse:
        return _safe_call(service.status)

    return server


mcp = create_server()


def main() -> None:
    transport = os.environ.get("SENSE_MCP_TRANSPORT", "stdio")
    if transport == "stdio":
        mcp.run(transport="stdio")
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
            "SENSE_MCP_HOST must be loopback until an authenticated OAuth resource server is configured"
        )
    mcp.run(
        transport="streamable-http",
        host=host,
        port=int(os.environ.get("SENSE_MCP_PORT", "8000")),
        streamable_http_path=os.environ.get("SENSE_MCP_PATH", "/sense/mcp"),
        stateless_http=True,
        json_response=True,
    )


if __name__ == "__main__":
    main()
