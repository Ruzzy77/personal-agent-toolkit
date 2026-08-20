"""Sense operations shared by the local CLI and MCP surface."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from . import BUILD_ID, __version__
from .errors import ConfirmationRequiredError, SectionNotFoundError
from .exposure import guidance_overview, profile_index, section_view
from .model import ProfileDocument, SectionChange
from .store import SenseStore

ReadView = Literal["index", "sections", "full"]


class SenseService:
    def __init__(self, data_root: Path | None = None, *, prepare: bool = True) -> None:
        self.store = SenseStore(data_root)
        if prepare:
            self.store.ensure_ready()

    def import_profile(
        self,
        profile: ProfileDocument,
        *,
        replace: bool = False,
        trusted_user_action: bool = False,
    ) -> dict[str, Any]:
        if replace and not trusted_user_action:
            raise ConfirmationRequiredError(
                "replacing the current Sense profile requires explicit local confirmation"
            )
        stored = self.store.initialize(profile, replace=replace)
        return self._summary(stored)

    @staticmethod
    def _summary(stored: Any) -> dict[str, Any]:
        return {
            "schema_version": stored.profile.schema_version,
            "profile_sha256": stored.digest,
            "section_count": len(stored.profile.sections),
            "updated_at": stored.updated_at,
        }

    def read(
        self,
        *,
        view: ReadView = "index",
        section_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        stored = self.store.read()
        if view == "index":
            return {"sections": profile_index(stored.profile)}
        if view == "full":
            result = self._summary(stored)
            result["profile"] = {
                "schema_version": stored.profile.schema_version,
                "sections": [
                    section_view(section, include_change_token=False)
                    for section in stored.profile.sections
                ],
            }
            return result
        if not section_ids:
            raise ValueError("section_ids are required when view=sections")
        if len(section_ids) > 12:
            raise ValueError("no more than 12 sections may be read at once")

        sections: list[dict[str, Any]] = []
        for section_id in dict.fromkeys(section_ids):
            section = next(
                (
                    candidate
                    for candidate in stored.profile.sections
                    if candidate.id == section_id
                ),
                None,
            )
            if section is None:
                raise SectionNotFoundError(
                    "Sense section was not found",
                    details={"section_id": section_id},
                )
            sections.append(section_view(section, include_change_token=True))
        return {"sections": sections}

    def revise(
        self,
        *,
        changes: list[SectionChange],
        trusted_user_action: bool = False,
    ) -> dict[str, Any]:
        return self.store.revise(
            changes=changes,
            user_confirmed=trusted_user_action,
        )

    def overview(self) -> dict[str, Any]:
        stored = self.store.read()
        return guidance_overview(
            stored.profile,
            updated_at=stored.updated_at,
        )

    def remove_section(
        self,
        *,
        section_id: str,
        previous_section_sha256: str,
        trusted_user_action: bool,
    ) -> dict[str, Any]:
        return self.store.remove_section(
            section_id=section_id,
            previous_section_sha256=previous_section_sha256,
            user_confirmed=trusted_user_action,
        )

    def remove_database(self, *, trusted_user_action: bool) -> dict[str, Any]:
        return self.store.remove_database(user_confirmed=trusted_user_action)

    def status(self) -> dict[str, Any]:
        stored = self.store.read()
        result = self._summary(stored)
        result.update(
            {
                "storage": self.store.security_status(),
                "server_version": __version__,
                "build_id": BUILD_ID,
            }
        )
        return result
