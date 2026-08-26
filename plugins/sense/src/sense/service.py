"""Sense operations shared by the local CLI and MCP surface."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from . import BUILD_ID, __version__
from .errors import ConfirmationRequiredError, SectionNotFoundError
from .exposure import guidance_overview, profile_index, section_view
from .model import ProfileDocument, SectionChange
from .section_skills import SectionSkillService
from .store import SenseStore

ReadView = Literal["index", "sections", "full"]


class SenseService:
    def __init__(self, data_root: Path | None = None, *, prepare: bool = True) -> None:
        self.store = SenseStore(data_root)
        if prepare:
            self.store.ensure_ready()
        self.section_skills = SectionSkillService(
            self.store.data_root,
            store=self.store,
        )

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
        audience: str = "local_cli",
    ) -> dict[str, Any]:
        if audience not in {"local_cli", "external_mcp"}:
            raise ValueError("unsupported Sense audience")
        stored = self.store.read()
        if view == "index":
            sections = profile_index(stored.profile)
            ordinary_ids = {
                section.id
                for section in stored.profile.sections
                if section.sensitivity == "ordinary"
            }
            for item in sections:
                section_id = item["id"]
                if section_id not in ordinary_ids:
                    continue
                skill = self.section_skills.read(
                    section_id=section_id,
                    audience=audience,
                    include_instructions=False,
                    require_section=False,
                )
                if skill is not None:
                    item["skill"] = skill
            return {"sections": sections}
        if view == "full":
            result = self._summary(stored)
            result["profile"] = {
                "schema_version": stored.profile.schema_version,
                "sections": [
                    self._section_view_with_skill(
                        section,
                        include_change_token=False,
                        audience=audience,
                    )
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
            sections.append(
                self._section_view_with_skill(
                    section,
                    include_change_token=True,
                    audience=audience,
                )
            )
        return {"sections": sections}

    def _section_view_with_skill(
        self,
        section: Any,
        *,
        include_change_token: bool,
        audience: str,
    ) -> dict[str, Any]:
        result = section_view(
            section,
            include_change_token=include_change_token,
        )
        skill = self.section_skills.read(
            section_id=section.id,
            audience=audience,
            include_instructions=True,
            require_section=False,
        )
        if skill is not None:
            result["skill"] = skill
        return result

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
        section_skills = {
            section.id: skill
            for section in stored.profile.sections
            if section.sensitivity == "ordinary"
            and (
                skill := self.section_skills.read(
                    section_id=section.id,
                    audience="external_mcp",
                    include_instructions=True,
                    require_section=False,
                )
            )
            is not None
        }
        return guidance_overview(
            stored.profile,
            updated_at=stored.updated_at,
            section_skills=section_skills,
        )

    def section_skill_read(
        self,
        *,
        section_id: str,
        audience: str = "local_cli",
    ) -> dict[str, Any]:
        return {
            "section_id": section_id,
            "skill": self.section_skills.read(
                section_id=section_id,
                audience=audience,
            ),
        }

    def section_skill_set(
        self,
        *,
        section_id: str,
        skill_file: Path,
        expected_version: str,
        confirm_section_skill_write: bool,
    ) -> dict[str, Any]:
        return self.section_skills.set(
            section_id=section_id,
            skill_file=skill_file,
            expected_version=expected_version,
            confirm_section_skill_write=confirm_section_skill_write,
        )

    def section_skill_remove(
        self,
        *,
        section_id: str,
        expected_version: str,
        confirm_section_skill_remove: bool,
    ) -> dict[str, Any]:
        return self.section_skills.remove(
            section_id=section_id,
            expected_version=expected_version,
            confirm_section_skill_remove=confirm_section_skill_remove,
        )

    def remove_section(
        self,
        *,
        section_id: str,
        previous_section_sha256: str,
        trusted_user_action: bool,
    ) -> dict[str, Any]:
        result = self.store.remove_section(
            section_id=section_id,
            previous_section_sha256=previous_section_sha256,
            user_confirmed=trusted_user_action,
        )
        result["removed_skill_storage"] = self.section_skills.purge(
            section_id=section_id
        )
        return result

    def remove_database(self, *, trusted_user_action: bool) -> dict[str, Any]:
        result = self.store.remove_database(user_confirmed=trusted_user_action)
        result["removed"].extend(self.section_skills.purge_all())
        return result

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
