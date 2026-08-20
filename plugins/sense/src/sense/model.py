"""Validated Sense profile models and stable serialization."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    field_validator,
    model_validator,
)

SCHEMA_VERSION = 2
MAX_SECTIONS = 24
MAX_CHANGES = 12
MAX_SECTION_TEXT_CHARS = 12_000
MAX_PROFILE_BYTES = 256 * 1024
SECTION_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

Origin = Literal["user_set", "learned_from_results"]
Sensitivity = Literal["ordinary", "sensitive"]


class ProfileSection(BaseModel):
    """One independently replaceable item of durable guidance."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    purpose: str = Field(min_length=1, max_length=320)
    text: str = Field(min_length=1, max_length=MAX_SECTION_TEXT_CHARS)
    origins: list[Origin] = Field(min_length=1, max_length=2)
    sensitivity: Sensitivity = "ordinary"

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if SECTION_ID_RE.fullmatch(value) is None:
            raise ValueError("section id must use lowercase hyphen-case")
        return value

    @field_validator("purpose", "text")
    @classmethod
    def reject_nul(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("Sense text must not contain NUL characters")
        return value

    @field_validator("origins")
    @classmethod
    def unique_origins(cls, value: list[Origin]) -> list[Origin]:
        return list(dict.fromkeys(value))


class ProfileDocument(BaseModel):
    """The single current Sense profile."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2] = SCHEMA_VERSION
    sections: list[ProfileSection] = Field(min_length=1, max_length=MAX_SECTIONS)

    @model_validator(mode="after")
    def unique_sections_and_bounded_profile(self) -> ProfileDocument:
        ids = [section.id for section in self.sections]
        if len(ids) != len(set(ids)):
            raise ValueError("profile section ids must be unique")
        if len(canonical_json_bytes(self)) > MAX_PROFILE_BYTES:
            raise ValueError("profile exceeds the private store size limit")
        return self


class SectionChange(BaseModel):
    """One complete section replacement in an atomic Sense update."""

    model_config = ConfigDict(extra="forbid")

    section_id: str = Field(min_length=1, max_length=64)
    previous_section_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    new_section: ProfileSection

    @field_validator("section_id")
    @classmethod
    def validate_section_id(cls, value: str) -> str:
        if SECTION_ID_RE.fullmatch(value) is None:
            raise ValueError("section id must use lowercase hyphen-case")
        return value

    @model_validator(mode="after")
    def replacement_id_matches_target(self) -> SectionChange:
        if self.new_section.id != self.section_id:
            raise ValueError("replacement section id must match section_id")
        return self


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
    model_config = ConfigDict(json_schema_extra={"type": "object"})


def canonical_json_bytes(value: BaseModel | dict[str, Any] | list[Any]) -> bytes:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def content_sha256(value: BaseModel | dict[str, Any] | list[Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def section_sha256(section: ProfileSection) -> str:
    return content_sha256(section)
