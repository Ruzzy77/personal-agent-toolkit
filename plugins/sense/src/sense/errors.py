"""Sense domain errors."""

from __future__ import annotations

from typing import Any


class SenseError(Exception):
    code = "sense_error"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


class ConfigurationError(SenseError):
    code = "configuration_error"


class ProfileNotFoundError(SenseError):
    code = "profile_not_found"


class ProfileExistsError(SenseError):
    code = "profile_exists"


class PreviewReadOnlyError(SenseError):
    code = "preview_read_only"


class RevisionConflictError(SenseError):
    code = "revision_conflict"


class SectionNotFoundError(SenseError):
    code = "section_not_found"


class ConfirmationRequiredError(SenseError):
    code = "confirmation_required"


class ConfirmationMismatchError(SenseError):
    code = "confirmation_mismatch"


class IdempotencyConflictError(SenseError):
    code = "idempotency_conflict"


class InvalidDeleteTicketError(SenseError):
    code = "invalid_delete_ticket"


class MigrationStateError(SenseError):
    code = "migration_state_error"


class MigrationTargetNotEmptyError(SenseError):
    code = "migration_target_not_empty"


class UnsafeStorageError(SenseError):
    code = "unsafe_storage"


class ProfileBusyError(SenseError):
    code = "profile_busy"
