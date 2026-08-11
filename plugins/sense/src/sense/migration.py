"""Canonical, content-complete bundle for trusted Sense profile migration."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .model import Lifecycle, ProfileDocument, canonical_json_bytes, content_sha256

MIGRATION_FORMAT = "sense-profile-migration"
MIGRATION_BUNDLE_SCHEMA_VERSION = 1
IDEMPOTENCY_KEY_RE = re.compile(r"^[a-zA-Z0-9._:-]{1,160}$")


class SenseMigrationBundle(BaseModel):
    """One normalized current-profile snapshot; never a raw store backup."""

    model_config = ConfigDict(extra="forbid")

    format: Literal["sense-profile-migration"] = MIGRATION_FORMAT
    bundle_schema_version: Literal[1] = MIGRATION_BUNDLE_SCHEMA_VERSION
    lifecycle: Lifecycle
    profile: ProfileDocument
    profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    def digest_payload(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "bundle_schema_version": self.bundle_schema_version,
            "lifecycle": self.lifecycle,
            "profile": self.profile.model_dump(mode="json"),
            "profile_sha256": self.profile_sha256,
        }

    @model_validator(mode="after")
    def validate_active_profile_and_digests(self) -> SenseMigrationBundle:
        if self.lifecycle != "active":
            raise ValueError("Sense migration accepts only an active profile")
        if content_sha256(self.profile) != self.profile_sha256:
            raise ValueError(
                "Sense migration profile digest does not match its payload"
            )
        if content_sha256(self.digest_payload()) != self.bundle_sha256:
            raise ValueError("Sense migration bundle digest does not match its payload")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))

    @classmethod
    def from_profile(
        cls,
        *,
        lifecycle: Lifecycle,
        profile: ProfileDocument,
    ) -> SenseMigrationBundle:
        profile_sha256 = content_sha256(profile)
        payload = {
            "format": MIGRATION_FORMAT,
            "bundle_schema_version": MIGRATION_BUNDLE_SCHEMA_VERSION,
            "lifecycle": lifecycle,
            "profile": profile.model_dump(mode="json"),
            "profile_sha256": profile_sha256,
        }
        return cls.model_validate(
            {
                **payload,
                "bundle_sha256": content_sha256(payload),
            }
        )


def validate_idempotency_key(value: str) -> str:
    if IDEMPOTENCY_KEY_RE.fullmatch(value) is None:
        raise ValueError(
            "idempotency_key must contain 1-160 letters, numbers, '.', '_', ':', or '-'"
        )
    return value


__all__ = [
    "MIGRATION_BUNDLE_SCHEMA_VERSION",
    "MIGRATION_FORMAT",
    "SenseMigrationBundle",
    "validate_idempotency_key",
]
