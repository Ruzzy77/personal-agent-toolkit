"""Expected Hypes service failures."""

from __future__ import annotations

from typing import Any


class HypesError(Exception):
    """A structured service failure whose public boundary sanitizes details."""

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


__all__ = ["HypesError"]
