"""Public data shapes for the Hypes cognitive model."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator

RelationKind = Literal[
    "understood_relation",
    "unclear_relation",
    "helpful_explanation",
    "unhelpful_explanation",
]
RelationStatus = Literal["active", "recheck_due"]
RetentionBasis = Literal[
    "explicit_user_request",
    "conversation_conclusion",
]
RecheckBasis = Literal[
    "explicit_user_correction",
    "incompatible_application_outcome",
    "current_conversation_conflict",
]


class CognitiveScope(BaseModel):
    """The narrow place where an understanding is useful."""

    model_config = ConfigDict(extra="forbid")

    topic: str = Field(min_length=1, max_length=160)
    task: str | None = Field(default=None, min_length=1, max_length=160)
    responsibility: str | None = Field(default=None, min_length=1, max_length=160)


class RelationDraft(BaseModel):
    """A compact relationship, never a transcript or a user trait."""

    model_config = ConfigDict(extra="forbid")

    relation_id: str = Field(pattern=r"^[a-z0-9](?:[a-z0-9._-]{0,127})$")
    scope: CognitiveScope
    kind: RelationKind
    statement: str = Field(min_length=1, max_length=1000)
    explanation_pattern: str | None = Field(default=None, min_length=1, max_length=500)


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
