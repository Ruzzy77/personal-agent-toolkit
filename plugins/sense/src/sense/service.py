"""Sense operations shared by CLI and MCP surfaces."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from . import BUILD_ID, __version__
from .errors import ConfirmationRequiredError, SectionNotFoundError
from .exposure import local_profile_index, section_view, work_profile_overview
from .model import ProfileDocument, ProfileSection
from .store import SenseStore

ReadView = Literal["index", "sections", "full"]
ControlAction = Literal[
    "inspect",
    "export",
    "preview_forget",
    "forget",
    "activate",
    "preview_remove_database",
    "remove_database",
]


class SenseService:
    def __init__(self, data_root: Path | None = None) -> None:
        self.store = SenseStore(data_root)

    def import_profile(
        self,
        profile: ProfileDocument,
        *,
        replace_preview: bool = False,
        expected_preview_revision: int | None = None,
        expected_preview_digest: str | None = None,
    ) -> dict[str, Any]:
        stored = self.store.initialize(
            profile,
            lifecycle="preview",
            replace_preview=replace_preview,
            expected_preview_revision=expected_preview_revision,
            expected_preview_digest=expected_preview_digest,
        )
        return self._summary(stored)

    @staticmethod
    def _summary(stored: Any) -> dict[str, Any]:
        return {
            "lifecycle": stored.lifecycle,
            "schema_version": stored.profile.schema_version,
            "revision": stored.profile.revision,
            "profile_sha256": stored.digest,
            "section_count": len(stored.profile.sections),
            "updated_at": stored.updated_at,
        }

    def read(
        self,
        *,
        view: ReadView = "index",
        section_ids: list[str] | None = None,
        include_sources: bool = False,
    ) -> dict[str, Any]:
        stored = self.store.read()
        result = self._summary(stored)
        if view == "index":
            result["sections"] = local_profile_index(stored.profile)
            result["controls"] = stored.profile.controls.model_dump(mode="json")
            return result
        if view == "full":
            result["profile"] = stored.profile.model_dump(mode="json")
            if not include_sources:
                for section in result["profile"]["sections"]:
                    section.pop("source_refs", None)
            return result
        if not section_ids:
            raise ValueError("section_ids are required when view=sections")
        if len(section_ids) > 12:
            raise ValueError("no more than 12 sections may be read at once")
        requested = list(dict.fromkeys(section_ids))
        sections: list[dict[str, Any]] = []
        for section_id in requested:
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
                    "Sense profile section was not found",
                    details={"section_id": section_id},
                )
            sections.append(
                section_view(
                    section,
                    include_sources=include_sources,
                    include_change_token=True,
                )
            )
        result["sections"] = sections
        return result

    def revise(
        self,
        *,
        expected_revision: int,
        section_id: str,
        previous_section_sha256: str,
        previous_understanding: str,
        changed_future_judgment: str,
        new_section: ProfileSection,
        trusted_user_action: bool = False,
    ) -> dict[str, Any]:
        if not previous_understanding.strip():
            raise ValueError("previous_understanding must explain the replaced view")
        if not changed_future_judgment.strip():
            raise ValueError("changed_future_judgment must state what will differ next time")
        stored = self.store.revise(
            expected_revision=expected_revision,
            section_id=section_id,
            previous_section_sha256=previous_section_sha256,
            new_section=new_section,
            user_confirmed=trusted_user_action,
        )
        return self._summary(stored)

    def control(
        self,
        *,
        action: ControlAction,
        section_id: str | None = None,
        replacement_section: ProfileSection | None = None,
        expected_revision: int | None = None,
        confirmation_digest: str | None = None,
        confirm_profile_digest: str | None = None,
        trusted_user_action: bool = False,
    ) -> dict[str, Any]:
        if action in {"inspect", "export"}:
            result = self.read(view="full", include_sources=True)
            result["format"] = "sense-profile-v1"
            return result
        if action == "preview_forget":
            if section_id is None:
                raise ValueError("section_id is required")
            return self.store.preview_forget(
                section_id=section_id,
                replacement_section=replacement_section,
            )
        if action == "forget":
            if section_id is None or expected_revision is None:
                raise ValueError("section_id and expected_revision are required")
            if confirmation_digest is None or not trusted_user_action:
                raise ConfirmationRequiredError(
                    "forget requires user confirmation and the digest returned by preview_forget"
                )
            stored = self.store.forget(
                expected_revision=expected_revision,
                section_id=section_id,
                confirmation_digest=confirmation_digest,
                replacement_section=replacement_section,
                user_confirmed=trusted_user_action,
            )
            return self._summary(stored)
        if action == "activate":
            if (
                expected_revision is None
                or confirm_profile_digest is None
                or not trusted_user_action
            ):
                raise ConfirmationRequiredError(
                    "activation requires user confirmation, expected_revision, "
                    "and the reviewed profile digest"
                )
            stored = self.store.activate(
                expected_revision=expected_revision,
                confirm_profile_digest=confirm_profile_digest,
            )
            return self._summary(stored)
        if action == "preview_remove_database":
            return self.store.removal_preview()
        if action == "remove_database":
            if confirmation_digest is None or not trusted_user_action:
                raise ConfirmationRequiredError(
                    "database removal requires user confirmation and the digest "
                    "returned by preview_remove_database"
                )
            return self.store.remove_database(
                confirmation_digest=confirmation_digest
            )
        raise ValueError(f"unsupported Sense control action: {action}")

    def status(self) -> dict[str, Any]:
        stored = self.store.read()
        result = self._summary(stored)
        result.update(
            {
                "retained_previous_revisions": self.store.history_count(),
                "storage": self.store.security_status(),
                "server_version": __version__,
                "build_id": BUILD_ID,
            }
        )
        return result

    def overview(self) -> dict[str, Any]:
        stored = self.store.read()
        return work_profile_overview(
            stored.profile,
            lifecycle=stored.lifecycle,
            updated_at=stored.updated_at,
        )
