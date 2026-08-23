"""Stateless MCP surface for the Hypes relationship model of the user."""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, TypeAdapter, model_validator

from . import __version__
from .errors import HypesError
from .model import RewriteOperation, ToolError, ToolFailure, ToolResponse, ToolSuccess
from .service import HypesService

SERVER_INSTRUCTIONS = (
    "Hypes is the assistant's private, revisable relationship model of the user. "
    "Stored relationships can shape interpretation and choice, and reusable relationships can evolve "
    "from the current interaction. Current user input has precedence. Knowledge and understanding are "
    "tentative relations rather than fixed traits: do not infer them from project files or collaborative "
    "outputs, and do not require evidence or source bookkeeping. Use the current interaction to adjust "
    "the explanation level and revise the relation when it changes. Focused reads locate relevant "
    "relations and existing objects; one atomic patch applies changes. Hypes stores nonsensitive "
    "reusable relationships. Transcripts, project records, source material, Sense guidance and Corpus "
    "context remain in their respective systems. Hypes operates in the background and becomes visible "
    "through a user-requested review."
)

READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
REWRITE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=False,
)

_READ_SEED_REF_PATTERN = r"^(?:node|pred)_[0-9a-f]{32}$"
_PUBLIC_ERROR_DETAIL_VALUES: dict[str, dict[str, frozenset[str]]] = {
    "reference_type_mismatch": {
        "expected_type": frozenset({"node", "pred"}),
    },
}


class _RuntimeArguments(BaseModel):
    """Carry unsupported field detection through MCP's safe tool boundary."""

    model_config = ConfigDict(extra="ignore", arbitrary_types_allowed=True)
    public_fields: ClassVar[frozenset[str]] = frozenset()
    unsupported_fields: bool = False

    @model_validator(mode="before")
    @classmethod
    def capture_unsupported_fields(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        cleaned = {key: item for key, item in value.items() if key in cls.public_fields}
        cleaned["unsupported_fields"] = any(
            key not in cls.public_fields for key in value
        )
        return cleaned

    def model_dump_one_level(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in type(self).model_fields}


class _ReadRuntimeArguments(_RuntimeArguments):
    public_fields = frozenset(
        {
            "focus",
            "seed_refs",
            "max_hops",
            "limit",
            "continuation",
        }
    )
    focus: Any = None
    seed_refs: Any = None
    max_hops: Any = 1
    limit: Any = 50
    continuation: Any = None


class _RewriteRuntimeArguments(_RuntimeArguments):
    public_fields = frozenset({"operations"})
    operations: Any = None


def _read_input_schema() -> dict[str, Any]:
    """Return the bounded schema advertised to MCP clients."""

    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "focus": {
                "anyOf": [
                    {"type": "string", "minLength": 1, "maxLength": 1000},
                    {"type": "null"},
                ],
                "default": None,
                "description": "A few short terms likely to occur in a stored name, alias, or description.",
            },
            "seed_refs": {
                "anyOf": [
                    {
                        "type": "array",
                        "items": {"type": "string", "pattern": _READ_SEED_REF_PATTERN},
                        "maxItems": 50,
                    },
                    {"type": "null"},
                ],
                "default": None,
                "description": "Node or predicate refs returned by an earlier read.",
            },
            "max_hops": {
                "type": "integer",
                "minimum": 0,
                "maximum": 2,
                "default": 1,
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 200,
                "default": 50,
            },
            "continuation": {
                "anyOf": [
                    {
                        "type": "string",
                        "pattern": r"^outline-v1:[1-9][0-9]{0,9}$",
                    },
                    {"type": "null"},
                ],
                "default": None,
                "description": "Cursor returned by an earlier outline read.",
            },
        },
    }


def _rewrite_input_schema() -> dict[str, Any]:
    """Return an operation-specific patch schema while runtime input stays opaque."""

    operations = TypeAdapter(list[RewriteOperation]).json_schema()
    definitions = operations.pop("$defs", {})
    operations.update(
        {
            "minItems": 1,
            "maxItems": 100,
            "description": (
                "One atomic list of put_node, put_predicate, put_edge, or delete objects."
            ),
        }
    )
    return {
        "$defs": definitions,
        "type": "object",
        "additionalProperties": False,
        "properties": {"operations": operations},
        "required": ["operations"],
    }


def _set_advertised_input_schema(
    server: MCPServer,
    name: str,
    schema: dict[str, Any],
    runtime_arguments: type[_RuntimeArguments],
) -> None:
    """Set discovery schema without moving content validation outside `_safe_call`.

    MCPServer currently derives runtime validation and discovery from one function
    annotation model and has no public schema override. The runtime model therefore
    carries raw field values to our safe service boundary while reducing unsupported
    field names to a boolean; the strict client-facing schema remains fully advertised.
    """

    tool = server._tool_manager.get_tool(name)
    if tool is None:  # pragma: no cover - registration immediately precedes this call
        raise RuntimeError(f"MCP tool was not registered: {name}")
    tool.parameters = schema
    tool.fn_metadata.arg_model = runtime_arguments


def _safe_call(operation: Callable[[], Any]) -> ToolResponse:
    try:
        return ToolResponse(ToolSuccess(result=operation()))
    except HypesError as exc:
        return ToolResponse(
            ToolFailure(
                error=ToolError(
                    code=exc.code,
                    message=str(exc),
                    details=_public_error_details(exc),
                )
            )
        )
    except (TypeError, ValueError):
        return ToolResponse(
            ToolFailure(
                error=ToolError(
                    code="invalid_request",
                    message="invalid Hypes request",
                    details={},
                )
            )
        )
    except Exception:  # noqa: BLE001 - public responses must not expose internals
        return ToolResponse(
            ToolFailure(
                error=ToolError(
                    code="unexpected_error",
                    message="unexpected Hypes operation failure",
                    details={},
                )
            )
        )


def _public_error_details(error: HypesError) -> dict[str, str]:
    """Return only fixed, non-caller-controlled error metadata."""

    allowed_fields = _PUBLIC_ERROR_DETAIL_VALUES.get(error.code, {})
    public: dict[str, str] = {}
    for field, allowed_values in allowed_fields.items():
        value = error.details.get(field)
        if isinstance(value, str) and value in allowed_values:
            public[field] = value
    return public


def _reject_unsupported_fields() -> Any:
    raise HypesError(
        "invalid_request",
        "the Hypes request contains unsupported fields",
    )


def create_server(
    data_root: Path | None = None,
    *,
    prepare: bool = True,
) -> MCPServer:
    service = HypesService(data_root, prepare=prepare)
    server = MCPServer("Hypes", version=__version__, instructions=SERVER_INSTRUCTIONS)

    @server.tool(
        name="hypes_read",
        title="Read User Relationship Model",
        description=(
            "Read a focused relationship slice for the current interpretation, choice or "
            "user-requested review. Search with a few short terms or continue from refs returned by "
            "an earlier read. Results express the assistant's revisable model."
        ),
        annotations=READ_ONLY,
    )
    def hypes_read(
        focus: Any = None,
        seed_refs: Any = None,
        max_hops: Any = 1,
        limit: Any = 50,
        continuation: Any = None,
        unsupported_fields: bool = False,
    ) -> ToolResponse:
        if unsupported_fields:
            return _safe_call(_reject_unsupported_fields)
        return _safe_call(
            lambda: service.read(
                focus=focus,
                seed_refs=seed_refs,
                max_hops=max_hops,
                limit=limit,
                continuation=continuation,
            )
        )

    @server.tool(
        name="hypes_rewrite",
        title="Rewrite User Relationship Model",
        description=(
            "Maintain reusable relationships with one atomic patch. A focused read locates existing "
            "objects for replacement. Use put_node, put_predicate, put_edge and delete operations. "
            "Node or predicate deletion removes incident edges. Hypes content consists of nonsensitive "
            "user-model relationships."
        ),
        annotations=REWRITE,
    )
    def hypes_rewrite(
        operations: Any = None,
        unsupported_fields: bool = False,
    ) -> ToolResponse:
        if unsupported_fields:
            return _safe_call(_reject_unsupported_fields)
        return _safe_call(lambda: service.rewrite(operations=operations))

    _set_advertised_input_schema(
        server,
        "hypes_read",
        _read_input_schema(),
        _ReadRuntimeArguments,
    )
    _set_advertised_input_schema(
        server,
        "hypes_rewrite",
        _rewrite_input_schema(),
        _RewriteRuntimeArguments,
    )
    return server


mcp = create_server(prepare=False)


def main() -> None:
    server = create_server()
    transport = os.environ.get("HYPES_MCP_TRANSPORT", "stdio")
    if transport == "stdio":
        server.run(transport="stdio")
        return
    if transport != "streamable-http":
        raise ValueError("HYPES_MCP_TRANSPORT must be stdio or streamable-http")
    host = os.environ.get("HYPES_MCP_HOST", "127.0.0.1")
    try:
        loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    if not loopback:
        raise ValueError(
            "HYPES_MCP_HOST must be loopback until an authenticated OAuth resource server is "
            "configured"
        )
    server.run(
        transport="streamable-http",
        host=host,
        port=int(os.environ.get("HYPES_MCP_PORT", "8000")),
        streamable_http_path=os.environ.get("HYPES_MCP_PATH", "/hypes/mcp"),
        stateless_http=True,
        json_response=True,
    )


if __name__ == "__main__":
    main()
