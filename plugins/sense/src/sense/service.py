"""Sense operations shared by CLI and MCP surfaces."""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any, Literal

from . import BUILD_ID, __version__
from .errors import ConfirmationRequiredError, MigrationStateError, SectionNotFoundError
from .exposure import (
    guidance_overview,
    local_profile_index,
    section_view,
    stored_section_id,
)
from .migration import SenseMigrationBundle, validate_idempotency_key
from .model import ProfileDocument, ProfileSection, section_sha256
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

    def export_migration_bundle(self) -> dict[str, Any]:
        """Export the complete current profile for a trusted control surface."""

        stored = self.store.read()
        if stored.lifecycle != "active":
            raise MigrationStateError(
                "Sense migration export requires an active profile"
            )
        bundle = SenseMigrationBundle.from_profile(
            lifecycle=stored.lifecycle,
            profile=stored.profile,
        )
        return bundle.model_dump(mode="json")

    def import_migration_bundle(
        self,
        bundle_payload: dict[str, Any],
        *,
        expected_empty: bool,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Import once into this service's already selected tenant namespace."""

        if expected_empty is not True:
            raise ValueError("Sense migration import requires expected_empty=true")
        validate_idempotency_key(idempotency_key)
        bundle = SenseMigrationBundle.model_validate(bundle_payload)
        return self.store.import_migration_profile(
            profile=bundle.profile,
            lifecycle=bundle.lifecycle,
            profile_sha256=bundle.profile_sha256,
            bundle_sha256=bundle.bundle_sha256,
            expected_empty=True,
            idempotency_key=idempotency_key,
        )

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
        if view == "index":
            return {
                "revision": stored.profile.revision,
                "sections": local_profile_index(stored.profile),
            }
        if view == "full":
            result = self._summary(stored)
            result["profile"] = {
                "schema_version": stored.profile.schema_version,
                "revision": stored.profile.revision,
                "sections": [
                    section_view(
                        section,
                        include_sources=include_sources,
                        include_change_token=False,
                    )
                    for section in stored.profile.sections
                ],
                "controls": stored.profile.controls.model_dump(mode="json"),
            }
            return result
        if not section_ids:
            raise ValueError("section_ids are required when view=sections")
        if len(section_ids) > 12:
            raise ValueError("no more than 12 sections may be read at once")
        requested = list(dict.fromkeys(section_ids))
        sections: list[dict[str, Any]] = []
        for public_id in requested:
            section_id = stored_section_id(public_id)
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
                    details={"section_id": public_id},
                )
            sections.append(
                section_view(
                    section,
                    include_sources=include_sources,
                    include_change_token=True,
                )
            )
        return {
            "revision": stored.profile.revision,
            "sections": sections,
        }

    @staticmethod
    def _changed_section_ids(
        before: ProfileDocument,
        after: ProfileDocument,
    ) -> list[str]:
        before_sections = {section.id: section for section in before.sections}
        after_sections = {section.id: section for section in after.sections}
        return sorted(
            section_id
            for section_id in set(before_sections) | set(after_sections)
            if before_sections.get(section_id) != after_sections.get(section_id)
        )

    @staticmethod
    def _profile_diff(
        before: ProfileDocument,
        after: ProfileDocument,
    ) -> dict[str, Any]:
        before_sections = {section.id: section for section in before.sections}
        after_sections = {section.id: section for section in after.sections}
        before_ids = set(before_sections)
        after_ids = set(after_sections)
        changed_sections: list[dict[str, Any]] = []
        for section_id in sorted(before_ids & after_ids):
            previous = before_sections[section_id]
            current = after_sections[section_id]
            if previous == current:
                continue
            previous_payload = previous.model_dump(mode="json")
            current_payload = current.model_dump(mode="json")
            fields_changed = sorted(
                field
                for field in set(previous_payload) | set(current_payload)
                if field != "id"
                and previous_payload.get(field) != current_payload.get(field)
            )
            text_diff = list(
                difflib.unified_diff(
                    previous.text.splitlines(),
                    current.text.splitlines(),
                    fromfile=f"revision-{before.revision}/{section_id}",
                    tofile=f"revision-{after.revision}/{section_id}",
                    lineterm="",
                )
            )
            changed_sections.append(
                {
                    "section_id": section_id,
                    "fields_changed": fields_changed,
                    "before_sha256": section_sha256(previous),
                    "after_sha256": section_sha256(current),
                    "text_diff": text_diff,
                }
            )
        return {
            "added_section_ids": sorted(after_ids - before_ids),
            "removed_section_ids": sorted(before_ids - after_ids),
            "changed_sections": changed_sections,
        }

    def history(
        self,
        *,
        from_revision: int | None = None,
        to_revision: int | None = None,
    ) -> dict[str, Any]:
        revisions = self.store.revision_history()
        by_revision = {
            revision.profile.revision: revision
            for revision in revisions
        }
        if (from_revision is None) != (to_revision is None):
            raise ValueError(
                "from_revision and to_revision must be provided together"
            )
        if from_revision is not None and to_revision is not None:
            if from_revision >= to_revision:
                raise ValueError("from_revision must be lower than to_revision")
            missing = [
                revision
                for revision in (from_revision, to_revision)
                if revision not in by_revision
            ]
            if missing:
                raise ValueError(
                    "requested revision is not retained: "
                    + ", ".join(str(revision) for revision in missing)
                )
            previous = by_revision[from_revision]
            current = by_revision[to_revision]
            return {
                "from_revision": {
                    "revision": from_revision,
                    "profile_sha256": previous.digest,
                    "created_at": previous.created_at,
                },
                "to_revision": {
                    "revision": to_revision,
                    "profile_sha256": current.digest,
                    "created_at": current.created_at,
                    "current": current.current,
                },
                "diff": self._profile_diff(previous.profile, current.profile),
            }

        summaries = []
        for revision in revisions:
            previous = by_revision.get(revision.profile.revision - 1)
            summaries.append(
                {
                    "revision": revision.profile.revision,
                    "profile_sha256": revision.digest,
                    "created_at": revision.created_at,
                    "current": revision.current,
                    "changed_section_ids": (
                        self._changed_section_ids(
                            previous.profile,
                            revision.profile,
                        )
                        if previous is not None
                        else None
                    ),
                }
            )
        return {
            "current_revision": revisions[0].profile.revision,
            "retained_previous_revisions": len(revisions) - 1,
            "revisions": summaries,
        }

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
        section_id = stored_section_id(section_id)
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

    def remote_update(
        self,
        *,
        expected_revision: int,
        idempotency_key: str,
        principal_binding: str,
        section_id: str,
        previous_understanding: str,
        changed_future_judgment: str,
        public_fields: dict[str, Any],
    ) -> dict[str, Any]:
        section_id = stored_section_id(section_id)
        return self.store.remote_revise_public(
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            principal_binding=principal_binding,
            section_id=section_id,
            previous_understanding=previous_understanding,
            changed_future_judgment=changed_future_judgment,
            public_fields=public_fields,
        )

    def remote_delete_preview(
        self,
        *,
        section_id: str,
        principal_binding: str,
    ) -> dict[str, Any]:
        section_id = stored_section_id(section_id)
        return self.store.remote_delete_preview(
            section_id=section_id,
            principal_binding=principal_binding,
        )

    def remote_delete(
        self,
        *,
        expected_revision: int,
        idempotency_key: str,
        principal_binding: str,
        delete_ticket: str,
    ) -> dict[str, Any]:
        return self.store.remote_delete(
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            principal_binding=principal_binding,
            delete_ticket=delete_ticket,
        )

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
        if section_id is not None:
            section_id = stored_section_id(section_id)
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
        return guidance_overview(
            stored.profile,
            lifecycle=stored.lifecycle,
            updated_at=stored.updated_at,
        )
