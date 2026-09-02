"""Stable errors returned to the remote broker without local path disclosure."""

from __future__ import annotations


class SyncError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class PolicyDenied(SyncError):
    def __init__(
        self, message: str = "the local Connection policy denied this operation"
    ) -> None:
        super().__init__("policy_denied", message)


class VersionConflict(SyncError):
    def __init__(
        self, message: str = "the local Work file changed after it was read"
    ) -> None:
        super().__init__("version_conflict", message)
