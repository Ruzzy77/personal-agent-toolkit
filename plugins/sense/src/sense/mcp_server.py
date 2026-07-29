"""Local MCP surface for the Sense work profile."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

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
    "Sense keeps one private work profile for collaborating with the user across AI tools. "
    "It contains durable ways of working and cross-work learning, not project facts or raw "
    "conversation history. Current user requests, project-owned sources, and executed results "
    "remain authoritative. While the profile is a preview, read it only when the user explicitly "
    "asks. After trusted activation, read it for work that needs interpretation or judgment. "
    "Do not echo its wording mechanically; use it to form an independent view. "
    "Only revise the profile when a completed result or explicit correction changes a future "
    "judgment across work. Project-specific understanding belongs with the project or Corpus. "
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
PROFILE_UI_PATH = (
    Path(__file__).resolve().parents[2] / "ui" / "work-profile" / "index.html"
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


def create_server(data_root: Path | None = None) -> FastMCP:
    service = SenseService(data_root)
    server = FastMCP("Sense", instructions=SERVER_INSTRUCTIONS)

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
        return PROFILE_UI_PATH.read_text(encoding="utf-8")

    @server.tool(
        name="sense_read",
        title="Read Work Profile",
        description=(
            "Read the private shared work profile. Start with view=index, then request only the "
            "relevant section ids. When the user asks to see the whole profile, use "
            "sense_overview. view=full is reserved for explicit structured inspection or repair "
            "when that review screen is insufficient. Sensitive sections are never returned by "
            "index alone. Source "
            "locators are omitted unless include_sources=true is needed for user-requested "
            "inspection or repair. This never changes state."
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
            "Show a read-only review screen of the whole shared work profile when the user "
            "explicitly asks to see or inspect what Sense currently uses. The screen includes "
            "where each part applies, when to review it, and source categories without exposing "
            "source locators, digests, or sensitive section content. This never changes state."
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
            "work changes a future judgment across work. Requires the revision and section "
            "digest returned by sense_read. The previous understanding and changed future "
            "judgment are checked for presence but are not stored. A preview profile rejects "
            "all writes. This MCP call cannot authorize sensitive content or expanded use; those "
            "changes require a trusted local review surface."
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
            "Inspect or export the full work profile and preview the exact effect of forgetting "
            "a section or removing the Sense database. This MCP surface cannot activate, forget, "
            "or remove data because a model-supplied flag is not evidence of the user's approval. "
            "Those actions require a trusted local review surface."
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
            "Return the local Sense schema, preview or active state, current revision and digest, "
            "retained revision count, server version, and private file permissions. It does not "
            "claim that every provider is connected; compare real client reads when checking "
            "cross-platform state."
        ),
        annotations=READ_ONLY,
        meta={"ui": {"visibility": ["model"]}},
    )
    def sense_status() -> ToolResponse:
        return _safe_call(service.status)

    return server


mcp = create_server()


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
