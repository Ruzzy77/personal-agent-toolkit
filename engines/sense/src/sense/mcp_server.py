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
from pydantic import BaseModel, ConfigDict, Field
from pydantic.json_schema import (
    CoreSchemaOrFieldType,
    GenerateJsonSchema,
    JsonSchemaValue,
)

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
    "Sense supplies durable user guidance for important choices. Current requests and sources have "
    "precedence. Read the index, then the relevant sections. An index entry may advertise a "
    "user-approved Section Skill; opening that section returns the complete workflow guidance. "
    "An explicit user request initiates a revision. Present assistant-drafted or multi-section final "
    "wording before one atomic update. Section tokens provide conflict safety. Sensitive persistence, "
    "sensitive Section Skill changes, and permanent deletion use the local interface. An explicit "
    "user request can replace an ordinary Section Skill after its current version is read."
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


class SectionSkillReplacement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    )
    description: str = Field(min_length=1, max_length=1_000)
    instructions: str = Field(min_length=1, max_length=24_000)


class _InlineInputSchema(GenerateJsonSchema):
    """Expose finite input models without client-side reference resolution."""

    def generate_inner(self, schema: CoreSchemaOrFieldType) -> JsonSchemaValue:
        return self.resolve_ref_schema(super().generate_inner(schema))


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
            "Read durable guidance relevant to the current choice or a user-requested review. Begin "
            "with view=index, where ordinary sections may advertise a user-approved Section Skill. "
            "Continue with the relevant sections to receive their guidance and complete Skill "
            "instructions. Sensitive text is available through an explicit section id."
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
                audience="external_mcp",
            )
        )

    @server.tool(
        name="sense_overview",
        title="Show Sense",
        description=(
            "Show the complete ordinary guidance and linked Section Skills when the user asks to "
            "review Sense. The read-only view omits sensitive sections."
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
            "An explicit user request authorizes atomic replacement of complete ordinary Sense "
            "sections. Read every affected section and use its section_sha256. Identical content "
            "produces a no-op. A section conflict returns the revision to the user. Sensitive changes "
            "use the local interface."
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

    @server.tool(
        name="sense_skill_revise",
        title="Revise Sense Section Skill",
        description=(
            "Replace one complete ordinary Section Skill after an explicit user request. Read the "
            "linked section first, pass the current skill version (or 'absent'), and provide the "
            "complete name, description, and instructions. Identical content is a no-op; a version "
            "conflict preserves the current Skill. Sensitive Skill changes remain local."
        ),
        annotations=PROFILE_WRITE,
        meta={"ui": {"visibility": ["model"]}},
    )
    def sense_skill_revise(
        section_id: Annotated[
            str,
            Field(
                min_length=1,
                max_length=64,
                pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
            ),
        ],
        expected_version: Annotated[str, Field(min_length=6, max_length=128)],
        new_skill: SectionSkillReplacement,
    ) -> ToolResponse:
        return _safe_call(
            lambda: service.section_skill_revise(
                section_id=section_id,
                name=new_skill.name,
                description=new_skill.description,
                instructions=new_skill.instructions,
                expected_version=expected_version,
            )
        )

    for tool in server._tool_manager.list_tools():
        tool.parameters = tool.fn_metadata.arg_model.model_json_schema(
            by_alias=True,
            schema_generator=_InlineInputSchema,
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
