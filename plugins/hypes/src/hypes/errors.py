"""Expected Hypes service failures."""

from __future__ import annotations

from typing import Any


class HypesError(Exception):
    """A safe, structured failure that callers may show to the model."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


class RevisionConflict(HypesError):
    def __init__(self, expected: int, current: int) -> None:
        super().__init__(
            "revision_conflict",
            "the Hypes model changed; read the current revision before writing",
            details={"expected_revision": expected, "current_revision": current},
        )


class ReplayConflict(HypesError):
    def __init__(self) -> None:
        super().__init__(
            "idempotency_conflict",
            "the idempotency key was already used with different input",
        )


class InvalidTicket(HypesError):
    def __init__(self, message: str = "the forget ticket is invalid or expired") -> None:
        super().__init__("invalid_forget_ticket", message)


class DeletionCleanupPending(HypesError):
    """The logical deletion committed but SQLite still needs a physical cleanup retry."""

    def __init__(self) -> None:
        super().__init__(
            "deletion_cleanup_pending",
            "the relation deletion committed, but physical storage cleanup is still pending; "
            "retry the same forget request with the same idempotency key",
        )
