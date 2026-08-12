"""Validated shapes for Hypes explanation clues."""

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
    "explicit_user_correction",
    "demonstrated_application",
    "confirmed_explanation_outcome",
    "repeated_across_conversations",
]
RecheckBasis = Literal[
    "explicit_user_correction",
    "incompatible_application_outcome",
    "current_conversation_conflict",
]


class StoredScope(BaseModel):
    """The stored scope used by existing Hypes databases."""

    model_config = ConfigDict(extra="forbid")

    topic: str = Field(
        min_length=1,
        max_length=160,
        description="Exact concept or relation domain where this clue applies.",
    )
    task: str | None = Field(
        default=None,
        min_length=1,
        max_length=160,
        description="Optional exact situation scope; null is broader by design, not a wildcard.",
    )
    responsibility: str | None = Field(
        default=None,
        min_length=1,
        max_length=160,
        description=(
            "Optional exact responsibility scope; null is an explicit broader scope, not a wildcard."
        ),
    )


class RelationDraft(BaseModel):
    """A compact relationship, never a transcript or a user trait."""

    model_config = ConfigDict(extra="forbid")

    relation_id: str = Field(
        pattern=r"^[a-z0-9](?:[a-z0-9._-]{0,127})$",
        description="Stable caller-chosen id for this one scoped relation.",
    )
    scope: StoredScope = Field(
        description="Narrow topic, situation, and responsibility where the clue may affect an answer."
    )
    kind: RelationKind = Field(
        description=(
            "Whether the clue records an understood relation, unclear relation, helpful "
            "explanation, or unhelpful explanation."
        )
    )
    statement: str = Field(
        min_length=1,
        max_length=1000,
        description="Compact concept relationship or explanation clue, never transcript text.",
    )
    explanation_pattern: str | None = Field(
        default=None,
        min_length=1,
        max_length=500,
        description=(
            "Required only for helpful or unhelpful explanation relations; compactly describe "
            "the explanation approach whose effect the user confirmed."
        ),
    )

    @model_validator(mode="after")
    def require_kind_specific_shape(self) -> RelationDraft:
        explanation_kinds = {"helpful_explanation", "unhelpful_explanation"}
        if self.kind in explanation_kinds and self.explanation_pattern is None:
            raise ValueError(
                "helpful or unhelpful explanation relations require explanation_pattern"
            )
        if self.kind not in explanation_kinds and self.explanation_pattern is not None:
            raise ValueError(
                "understood or unclear concept relations cannot contain explanation_pattern"
            )
        return self


class ExplanationScope(BaseModel):
    """The situation where an explanation clue may be useful."""

    model_config = ConfigDict(extra="forbid")

    topic: str = Field(
        min_length=1,
        max_length=160,
        description="Exact concept or relation domain where this clue applies.",
    )
    situation: str | None = Field(
        default=None,
        min_length=1,
        max_length=160,
        description="Optional exact situation; null deliberately means a broader scope.",
    )
    responsibility: str | None = Field(
        default=None,
        min_length=1,
        max_length=160,
        description="Optional exact responsibility; null deliberately means a broader scope.",
    )

    def to_stored(self) -> StoredScope:
        return StoredScope(
            topic=self.topic,
            task=self.situation,
            responsibility=self.responsibility,
        )


class ExplanationClueInput(BaseModel):
    """One compact clue accepted by the public Hypes tools."""

    model_config = ConfigDict(extra="forbid")

    relation_id: str = Field(
        pattern=r"^[a-z0-9](?:[a-z0-9._-]{0,127})$",
        description="Stable caller-chosen id for this one narrowly scoped clue.",
    )
    scope: ExplanationScope = Field(
        description="Narrow topic, situation, and responsibility where the clue may matter."
    )
    kind: RelationKind = Field(
        description=(
            "Whether the clue records an understood relation, unclear relation, helpful "
            "explanation, or unhelpful explanation."
        )
    )
    statement: str = Field(
        min_length=1,
        max_length=1000,
        description="Compact concept relationship or explanation clue, never transcript text.",
    )
    explanation_pattern: str | None = Field(
        default=None,
        min_length=1,
        max_length=500,
        description=(
            "Required only for helpful or unhelpful explanation clues; describe the approach "
            "whose effect the user confirmed."
        ),
    )

    @model_validator(mode="after")
    def require_kind_specific_shape(self) -> ExplanationClueInput:
        explanation_kinds = {"helpful_explanation", "unhelpful_explanation"}
        if self.kind in explanation_kinds and self.explanation_pattern is None:
            raise ValueError(
                "helpful or unhelpful explanation clues require explanation_pattern"
            )
        if self.kind not in explanation_kinds and self.explanation_pattern is not None:
            raise ValueError(
                "understood or unclear concept clues cannot contain explanation_pattern"
            )
        return self

    def to_relation_draft(self) -> RelationDraft:
        return RelationDraft(
            relation_id=self.relation_id,
            scope=self.scope.to_stored(),
            kind=self.kind,
            statement=self.statement,
            explanation_pattern=self.explanation_pattern,
        )


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
