"""Domain errors with stable machine-readable codes."""

from __future__ import annotations


class CorpusError(Exception):
    """Base error returned by the CLI and MCP boundary."""

    code = "corpus_error"

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


class ConfigurationError(CorpusError):
    code = "configuration_error"


class InvalidRequestError(CorpusError):
    code = "invalid_request"


class CorpusNotFoundError(CorpusError):
    code = "corpus_not_found"


class ContextNotFoundError(CorpusError):
    code = "context_not_found"


class ContextConflictError(CorpusError):
    code = "context_conflict"


class ContextValidationError(CorpusError):
    code = "context_validation_error"


class SpaceNotFoundError(CorpusError):
    code = "space_not_found"


class SpaceValidationError(CorpusError):
    code = "space_validation"


class SpaceConflictError(CorpusError):
    code = "space_conflict"


class WorkspaceNotFoundError(CorpusError):
    code = "workspace_not_found"


class WorkspaceValidationError(CorpusError):
    code = "workspace_validation"


class WorkspaceBoundaryError(CorpusError):
    code = "workspace_boundary"


class WorkspaceUnavailableError(CorpusError):
    code = "workspace_unavailable"


class WorkspaceConflictError(CorpusError):
    code = "workspace_conflict"


class PolicyDeniedError(CorpusError):
    code = "policy_denied"


class SourceBoundaryError(CorpusError):
    code = "source_boundary_error"


class SourceChangedError(CorpusError):
    code = "source_changed_during_capture"


class SourceUnavailableError(CorpusError):
    code = "source_unavailable"


class HydrationRequiredError(CorpusError):
    code = "hydration_required"


class HydrationUnavailableError(CorpusError):
    code = "hydration_unavailable"


class BudgetExceededError(CorpusError):
    code = "budget_exceeded"


class ExtractionError(CorpusError):
    code = "extraction_error"


class SemanticCommitError(CorpusError):
    code = "semantic_commit_error"


class SnapshotConflictError(CorpusError):
    code = "snapshot_conflict"


class MigrationRequiredError(CorpusError):
    code = "migration_required"


class UnsupportedSchemaError(CorpusError):
    code = "unsupported_schema"


class MigrationError(CorpusError):
    code = "migration_error"
