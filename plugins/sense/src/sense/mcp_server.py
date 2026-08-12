"""Local MCP surface for Sense."""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Callable
from importlib import resources
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from . import __version__
from .errors import SenseError
from .exposure import stored_section_id
from .model import (
    ProfileSection,
    ToolError,
    ToolFailure,
    ToolResponse,
    ToolSuccess,
)
from .service import ControlAction, ReadView, SenseService

SERVER_INSTRUCTIONS = (
    "Sense holds a small set of private guidance for important choices that recur in different "
    "contexts. Use it only when that guidance could change a conclusion or when the user asks to "
    "see or change it. The current request and current sources always come first. Read only the "
    "relevant sections and reach an independent conclusion rather than copying their wording. "
    "Project facts stay with the project, continuing source-linked questions stay in the Corpus "
    "chosen by the user, and narrow clues about understanding or explanation stay in Hypes. "
    "Revise Sense only after an explicit correction or an observed result establishes guidance "
    "that should remain useful elsewhere. A preview is read-only."
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
PublicOrigin = Literal["user_set", "learned_from_results"]
McpControlAction = Literal[
    "inspect",
    "export",
    "preview_forget",
    "preview_remove_database",
]
GUIDANCE_UI_URI = "ui://sense/guidance-v1.html"
GUIDANCE_UI_RESOURCE = (
    resources.files("sense")
    .joinpath("ui")
    .joinpath("guidance")
    .joinpath("index.html")
)


class SenseSource(BaseModel):
    """One bounded source locator accepted by the local Sense tools."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["conversation", "file", "corpus", "result"]
    locator: str = Field(min_length=1, max_length=512)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    origin: PublicOrigin


class SenseSection(BaseModel):
    """One complete Sense section accepted by the local Sense tools."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    purpose: str = Field(min_length=1, max_length=320)
    text: str = Field(min_length=1, max_length=12_000)
    origins: list[PublicOrigin] = Field(min_length=1, max_length=2)
    use_for: list[str] = Field(default_factory=list, max_length=16)
    review_when: list[str] = Field(default_factory=list, max_length=12)
    sensitivity: Literal["ordinary", "sensitive"] = "ordinary"
    source_refs: list[SenseSource] = Field(default_factory=list, max_length=12)

    def to_stored(self) -> ProfileSection:
        payload = self.model_dump(mode="json")
        payload["id"] = stored_section_id(payload["id"])
        payload["origins"] = [
            "learned_from_work" if value == "learned_from_results" else value
            for value in payload["origins"]
        ]
        for source in payload["source_refs"]:
            if source["origin"] == "learned_from_results":
                source["origin"] = "learned_from_work"
        return ProfileSection.model_validate(payload)


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
        GUIDANCE_UI_URI,
        name="sense-guidance",
        title="Sense Guidance",
        description="Read-only view of the guidance kept in Sense.",
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
            "Use this when retained Sense guidance could change an important choice, or when the "
            "user asks what Sense contains. Start with view=index and read only the relevant "
            "sections. Use sense_overview for a complete ordinary review and view=full only for "
            "explicit inspection or repair. Sensitive text is absent from the index, and source "
            "locators are omitted unless the user-requested inspection needs them. Read-only."
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
        title="Show Sense",
        description=(
            "Use this when the user asks to review the ordinary guidance kept in Sense. The "
            "read-only view shows each section, when it matters, and broad source types. It omits "
            "source locators, digests, and sensitive sections."
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
            "Use this after an explicit correction or observed result establishes guidance that "
            "should remain useful in other contexts. Do not save project facts, one-project notes, "
            "or clues that belong in Corpus or Hypes. Replace one complete ordinary section using "
            "the current revision and digest from sense_read. The change explanation is checked "
            "but not stored. Preview data and sensitive sections cannot be changed here."
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
        new_section: SenseSection,
    ) -> ToolResponse:
        return _safe_call(
            lambda: service.revise(
                expected_revision=expected_revision,
                section_id=section_id,
                previous_section_sha256=previous_section_sha256,
                previous_understanding=previous_understanding,
                changed_future_judgment=changed_future_judgment,
                new_section=new_section.to_stored(),
                trusted_user_action=False,
            )
        )

    @server.tool(
        name="sense_control",
        title="Manage Sense Data",
        description=(
            "Use this only when the user asks to inspect, export, or remove Sense data. It can "
            "show the full data or an exact removal preview. Activation and deletion require a "
            "trusted local confirmation and cannot be authorized by model text."
        ),
        annotations=PROFILE_CONTROL,
        meta={"ui": {"visibility": ["model"]}},
    )
    def sense_control(
        action: McpControlAction,
        section_id: SectionId | None = None,
        replacement_section: SenseSection | None = None,
    ) -> ToolResponse:
        return _safe_call(
            lambda: service.control(
                action=action,
                section_id=section_id,
                replacement_section=(
                    replacement_section.to_stored()
                    if replacement_section is not None
                    else None
                ),
                trusted_user_action=False,
            )
        )

    @server.tool(
        name="sense_status",
        title="Check Sense",
        description=(
            "Use this when the user asks about the running Sense version, activation, revision, or "
            "local data protection, or when the connection needs diagnosis. It checks only this "
            "local installation."
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
