"""Validated public shapes for the Hypes ontology tools."""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    RootModel,
    model_validator,
)

TEMP_REF_PATTERN = r"^\$[a-z][a-z0-9._-]{0,63}$"
PERSISTENT_NODE_REF_PATTERN = r"^node_[0-9a-f]{32}$"
PERSISTENT_PREDICATE_REF_PATTERN = r"^pred_[0-9a-f]{32}$"
PERSISTENT_EDGE_REF_PATTERN = r"^edge_[0-9a-f]{32}$"
NODE_REF_PATTERN = r"^(?:\$[a-z][a-z0-9._-]{0,63}|node_[0-9a-f]{32})$"
PREDICATE_REF_PATTERN = r"^(?:\$[a-z][a-z0-9._-]{0,63}|pred_[0-9a-f]{32})$"
EDGE_REF_PATTERN = r"^(?:\$[a-z][a-z0-9._-]{0,63}|edge_[0-9a-f]{32})$"
PERSISTENT_REF_PATTERN = r"^(?:node|pred|edge)_[0-9a-f]{32}$"
_MAX_JSON_OBJECT_BYTES = 64 * 1024
_MAX_JSON_NESTING_DEPTH = 8


def _json_nesting_depth(value: JsonValue) -> int:
    if isinstance(value, dict):
        return 1 + max(
            (_json_nesting_depth(item) for item in value.values()), default=0
        )
    if isinstance(value, list):
        return 1 + max((_json_nesting_depth(item) for item in value), default=0)
    return 0


def _bounded_json_object(value: dict[str, JsonValue]) -> dict[str, JsonValue]:
    if _json_nesting_depth(value) > _MAX_JSON_NESTING_DEPTH:
        raise ValueError(
            f"JSON objects may be nested at most {_MAX_JSON_NESTING_DEPTH} levels"
        )
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if len(encoded) > _MAX_JSON_OBJECT_BYTES:
        raise ValueError(
            f"JSON objects may contain at most {_MAX_JSON_OBJECT_BYTES} serialized bytes"
        )
    return value


BoundedJsonObject = Annotated[
    dict[str, JsonValue],
    AfterValidator(_bounded_json_object),
]


TemporaryRef = Annotated[
    str,
    Field(
        pattern=TEMP_REF_PATTERN,
        description=(
            "Patch-local reference beginning with '$'; it may be used by later operations in "
            "the same rewrite."
        ),
    ),
]
PersistentNodeRef = Annotated[
    str,
    Field(
        pattern=PERSISTENT_NODE_REF_PATTERN,
        description="Persistent node reference in node_<uuidhex> form.",
    ),
]
PersistentPredicateRef = Annotated[
    str,
    Field(
        pattern=PERSISTENT_PREDICATE_REF_PATTERN,
        description="Persistent predicate reference in pred_<uuidhex> form.",
    ),
]
PersistentEdgeRef = Annotated[
    str,
    Field(
        pattern=PERSISTENT_EDGE_REF_PATTERN,
        description="Persistent edge reference in edge_<uuidhex> form.",
    ),
]
NodeRef = Annotated[
    str,
    Field(
        pattern=NODE_REF_PATTERN,
        description="A patch-local '$...' reference or a persistent node_<uuidhex> reference.",
    ),
]
PredicateRef = Annotated[
    str,
    Field(
        pattern=PREDICATE_REF_PATTERN,
        description="A patch-local '$...' reference or a persistent pred_<uuidhex> reference.",
    ),
]
EdgeRef = Annotated[
    str,
    Field(
        pattern=EDGE_REF_PATTERN,
        description="A patch-local '$...' reference or a persistent edge_<uuidhex> reference.",
    ),
]
PersistentRef = Annotated[
    str,
    Field(
        pattern=PERSISTENT_REF_PATTERN,
        description="A persistent node, predicate, or edge reference; temporary refs are invalid.",
    ),
]


class _OntologyInput(BaseModel):
    """Common validation for JSON inputs accepted by the ontology tools."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
        allow_inf_nan=False,
        validate_default=True,
    )


class NodeInput(_OntologyInput):
    """The complete caller-supplied value of a node."""

    labels: list[Annotated[str, Field(min_length=1, max_length=120)]] = Field(
        default_factory=list,
        max_length=32,
        description="Agent-created labels; Hypes does not impose a label vocabulary.",
    )
    name: str = Field(
        min_length=1,
        max_length=240,
        description="Compact name by which the agent recalls this concept or object.",
    )
    description: str | None = Field(
        default=None,
        min_length=1,
        max_length=2000,
        description="Optional current interpretation of this node.",
    )
    aliases: list[Annotated[str, Field(min_length=1, max_length=240)]] = Field(
        default_factory=list,
        max_length=64,
        description="Alternative terms used to find the same node.",
    )
    attributes: BoundedJsonObject = Field(
        default_factory=dict,
        description=(
            "Optional agent-created attributes containing finite JSON values, at most 64 KiB "
            "when serialized and eight container levels deep."
        ),
    )


class PredicateInput(_OntologyInput):
    """The complete caller-supplied value of a predicate."""

    name: str = Field(
        min_length=1,
        max_length=240,
        description="Agent-created name for a relationship; no fixed predicate enum is used.",
    )
    description: str | None = Field(
        default=None,
        min_length=1,
        max_length=2000,
        description="Optional current meaning of this predicate.",
    )
    aliases: list[Annotated[str, Field(min_length=1, max_length=240)]] = Field(
        default_factory=list,
        max_length=64,
        description="Alternative relationship terms used to find this predicate.",
    )


class EdgeInput(_OntologyInput):
    """The complete caller-supplied value of an edge between two nodes."""

    source_ref: NodeRef = Field(
        description="Source node ref, persistent or local to this patch."
    )
    predicate_ref: PredicateRef = Field(
        description="Predicate ref, persistent or local to this patch."
    )
    target_ref: NodeRef = Field(
        description="Target node ref, persistent or local to this patch."
    )
    qualifiers: BoundedJsonObject = Field(
        default_factory=dict,
        description=(
            "Optional agent-created qualifiers containing finite JSON values, at most 64 KiB "
            "when serialized and eight container levels deep."
        ),
    )


class PutNodeOperation(_OntologyInput):
    """Create a node through a temporary ref or replace one through its persistent ref."""

    op: Literal["put_node"]
    ref: NodeRef
    value: NodeInput


class PutPredicateOperation(_OntologyInput):
    """Create a predicate through a temporary ref or replace a persistent predicate."""

    op: Literal["put_predicate"]
    ref: PredicateRef
    value: PredicateInput


class PutEdgeOperation(_OntologyInput):
    """Create an edge through a temporary ref or replace a persistent edge."""

    op: Literal["put_edge"]
    ref: EdgeRef
    value: EdgeInput


class DeleteOperation(_OntologyInput):
    """Delete one persistent node, predicate, or edge."""

    op: Literal["delete"]
    ref: PersistentRef


RewriteOperation = Annotated[
    PutNodeOperation | PutPredicateOperation | PutEdgeOperation | DeleteOperation,
    Field(discriminator="op"),
]


class ToolError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ToolSuccess(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: Literal[True] = True
    result: dict[str, Any] | list[Any]


class ToolFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: Literal[False] = False
    error: ToolError


class ToolResponse(
    RootModel[
        Annotated[
            ToolSuccess | ToolFailure,
            Field(discriminator="ok"),
        ]
    ]
):
    # Claude's MCP client requires the common top-level object type to be explicit.
    model_config = ConfigDict(json_schema_extra={"type": "object"})

    @model_validator(mode="before")
    @classmethod
    def require_response_object(cls, value: object) -> object:
        if isinstance(value, (ToolSuccess, ToolFailure)):
            return value
        if not isinstance(value, dict):
            raise TypeError("tool response must be an object")
        return value
