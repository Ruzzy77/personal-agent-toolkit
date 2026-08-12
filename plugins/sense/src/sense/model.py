"""Validated Sense profile models and stable serialization."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator, model_validator

SCHEMA_VERSION = 1
MAX_SECTIONS = 24
MAX_SECTION_TEXT_CHARS = 12_000
MAX_PROFILE_BYTES = 256 * 1024
SECTION_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

Origin = Literal["user_set", "learned_from_work"]
Sensitivity = Literal["ordinary", "sensitive"]
SourceKind = Literal["conversation", "file", "corpus", "result"]
Lifecycle = Literal["preview", "active"]


class SourceRef(BaseModel):
    """Minimal locator for finding the original basis again."""

    model_config = ConfigDict(extra="forbid")

    kind: SourceKind
    locator: str = Field(min_length=1, max_length=512)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    origin: Origin

    @field_validator("locator")
    @classmethod
    def reject_embedded_content(cls, value: str) -> str:
        if "\x00" in value or "\n" in value or "\r" in value:
            raise ValueError("source locator must be one bounded identifier")
        return value

    @model_validator(mode="after")
    def require_typed_locator(self) -> "SourceRef":
        locator = self.locator
        if self.kind == "file":
            if re.fullmatch(r"git:[0-9a-f]{40}:[^\x00\r\n]+", locator):
                relative = locator.split(":", 2)[2]
                path = PurePosixPath(relative)
                if not path.is_absolute() and ".." not in path.parts:
                    return self
            path = PurePosixPath(locator)
            if path.is_absolute() and path != PurePosixPath("/") and ".." not in path.parts:
                return self
            raise ValueError(
                "file locator must be an absolute path or a fixed Git revision"
            )
        schemes = {
            "conversation": (
                "thread://",
                "chatgpt-conversation://",
                "codex-session://",
                "claude-session://",
            ),
            "corpus": ("corpus://",),
            "result": ("result://",),
        }
        if not locator.startswith(schemes[self.kind]) or re.search(r"\s", locator):
            raise ValueError(f"{self.kind} locator must use its bounded locator scheme")
        return self


class ProfileSection(BaseModel):
    """One replaceable section of Sense guidance."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    purpose: str = Field(min_length=1, max_length=320)
    text: str = Field(min_length=1, max_length=MAX_SECTION_TEXT_CHARS)
    origins: list[Origin] = Field(min_length=1, max_length=2)
    use_for: list[str] = Field(default_factory=list, max_length=16)
    review_when: list[str] = Field(default_factory=list, max_length=12)
    sensitivity: Sensitivity = "ordinary"
    source_refs: list[SourceRef] = Field(default_factory=list, max_length=12)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if SECTION_ID_RE.fullmatch(value) is None:
            raise ValueError("section id must use lowercase hyphen-case")
        return value

    @field_validator("origins")
    @classmethod
    def unique_origins(cls, value: list[Origin]) -> list[Origin]:
        return list(dict.fromkeys(value))

    @field_validator("use_for", "review_when")
    @classmethod
    def validate_short_list(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for item in value:
            item = item.strip()
            if not item or len(item) > 240 or "\x00" in item:
                raise ValueError("profile labels must be short non-empty strings")
            if item not in normalized:
                normalized.append(item)
        return normalized


class ProfileControls(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_conversation_storage: Literal["never"] = "never"
    sensitive_persistence: Literal["explicit_confirmation"] = "explicit_confirmation"
    external_effects: Literal["responsibility_based"] = "responsibility_based"
    provider_memory_management: Literal["provider_owned"] = "provider_owned"


class ProfileDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = SCHEMA_VERSION
    revision: int = Field(ge=1)
    sections: list[ProfileSection] = Field(min_length=1, max_length=MAX_SECTIONS)
    controls: ProfileControls = Field(default_factory=ProfileControls)

    @model_validator(mode="after")
    def unique_sections_and_bounded_profile(self) -> "ProfileDocument":
        ids = [section.id for section in self.sections]
        if len(ids) != len(set(ids)):
            raise ValueError("profile section ids must be unique")
        if len(canonical_json_bytes(self)) > MAX_PROFILE_BYTES:
            raise ValueError("profile exceeds the private store size limit")
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
    # Claude's MCP client requires the common top-level object type to be explicit.
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
