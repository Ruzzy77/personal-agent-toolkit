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
    "Hypes is the assistant's revisable relationship model of the user. Use the first matching "
    "branch for the current interaction. (1) If a literal output explicitly takes a stored term or "
    "relation as input and has no separate reusable change or generalization, read only that "
    "structure and never rewrite task-local terms, equivalences, facts, alternatives, or instructions. "
    "(2) If the interaction directly states the reusable relation to "
    "create, or corrects, replaces, or deletes one, read the existing slice and then rewrite it; a "
    "request for the agent to derive a generalization uses branch 3. (3) "
    "If the request "
    "explicitly asks to generalize several stored relations into a reusable higher-level relation, "
    "read the source edges and then write only the minimal synthesized reusable structure. Reuse a "
    "predicate when it fits and create one only when needed; never copy a task, event, subject, or "
    "source fact. (4) If an existing relation could materially change a non-literal "
    "interpretation, explanation, question, or choice, read and apply it without rewriting; this "
    "includes a stored criterion that could select between live alternatives. A request to inspect "
    "the model uses a bounded read. (5) Otherwise make no Hypes call. Use known relevant refs; "
    "otherwise every ordinary read starts with one to three short anchors. It is complete when it returns "
    "the stored object needed by its branch. Applying, choosing by, correcting, replacing, deleting, "
    "or generalizing a relationship requires the relevant edge; direct inspection or literal use of "
    "one node or predicate may complete without an edge. When read_state is present and a relationship "
    "is required, follow next_action_if_relationship_required; complete_if_relevant completes only for "
    "a needed returned edge and otherwise stops without widening. When the branch needs a relationship "
    "and an outline returns candidates, take only the relevant node_id and predicate_id values and pass "
    "them as seed_refs in one follow-up "
    "read before answering or rewriting. When read_state is absent, use that same follow-up after "
    "exactly one small outline read. Stop without unrelated reads when no candidate or required edge "
    "is found. Use "
    "continuation_action only for an explicit request to inspect the whole model. "
    "Branch 2 may create an explicitly stated reusable relation after confirming no old structure; "
    "branch 3 requires actually read source edges. The current message always controls the answer "
    "but does not itself authorize a rewrite. Never announce that you will read or check the model "
    "or tools; return only the requested output. Task-local output premises never authorize a "
    "rewrite; branches 2 and 3 write only the separately stated or derived reusable structure. Read "
    "only relevant structure and do not expose its categories. Prefer one "
    "coherent replacement patch over accumulation. Use reusable aliases, but never store transcripts, "
    "task or project facts, source material, credentials, direct identifiers, sensitive traits, or "
    "hidden reasoning."
)

READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
REWRITE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=False,
    openWorldHint=False,
)

_READ_SEED_REF_PATTERN = r"^(?:node|pred)_[0-9a-f]{32}$"
_READ_MODES = frozenset(
    {"focused", "seeded", "focused_seeded", "outline", "outline_continuation"}
)
_SLICE_STATES = frozenset({"empty", "objects_without_edges", "edges_present"})
_RELATIONSHIP_NEXT_ACTIONS = frozenset(
    {
        "one_outline",
        "read_relevant_returned_seed_refs",
        "stop_without_widening",
        "complete_if_relevant",
    }
)
_CONTINUATION_ACTIONS = frozenset(
    {"none", "continue_only_for_explicit_full_inspection"}
)
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
        {"focus", "seed_refs", "max_hops", "limit", "continuation"}
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
    """Return the strict schema advertised to MCP clients."""

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
                "description": (
                    "One to three short anchors likely to occur in a stored name, alias, or "
                    "description; do not paste the full request."
                ),
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
                "description": "Persistent node or predicate refs from an earlier read.",
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
                "description": (
                    "Cursor returned by an earlier outline read. Use it only without "
                    "focus or seed_refs."
                ),
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


def _read_result_with_state(
    service: HypesService,
    *,
    focus: Any,
    seed_refs: Any,
    max_hops: Any,
    limit: Any,
    continuation: Any,
) -> dict[str, Any]:
    """Add slice-local navigation state after the service validates one read."""

    result = service.read(
        focus=focus,
        seed_refs=seed_refs,
        max_hops=max_hops,
        limit=limit,
        continuation=continuation,
    )

    has_focus = focus is not None
    has_seeds = bool(seed_refs)
    if continuation is not None:
        read_mode = "outline_continuation"
    elif has_focus and has_seeds:
        read_mode = "focused_seeded"
    elif has_focus:
        read_mode = "focused"
    elif has_seeds:
        read_mode = "seeded"
    else:
        read_mode = "outline"

    if result["edges"]:
        slice_state = "edges_present"
    elif result["nodes"] or result["predicates"]:
        slice_state = "objects_without_edges"
    else:
        slice_state = "empty"

    if slice_state == "edges_present":
        next_action = "complete_if_relevant"
    elif read_mode in {"seeded", "focused_seeded"}:
        next_action = "stop_without_widening"
    elif slice_state == "objects_without_edges":
        next_action = "read_relevant_returned_seed_refs"
    elif read_mode == "focused":
        next_action = "one_outline"
    else:
        next_action = "stop_without_widening"

    read_state = {
        "read_mode": read_mode,
        "slice_state": slice_state,
        "next_action_if_relationship_required": next_action,
        "continuation_action": (
            "continue_only_for_explicit_full_inspection"
            if result["continuation"] is not None
            else "none"
        ),
    }
    assert read_state["read_mode"] in _READ_MODES
    assert read_state["slice_state"] in _SLICE_STATES
    assert (
        read_state["next_action_if_relationship_required"] in _RELATIONSHIP_NEXT_ACTIONS
    )
    assert read_state["continuation_action"] in _CONTINUATION_ACTIONS
    return {**result, "read_state": read_state}


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


def create_server(data_root: Path | None = None) -> MCPServer:
    service = HypesService(data_root)
    server = MCPServer("Hypes", version=__version__, instructions=SERVER_INSTRUCTIONS)

    @server.tool(
        name="hypes_read",
        title="Read User Relationship Model",
        description=(
            "Read the narrowest relationship slice needed by the decision order: an explicitly "
            "named stored input to a literal-only output with no separate reusable change or "
            "generalization; existing structure before an explicit reusable "
            "change; source edges before an explicit reusable generalization; a relation that could "
            "materially change a non-literal response or choice; or a user request to inspect the "
            "model. Do not read for an unrelated request. Use one to three short anchors likely to "
            "occur in a stored name, alias, or description, or start from known node or predicate "
            "refs. A read is complete when it returns the stored object needed by its branch. Applying, "
            "choosing by, correcting, replacing, deleting, or generalizing a relationship requires the "
            "relevant edge; direct inspection or literal use of one node or predicate may complete "
            "without an edge. When read_state is present and a relationship is required, follow "
            "next_action_if_relationship_required; complete_if_relevant completes only for a needed "
            "returned edge and otherwise stops without widening. When the branch needs a relationship "
            "and an outline returns candidates, take only the relevant node_id and predicate_id values "
            "and pass them as seed_refs in one "
            "follow-up read before answering or rewriting. When read_state is absent, use that same "
            "follow-up after exactly one small outline read. Stop without unrelated reads when no "
            "candidate or required edge is found. "
            "Follow continuation_action only when the user asks to inspect the whole model. Results "
            "are a revisable agent model, not user-approved guidance or source-linked facts."
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
            lambda: _read_result_with_state(
                service,
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
            "Use this only after the decision order selects an explicit reusable relation change or "
            "an explicit reusable generalization of several source edges that were actually read. "
            "For a correction, first confirm the existing slice. For a generalization, write only "
            "the minimal reusable higher-level structure, reusing a predicate when it fits and "
            "creating one only when needed; never copy the current task, event, subject, or source "
            "fact. A literal output's task-local terms, equivalences, facts, "
            "alternatives, or instructions must never be written, even when they conflict with the "
            "graph. Reading and applying an existing relation does not itself justify a rewrite. "
            "Apply one atomic patch of node, predicate, and edge puts or deletes. "
            "Give created or replaced nodes and predicates a few short, reusable retrieval aliases, "
            "never conversation details or project facts. A '$...' ref "
            "creates an object and may be used by later operations in the same patch; a persistent "
            "ref replaces "
            "that object. Delete incident edges in the same patch before deleting their nodes or "
            "predicates. Do not write merely because a conversation turn completed."
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


mcp = create_server()


def main() -> None:
    transport = os.environ.get("HYPES_MCP_TRANSPORT", "stdio")
    if transport == "stdio":
        mcp.run(transport="stdio")
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
    mcp.run(
        transport="streamable-http",
        host=host,
        port=int(os.environ.get("HYPES_MCP_PORT", "8000")),
        streamable_http_path=os.environ.get("HYPES_MCP_PATH", "/hypes/mcp"),
        stateless_http=True,
        json_response=True,
    )


if __name__ == "__main__":
    main()
