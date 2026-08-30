"""Application service used by the CLI and MCP surfaces."""

from __future__ import annotations

import errno
import hashlib
import json
import re
import unicodedata
import uuid
from contextlib import nullcontext
from pathlib import Path

from .adapter_registry import AdapterRegistry, build_default_registry
from .adapters import (
    AdapterDescriptor,
    ExtractedUnit,
    ExtractionEnvelope,
    ExtractionIssue,
)
from .capture import (
    CapturedSource,
    capture_to_staging,
    cleanup_abandoned_staging,
    cleanup_source_copies,
    discard_staged_capture,
    observe_staging,
)
from .config import (
    RuntimePaths,
    default_data_root,
    is_within,
    normalize_corpus_id,
)
from .context_skills import ContextSkillService
from .contexts import ContextService, normalize_context_id
from .database import (
    configure_corpus_source_scope,
    corpus_connection,
    corpus_read_connection,
    encode_json,
    get_corpus,
    list_corpora,
    rebind_corpus_source_root,
    register_corpus,
    unregister_corpus,
    utc_now,
)
from .errors import (
    BudgetExceededError,
    ConfigurationError,
    CorpusError,
    ExtractionError,
    InvalidRequestError,
    SourceBoundaryError,
    SourceUnavailableError,
    SpaceConflictError,
    SpaceValidationError,
    WorkspaceConflictError,
)
from .locking import (
    context_writer_lock,
    source_workspace_registry_lock,
    workspace_writer_lock,
    writer_lock,
)
from .scanner import scan_corpus
from .schema import EXTRACTION_SCHEMA_VERSION
from .session_sources import SESSION_SOURCE_FETCH_DEFAULT_CHARS
from .spaces import (
    SpaceService,
    decode_space_reference,
    encode_space_reference,
    normalize_space_id,
)
from .workspaces import WORKSPACE_MAX_FILE_BYTES, WorkspaceService

_MAX_SEARCH_RESULTS = 200
_SEARCH_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_MAX_SEARCH_TERMS = 16
CORPUS_INVENTORY_DEFAULT_LIMIT = 100
CORPUS_INVENTORY_MAX_LIMIT = 200
CORPUS_INVENTORY_MAX_OFFSET = 100_000
CORPUS_INVENTORY_MAX_PATH_FILTER_CHARS = 1_000
CORPUS_INVENTORY_MAX_EXTENSION_CHARS = 20
CORPUS_INVENTORY_MAX_LOGICAL_BYTES = (1 << 63) - 1
CORPUS_INVENTORY_MAX_SERIALIZED_BYTES = 1024 * 1024
CORPUS_SEARCH_EXCERPT_MAX_CHARS = 2_000
CORPUS_SEARCH_EXCERPT_CONTEXT_BEFORE_CHARS = 400
CORPUS_SEARCH_MAX_SERIALIZED_BYTES = 1024 * 1024
SPACE_FILE_LIST_MODES = {"list_directory", "find"}
SPACE_FILE_LIST_MAX_SERIALIZED_BYTES = 1024 * 1024
SPACE_SEARCH_MAX_SERIALIZED_BYTES = 1024 * 1024
SPACE_FILE_TEXT_EXTENSIONS = {
    ".css",
    ".csv",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".py",
    ".rst",
    ".toml",
    ".ts",
    ".tsv",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
_MAX_READ_UNITS = 200
_MAX_NEIGHBOR_SPAN = 10
CORPUS_READ_MIN_CHARS = 1_000
CORPUS_READ_DEFAULT_CHARS = 30_000
CORPUS_READ_MAX_CHARS = 200_000
CORPUS_READ_MAX_SELECTED_UNITS = 500
CORPUS_READ_MAX_SERIALIZED_BYTES = 2 * 1024 * 1024
_MAX_INGEST_FILES = 50
_MAX_INGEST_BYTES = 500 * 1024 * 1024
_MAX_INGEST_FILE_BYTES = 250 * 1024 * 1024
_MAX_EXACT_INGEST_BYTES = 1024 * 1024 * 1024
_MAX_EXACT_INGEST_FILE_BYTES = 1024 * 1024 * 1024
_MAX_INGEST_DOCUMENT_IDS = 100
_MAX_INGEST_TIMEOUT_SECONDS = 600
_INVENTORY_ELIGIBILITY_STATES = {
    "all",
    "supported",
    "unsupported",
    "ignored",
}
_INVENTORY_RESIDENCY_STATES = {
    "all",
    "resident",
    "remote_only",
    "unknown",
}
_INVENTORY_INDEX_STATES = {
    "all",
    "current",
    "refresh_required",
    "unindexed",
    "not_applicable",
}


def _validate_ingest_budgets(
    *,
    max_files: int,
    max_bytes: int,
    max_file_bytes: int,
    timeout_seconds: float,
    exact_selection: bool = False,
) -> None:
    max_request_bytes = (
        _MAX_EXACT_INGEST_BYTES if exact_selection else _MAX_INGEST_BYTES
    )
    max_single_file_bytes = (
        _MAX_EXACT_INGEST_FILE_BYTES if exact_selection else _MAX_INGEST_FILE_BYTES
    )
    if (
        not 1 <= max_files <= _MAX_INGEST_FILES
        or not 1 <= max_bytes <= max_request_bytes
        or not 1 <= max_file_bytes <= max_single_file_bytes
        or not 0 < timeout_seconds <= _MAX_INGEST_TIMEOUT_SECONDS
    ):
        raise BudgetExceededError(
            "ingest budgets exceed the supported request bounds",
            details={
                "max_files": max_files,
                "max_bytes": max_bytes,
                "max_file_bytes": max_file_bytes,
                "timeout_seconds": timeout_seconds,
                "allowed": {
                    "max_files": [1, _MAX_INGEST_FILES],
                    "max_bytes": [1, max_request_bytes],
                    "max_file_bytes": [1, max_single_file_bytes],
                    "timeout_seconds": [">0", _MAX_INGEST_TIMEOUT_SECONDS],
                },
                "exact_selection": exact_selection,
            },
        )


def _ephemeral_capture_ref(sha256: str) -> str:
    return f"ephemeral:sha256:{sha256}"


def _source_is_missing(error: SourceBoundaryError) -> bool:
    reason = error.details.get("reason")
    return isinstance(reason, str) and reason.endswith(f":{errno.ENOENT}")


def _canonical_legacy_blob_ref(sha256: str) -> str:
    return f"blobs/{sha256[:2]}/{sha256}.blob"


def _safe_relative_inventory_path(value: object) -> bool:
    if not isinstance(value, str) or not value or "\x00" in value:
        return False
    if value.startswith(("/", "\\")):
        return False
    if (
        len(value) >= 3
        and value[0].isalpha()
        and value[1] == ":"
        and value[2] in {"/", "\\"}
    ):
        return False
    return all(
        part not in {"", ".", ".."} for part in value.replace("\\", "/").split("/")
    )


def _normalize_inventory_path_filter(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = unicodedata.normalize("NFC", value.strip())
    if not normalized:
        return None
    if len(normalized) > CORPUS_INVENTORY_MAX_PATH_FILTER_CHARS:
        raise BudgetExceededError(
            "inventory path filter is too long",
            details={
                "path_filter_chars": len(normalized),
                "maximum": CORPUS_INVENTORY_MAX_PATH_FILTER_CHARS,
            },
        )
    if not _safe_relative_inventory_path(normalized):
        raise ConfigurationError(
            "inventory path filter must be a safe relative literal",
        )
    return normalized


def _normalize_inventory_extension(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower().removeprefix(".")
    if not normalized:
        return None
    if len(normalized) > CORPUS_INVENTORY_MAX_EXTENSION_CHARS or not all(
        character.isascii() and character.isalnum() for character in normalized
    ):
        raise ConfigurationError(
            "inventory extension must be a lowercase alphanumeric suffix",
            details={
                "maximum_extension_chars": CORPUS_INVENTORY_MAX_EXTENSION_CHARS,
            },
        )
    return normalized


def _revision_id(document_id: str, sha256: str) -> str:
    value = hashlib.sha256(
        f"work-corpus-revision-v1\0{document_id}\0{sha256}".encode()
    ).hexdigest()
    return f"rev_{value[:32]}"


def _unit_id(projection_id: str, ordinal: int, content_sha256: str) -> str:
    value = hashlib.sha256(
        f"work-corpus-unit-v2\0{projection_id}\0{ordinal}\0{content_sha256}".encode()
    ).hexdigest()
    return f"unit_{value[:32]}"


def _projection_id(
    revision_id: str,
    adapter_id: str,
    adapter_version: str,
    config_hash: str,
    result_manifest_hash: str,
) -> str:
    value = hashlib.sha256(
        (
            "work-corpus-projection-v1\0"
            f"{revision_id}\0{adapter_id}\0{adapter_version}\0"
            f"{config_hash}\0{result_manifest_hash}"
        ).encode()
    ).hexdigest()
    return f"projection_{value[:32]}"


def _issue_locator(issue: dict) -> tuple[dict, str]:
    details = issue.get("details", {})
    structural_locator = issue.get("structural_locator")
    if not isinstance(structural_locator, dict) and isinstance(details, dict):
        structural_locator = details.get("structural_locator")
    if not isinstance(structural_locator, dict):
        structural_locator = {
            key: details.get(key, issue.get(key))
            for key in ("page", "slide", "sheet", "range", "section", "paragraph")
            if key in issue or (isinstance(details, dict) and key in details)
        }
    locator_payload = {
        "code": issue.get("code", "extractor_issue"),
        "structural_locator": structural_locator,
    }
    locator_key = hashlib.sha256(encode_json(locator_payload).encode()).hexdigest()
    return structural_locator, locator_key


def _source_span(structure: dict) -> dict | None:
    if "line_start" in structure:
        return {
            "line_start": structure["line_start"],
            "line_end": structure.get("line_end", structure["line_start"]),
        }
    if "page" in structure:
        return {"page": structure["page"]}
    if "slide" in structure:
        return {"slide": structure["slide"]}
    if "sheet" in structure:
        return {"sheet": structure["sheet"], "range": structure.get("range")}
    if "section" in structure:
        return {
            "section": structure["section"],
            "paragraph": structure.get("paragraph"),
            **{
                key: structure[key]
                for key in (
                    "record",
                    "paragraph_record",
                    "element",
                    "table",
                    "cell",
                    "row",
                    "col",
                    "note",
                    "object",
                )
                if key in structure
            },
        }
    return None


class CorpusService:
    def __init__(
        self,
        data_root: Path | None = None,
        *,
        adapter_registry: AdapterRegistry | None = None,
    ) -> None:
        self.data_root = (data_root or default_data_root()).expanduser().resolve()
        self.adapter_registry = adapter_registry or build_default_registry(
            self.data_root / "runtime"
        )
        self.contexts = ContextService(
            self.data_root,
            adapter_registry=self.adapter_registry,
        )
        self.context_skills = ContextSkillService(
            self.data_root,
            contexts=self.contexts,
        )
        self.workspaces = WorkspaceService(
            self.data_root,
            contexts=self.contexts,
        )
        self.spaces = SpaceService(
            self.data_root,
            contexts=self.contexts,
            context_skills=self.context_skills,
            workspaces=self.workspaces,
            source_state=self._space_source_state,
        )

    def _space_source_state(self, corpus_id: str) -> str:
        """Project current searchable coverage to one Chat-facing state."""

        try:
            with corpus_read_connection(self.data_root, corpus_id) as connection:
                latest_scan = connection.execute(
                    "SELECT status FROM scan_runs ORDER BY rowid DESC LIMIT 1"
                ).fetchone()
                summary = connection.execute(
                    """
                    SELECT COUNT(*) AS supported_documents,
                           COALESCE(SUM(CASE
                               WHEN d.current_revision_id IS NULL OR p.projection_id IS NULL
                               THEN 1 ELSE 0 END), 0) AS missing_projections,
                           COALESCE(SUM(CASE
                               WHEN d.current_revision_id IS NOT NULL AND (
                                   r.revision_id IS NULL
                                   OR r.source_size != d.logical_size
                                   OR r.source_modified_ns != d.modified_ns
                                   OR r.source_changed_ns != d.changed_ns
                                   OR r.source_inode != d.inode
                               ) THEN 1 ELSE 0 END), 0) AS stale_projections,
                           COALESCE(SUM(CASE
                               WHEN p.projection_id IS NOT NULL
                                AND p.completeness_state != 'complete'
                               THEN 1 ELSE 0 END), 0) AS partial_projections,
                           COALESCE(SUM(EXISTS(
                               SELECT 1 FROM extraction_issues progress
                               WHERE progress.projection_id = p.projection_id
                                 AND progress.lifecycle_state = 'active'
                                 AND progress.code IN ('pdf_page_range_pending', 'pdf_page_limit_reached')
                           )), 0) AS continuation_projections
                    FROM documents d
                    LEFT JOIN revisions r ON r.revision_id = d.current_revision_id
                    LEFT JOIN extraction_projections p
                      ON p.revision_id = d.current_revision_id AND p.is_active = 1
                    WHERE d.deleted_at IS NULL AND d.eligibility_state = 'supported'
                    """
                ).fetchone()
                adapter_rows = connection.execute(
                    """
                    SELECT DISTINCT d.extension, p.adapter_id, p.adapter_version, p.config_hash
                    FROM documents d
                    JOIN extraction_projections p
                      ON p.revision_id = d.current_revision_id AND p.is_active = 1
                    WHERE d.deleted_at IS NULL AND d.eligibility_state = 'supported'
                    """
                ).fetchall()
        except (CorpusError, OSError):
            return "unavailable"

        if latest_scan is None:
            return "needs_refresh"

        if (
            summary["stale_projections"]
            or summary["continuation_projections"]
            or any(
                not self._projection_uses_current_adapter(
                    row["extension"],
                    row["adapter_id"],
                    row["adapter_version"],
                    row["config_hash"],
                )
                for row in adapter_rows
            )
        ):
            return "needs_refresh"

        partial = (
            latest_scan["status"] != "complete"
            or bool(summary["missing_projections"])
            or bool(summary["partial_projections"])
        )
        if partial:
            return "partial"
        return "ready"

    def _prune_corpus_history_locked(self, corpus_id: str) -> None:
        """Keep only the current searchable projection and current failures."""

        with corpus_connection(self.data_root, corpus_id) as connection:
            legacy_tables = {
                row["name"]
                for row in connection.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type = 'table'
                      AND name IN ('snapshot_documents', 'snapshots', 'events')
                    """
                )
            }
            for table in ("snapshot_documents", "snapshots", "events"):
                if table in legacy_tables:
                    connection.execute(f"DELETE FROM {table}")
            connection.execute(
                """
                UPDATE documents
                SET current_revision_id = NULL
                WHERE deleted_at IS NOT NULL OR eligibility_state != 'supported'
                """
            )
            connection.execute(
                """
                DELETE FROM extraction_issues
                WHERE lifecycle_state != 'active'
                   OR (
                       revision_id IS NOT NULL
                       AND revision_id NOT IN (
                           SELECT current_revision_id FROM documents
                           WHERE current_revision_id IS NOT NULL
                       )
                   )
                   OR (
                       projection_id IS NOT NULL
                       AND projection_id NOT IN (
                           SELECT p.projection_id
                           FROM documents d
                           JOIN extraction_projections p
                             ON p.revision_id = d.current_revision_id
                            AND p.is_active = 1
                           WHERE d.deleted_at IS NULL
                             AND d.eligibility_state = 'supported'
                       )
                   )
                """
            )
            connection.execute(
                """
                DELETE FROM extraction_attempts
                WHERE revision_id NOT IN (
                    SELECT current_revision_id FROM documents
                    WHERE current_revision_id IS NOT NULL
                )
                   OR (
                       projection_id IS NOT NULL
                       AND projection_id NOT IN (
                           SELECT p.projection_id
                           FROM documents d
                           JOIN extraction_projections p
                             ON p.revision_id = d.current_revision_id
                            AND p.is_active = 1
                       )
                   )
                """
            )
            connection.execute(
                """
                DELETE FROM extraction_attempts
                WHERE attempt_id IN (
                    SELECT attempt_id FROM (
                        SELECT attempt_id,
                               ROW_NUMBER() OVER (
                                   PARTITION BY revision_id, adapter_id,
                                                adapter_version, config_hash
                                   ORDER BY started_at DESC, attempt_id DESC
                               ) AS attempt_rank
                        FROM extraction_attempts
                    )
                    WHERE attempt_rank > 1
                )
                  AND attempt_id NOT IN (
                      SELECT attempt_id FROM extraction_issues
                      WHERE lifecycle_state = 'active' AND attempt_id IS NOT NULL
                  )
                """
            )
            connection.execute(
                """
                DELETE FROM source_units
                WHERE projection_id NOT IN (
                    SELECT p.projection_id
                    FROM documents d
                    JOIN extraction_projections p
                      ON p.revision_id = d.current_revision_id
                     AND p.is_active = 1
                    WHERE d.deleted_at IS NULL
                      AND d.eligibility_state = 'supported'
                )
                """
            )
            connection.execute(
                """
                DELETE FROM source_units_fts
                WHERE unit_id NOT IN (SELECT unit_id FROM source_units)
                """
            )
            connection.execute(
                """
                DELETE FROM extraction_projections
                WHERE projection_id NOT IN (
                    SELECT p.projection_id
                    FROM documents d
                    JOIN extraction_projections p
                      ON p.revision_id = d.current_revision_id
                     AND p.is_active = 1
                    WHERE d.deleted_at IS NULL
                      AND d.eligibility_state = 'supported'
                )
                """
            )
            connection.execute("UPDATE revisions SET predecessor_revision_id = NULL")
            connection.execute(
                """
                DELETE FROM revisions
                WHERE revision_id NOT IN (
                    SELECT current_revision_id FROM documents
                    WHERE current_revision_id IS NOT NULL
                )
                """
            )
            connection.execute(
                """
                DELETE FROM scan_runs
                WHERE scan_id NOT IN (
                    SELECT last_seen_scan_id FROM documents
                    UNION
                    SELECT scan_id FROM extraction_issues WHERE scan_id IS NOT NULL
                )
                  AND scan_id != (
                      SELECT scan_id FROM scan_runs ORDER BY rowid DESC LIMIT 1
                  )
                """
            )

    def _require_source_outside_workspaces(self, source_root: Path) -> None:
        # Let the existing source registration/rebind validation report a missing
        # or otherwise invalid root with its established domain error.  This
        # preflight only owns the editable-workspace overlap decision.
        requested = source_root.expanduser().resolve(strict=False)
        for workspace_root in self.workspaces.roots():
            connected = workspace_root.expanduser().resolve(strict=False)
            if requested == connected:
                continue
            if is_within(requested, connected) or is_within(connected, requested):
                raise SourceBoundaryError(
                    "registered sources and editable work folders must not partially overlap",
                    details={"reason": "workspace_root_overlap"},
                )

    def _projection_uses_current_adapter(
        self,
        extension: str,
        adapter_id: str,
        adapter_version: str,
        config_hash: str,
    ) -> bool:
        return self.adapter_registry.accepts_projection(
            extension,
            adapter_id,
            adapter_version,
            config_hash,
        )

    def register(
        self,
        *,
        corpus_id: str,
        source_root: Path,
        execution_policy: str,
        provider_kind: str = "filesystem",
        source_scope: dict | None = None,
    ) -> dict:
        self._require_source_outside_workspaces(source_root)
        with source_workspace_registry_lock(self.data_root):
            self._require_source_outside_workspaces(source_root)
            return register_corpus(
                data_root=self.data_root,
                corpus_id=corpus_id,
                source_root=source_root,
                execution_policy=execution_policy,
                provider_kind=provider_kind,
                source_scope=source_scope,
            )

    def configure_source_scope(
        self,
        *,
        corpus_id: str,
        exclude_directory_names: object = (),
        exclude_path_prefixes: object = (),
    ) -> dict:
        return configure_corpus_source_scope(
            data_root=self.data_root,
            corpus_id=corpus_id,
            exclude_directory_names=exclude_directory_names,
            exclude_path_prefixes=exclude_path_prefixes,
        )

    def rebind_source_root(
        self,
        *,
        corpus_id: str,
        source_root: Path,
        expected_source_root: Path,
    ) -> dict:
        corpus_id = normalize_corpus_id(corpus_id)
        get_corpus(self.data_root, corpus_id)
        self._require_source_outside_workspaces(source_root)
        paths = self._paths(corpus_id)
        paths.ensure()
        with source_workspace_registry_lock(self.data_root):
            self._require_source_outside_workspaces(source_root)
            with writer_lock(paths.corpus_root / "writer.lock"):
                return rebind_corpus_source_root(
                    data_root=self.data_root,
                    corpus_id=corpus_id,
                    source_root=source_root,
                    expected_source_root=expected_source_root,
                )

    def corpora(self) -> list[dict]:
        return list_corpora(self.data_root)

    def unregister(
        self,
        *,
        corpus_id: str,
        expected_source_root: Path,
        confirm_unregister: bool,
        archived_context_id: str | None = None,
        expected_context_version: int | None = None,
        confirm_remove_linked_history: bool = False,
    ) -> dict:
        if confirm_unregister is not True:
            raise ConfigurationError(
                "corpus unregister requires explicit confirmation",
                details={"reason": "confirmation_required"},
            )
        cleanup_requested = (
            any(
                value is not None
                for value in (archived_context_id, expected_context_version)
            )
            or confirm_remove_linked_history
        )
        if cleanup_requested:
            if archived_context_id is None or expected_context_version is None:
                raise ConfigurationError(
                    "archived Context cleanup requires its id and current version",
                    details={"reason": "archived_context_options_incomplete"},
                )
            archived_context_id = normalize_context_id(archived_context_id)
            if (
                isinstance(expected_context_version, bool)
                or not isinstance(expected_context_version, int)
                or expected_context_version < 1
            ):
                raise ConfigurationError(
                    "expected Context version must be a positive integer",
                    details={"reason": "invalid_context_version"},
                )
            if confirm_remove_linked_history is not True:
                raise ConfigurationError(
                    "linked Context and source history removal requires confirmation",
                    details={"reason": "linked_history_confirmation_required"},
                )
        corpus_id = normalize_corpus_id(corpus_id)
        get_corpus(self.data_root, corpus_id)
        paths = self._paths(corpus_id)
        with (
            source_workspace_registry_lock(self.data_root),
            workspace_writer_lock(self.data_root),
            context_writer_lock(self.data_root),
        ):
            corpus_lock = (
                writer_lock(paths.corpus_root / "writer.lock")
                if paths.corpus_root.exists() or paths.corpus_root.is_symlink()
                else nullcontext()
            )
            with corpus_lock:
                return unregister_corpus(
                    data_root=self.data_root,
                    corpus_id=corpus_id,
                    expected_source_root=expected_source_root,
                    archived_context_id=archived_context_id,
                    expected_context_version=expected_context_version,
                )

    def context_read(
        self,
        *,
        context_id: str | None = None,
        state: str = "active",
        limit: int = 100,
        offset: int = 0,
        audience: str = "local_cli",
    ) -> dict:
        return self.contexts.read(
            context_id=context_id,
            state=state,
            limit=limit,
            offset=offset,
            audience=audience,
        )

    def context_update(
        self,
        *,
        action: str,
        context_id: str,
        expected_version: int,
        payload: dict,
        audience: str = "local_cli",
    ) -> dict:
        def update_context() -> dict:
            return self.contexts.update(
                action=action,
                context_id=context_id,
                expected_version=expected_version,
                payload=payload,
                audience=audience,
            )

        if action == "archive":
            with workspace_writer_lock(self.data_root):
                return update_context()
        return update_context()

    def context_skill_read(
        self,
        *,
        context_id: str,
        audience: str = "local_cli",
    ) -> dict:
        return {
            "context_id": context_id,
            "skill": self.context_skills.read(
                context_id=context_id,
                audience=audience,
            ),
        }

    def context_skill_set(
        self,
        *,
        context_id: str,
        skill_file: Path,
        expected_version: str,
        confirm_context_skill_write: bool,
    ) -> dict:
        return self.context_skills.set(
            context_id=context_id,
            skill_file=skill_file,
            expected_version=expected_version,
            confirm_context_skill_write=confirm_context_skill_write,
        )

    def context_skill_revise(
        self,
        *,
        context_id: str,
        name: str,
        description: str,
        instructions: str,
        expected_version: str,
        audience: str = "local_cli",
    ) -> dict:
        if audience == "external_mcp":
            self.space_get(
                space_id=context_id,
                audience=audience,
                context_limit=1,
                context_offset=0,
            )
        result = self.context_skills.set_content(
            context_id=context_id,
            name=name,
            description=description,
            instructions=instructions,
            expected_version=expected_version,
            confirm_context_skill_write=True,
        )
        result["skill"] = self.context_skills.read(
            context_id=context_id,
            audience=audience,
            require_context=False,
        )
        return result

    def context_skill_remove(
        self,
        *,
        context_id: str,
        expected_version: str,
        confirm_context_skill_remove: bool,
    ) -> dict:
        return self.context_skills.remove(
            context_id=context_id,
            expected_version=expected_version,
            confirm_context_skill_remove=confirm_context_skill_remove,
        )

    def space_list(
        self,
        *,
        audience: str = "local_cli",
        limit: int = 100,
        offset: int = 0,
    ) -> dict:
        return self.spaces.list(
            audience=audience,
            limit=limit,
            offset=offset,
        )

    def space_get(
        self,
        *,
        space_id: str,
        audience: str = "local_cli",
        context_limit: int = 100,
        context_offset: int = 0,
    ) -> dict:
        return self.spaces.get(
            space_id=space_id,
            audience=audience,
            context_limit=context_limit,
            context_offset=context_offset,
        )

    @staticmethod
    def _space_content_capability(
        *,
        relative_path: str,
        kind: str,
        size: int,
        residency_state: str,
        indexed: bool = False,
    ) -> str:
        if kind == "directory":
            return "directory"
        if residency_state == "remote_only":
            return "local_work_only"
        if indexed:
            return "indexed_text"
        if size > WORKSPACE_MAX_FILE_BYTES:
            return "local_work_only"
        return (
            "text_inline"
            if Path(relative_path).suffix.lower() in SPACE_FILE_TEXT_EXTENSIONS
            else "bytes_inline"
        )

    @staticmethod
    def _space_connection_response(
        resolved: dict,
        *,
        work_folder: dict | None = None,
    ) -> dict:
        connection = dict(resolved["connection"])
        if work_folder is not None:
            connection.update(
                {
                    "connection_state": work_folder["connection_state"],
                    "connection_reason": work_folder["connection_reason"],
                    "current_file": work_folder["current_file"],
                    "generation": work_folder["generation"],
                    "write_state": (
                        "unknown"
                        if work_folder["connection_state"] == "connected"
                        else None
                    ),
                }
            )
        return {
            "space_id": resolved["space"]["space_id"],
            "connection": connection,
        }

    @staticmethod
    def _bounded_space_response(
        response: dict,
        *,
        maximum_bytes: int,
        surface: str,
    ) -> dict:
        serialized_bytes = len(encode_json(response).encode())
        if serialized_bytes > maximum_bytes:
            raise BudgetExceededError(
                f"{surface} response exceeds the serialized response budget",
                details={
                    "serialized_bytes": serialized_bytes,
                    "maximum_serialized_bytes": maximum_bytes,
                },
            )
        return response

    def space_search(
        self,
        *,
        space_id: str,
        query: str,
        connection_id: str | None = None,
        limit: int = 20,
        audience: str = "local_cli",
    ) -> dict:
        canonical_space_id = normalize_space_id(space_id)
        canonical_connection_id = (
            normalize_space_id(connection_id) if connection_id is not None else None
        )
        if isinstance(limit, bool) or not 1 <= limit <= _MAX_SEARCH_RESULTS:
            raise SpaceValidationError(
                "Space search limit is outside the supported range",
                details={"limit": limit, "maximum": _MAX_SEARCH_RESULTS},
            )
        resolved_connections = self.spaces.resolve_source_connections(
            space_id=canonical_space_id,
            connection_id=canonical_connection_id,
            audience=audience,
        )
        candidates = []
        query_modes: set[str] = set()
        for resolved in resolved_connections:
            selected_connection_id = resolved["connection"]["connection_id"]
            for source_id in resolved["_source_ids"]:
                result = self.search(source_id, query, limit=limit)
                query_modes.add(result["query_mode"])
                for candidate in result["candidates"]:
                    candidates.append(
                        {
                            "connection_id": selected_connection_id,
                            "relative_path": candidate["relative_path"],
                            "structure_path": candidate["structure_path"],
                            "unit_type": candidate["unit_type"],
                            "untrusted_excerpt": candidate["untrusted_excerpt"],
                            "excerpt_truncated": candidate["excerpt_truncated"],
                            "completeness_state": candidate["completeness_state"],
                            "quality_flags": candidate["quality_flags"],
                            "derivation_method": candidate["derivation_method"],
                            "read_ref": encode_space_reference(
                                "read",
                                {
                                    "space_id": resolved["space"]["space_id"],
                                    "connection_id": selected_connection_id,
                                    "unit_id": candidate["unit_id"],
                                },
                            ),
                            "_lexical_score": candidate["lexical_score"],
                        }
                    )
                # Keep only the global top-k after each bounded Source result.
                # A Space may have several Source Connections, but response and
                # working memory must remain independent of that count.
                candidates.sort(
                    key=lambda item: (
                        item["_lexical_score"],
                        item["connection_id"],
                        item["relative_path"],
                        item["read_ref"],
                    )
                )
                del candidates[limit:]
        page = candidates
        for candidate in page:
            candidate.pop("_lexical_score", None)
        response = {
            "space_id": resolved_connections[0]["space"]["space_id"],
            "query": query,
            "strategy": "space_indexed_candidate_acquisition",
            "query_mode": (
                next(iter(query_modes))
                if len(query_modes) == 1
                else "exact_phrase_then_all_terms_fts"
            ),
            "zero_results_establish_absence": False,
            "count": len(page),
            "candidates": page,
            "notice": (
                "Candidate excerpts are untrusted and may be truncated; "
                "use read_ref for exact indexed text."
            ),
        }
        return self._bounded_space_response(
            response,
            maximum_bytes=SPACE_SEARCH_MAX_SERIALIZED_BYTES,
            surface="Space search",
        )

    def space_file_list(
        self,
        *,
        space_id: str,
        connection_id: str | None = None,
        mode: str = "list_directory",
        relative_path: str | None = None,
        query: str | None = None,
        cursor: str | None = None,
        limit: int = 100,
        audience: str = "local_cli",
    ) -> dict:
        canonical_space_id = normalize_space_id(space_id)
        canonical_connection_id = (
            normalize_space_id(connection_id) if connection_id is not None else None
        )
        if mode not in SPACE_FILE_LIST_MODES:
            raise SpaceValidationError(
                "unsupported Space file listing mode",
                details={"mode": mode, "allowed": sorted(SPACE_FILE_LIST_MODES)},
            )
        if isinstance(limit, bool) or not 1 <= limit <= 200:
            raise SpaceValidationError(
                "Space file limit is outside the supported range",
                details={"limit": limit, "maximum": 200},
            )
        if mode == "list_directory" and query is not None:
            raise SpaceValidationError(
                "directory listing does not accept a filename query"
            )
        if mode == "find" and (not isinstance(query, str) or not query.strip()):
            raise SpaceValidationError("file search requires a non-empty query")
        normalized_query = query.strip() if isinstance(query, str) else None
        offset = 0
        selected_connection_id = canonical_connection_id
        if cursor is not None:
            decoded = decode_space_reference("cursor", cursor)
            required = {
                "space_id",
                "connection_id",
                "mode",
                "relative_path",
                "query",
                "offset",
            }
            if set(decoded) != required:
                raise SpaceValidationError("Space file cursor is invalid")
            expected = {
                "space_id": canonical_space_id,
                "mode": mode,
                "relative_path": relative_path,
                "query": normalized_query,
            }
            if any(decoded[field] != value for field, value in expected.items()):
                raise SpaceConflictError(
                    "Space file cursor does not match the current request",
                    details={"reason": "cursor_request_mismatch"},
                )
            if (
                canonical_connection_id is not None
                and decoded["connection_id"] != canonical_connection_id
            ):
                raise SpaceConflictError(
                    "Space file cursor does not match the selected Connection",
                    details={"reason": "cursor_connection_mismatch"},
                )
            selected_connection_id = decoded["connection_id"]
            offset = decoded["offset"]
            if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
                raise SpaceValidationError("Space file cursor is invalid")

        resolved = self.spaces.resolve_connection(
            space_id=canonical_space_id,
            connection_id=selected_connection_id,
            audience=audience,
            capability="read",
        )
        selected_connection_id = resolved["connection"]["connection_id"]
        workspace_id = resolved["_workspace_id"]
        entries: list[dict] = []
        total_matching: int | None
        work_folder: dict | None = None
        listing_truncated = False
        if workspace_id is not None:
            listing = self.workspaces.files(
                workspace_id=workspace_id,
                relative_path=relative_path,
                path_contains=normalized_query if mode == "find" else None,
                limit=limit,
                offset=offset,
                audience=audience,
                recursive=mode == "find",
            )
            for entry in listing["entries"]:
                entries.append(
                    {
                        **entry,
                        "content_capability": self._space_content_capability(
                            relative_path=entry["relative_path"],
                            kind=entry["kind"],
                            size=entry["size"],
                            residency_state=entry["residency_state"],
                        ),
                    }
                )
            has_more = bool(listing["has_more"])
            next_offset = listing["next_offset"]
            total_matching = listing["total_matching"]
            listing_truncated = bool(listing["listing_truncated"])
            work_folder = listing["work_folder"]
            if listing_truncated and not has_more and next_offset is None:
                # The bounded v1 walker knows that unseen entries exist but
                # cannot yet produce a safe continuation beyond its scan cap.
                # Do not falsely report completion.
                has_more = None
            skipped = listing["skipped"]
        else:
            if mode != "find":
                raise SpaceValidationError(
                    "indexed Source-only Connections support filename search, "
                    "not live directory listing",
                    details={"connection_id": selected_connection_id},
                )
            source_ids = resolved["_source_ids"]
            if len(source_ids) != 1:
                raise SpaceValidationError(
                    "file search requires a Connection with one indexed Source"
                )
            inventory = self.inventory(
                source_ids[0],
                path_contains=normalized_query,
                eligibility_state="supported",
                residency_state="all",
                index_state="all",
                limit=limit,
                offset=offset,
            )
            for document in inventory["documents"]:
                indexed = document["active_projection_id"] is not None
                entries.append(
                    {
                        "relative_path": document["relative_path"],
                        "kind": "file",
                        "size": document["logical_size"],
                        "modified_ns": document["modified_ns"],
                        "residency_state": document["residency_state"],
                        "index_state": document["index_state"],
                        "content_capability": self._space_content_capability(
                            relative_path=document["relative_path"],
                            kind="file",
                            size=document["logical_size"],
                            residency_state=document["residency_state"],
                            indexed=indexed,
                        ),
                    }
                )
            has_more = bool(inventory["has_more"])
            next_offset = inventory["next_offset"]
            total_matching = inventory["total_matching"]
            skipped = None

        next_cursor = None
        if has_more is True and isinstance(next_offset, int):
            next_cursor = encode_space_reference(
                "cursor",
                {
                    "space_id": resolved["space"]["space_id"],
                    "connection_id": selected_connection_id,
                    "mode": mode,
                    "relative_path": relative_path,
                    "query": normalized_query,
                    "offset": next_offset,
                },
            )
        response = {
            **self._space_connection_response(resolved, work_folder=work_folder),
            "mode": mode,
            "relative_path": relative_path,
            "query": normalized_query,
            "returned_count": len(entries),
            "total_matching": total_matching,
            "has_more": has_more,
            "next_cursor": next_cursor,
            "listing_truncated": listing_truncated,
            "entries": entries,
            "skipped": skipped,
        }
        return self._bounded_space_response(
            response,
            maximum_bytes=SPACE_FILE_LIST_MAX_SERIALIZED_BYTES,
            surface="Space file listing",
        )

    def space_file_read(
        self,
        *,
        space_id: str,
        connection_id: str | None = None,
        relative_path: str | None = None,
        read_ref: str | None = None,
        encoding: str = "utf8",
        max_bytes: int = WORKSPACE_MAX_FILE_BYTES,
        neighbor_span: int = 0,
        include_structure_context: bool = False,
        max_chars: int = CORPUS_READ_DEFAULT_CHARS,
        start_char: int = 0,
        audience: str = "local_cli",
    ) -> dict:
        canonical_space_id = normalize_space_id(space_id)
        canonical_connection_id = (
            normalize_space_id(connection_id) if connection_id is not None else None
        )
        if type(include_structure_context) is not bool:
            raise SpaceValidationError("include_structure_context must be a boolean")
        if include_structure_context and read_ref is None:
            raise SpaceValidationError(
                "structure context is only available for indexed Source reads"
            )
        if read_ref is not None and relative_path is not None:
            raise SpaceValidationError("choose either read_ref or relative_path")
        if type(max_chars) is not int or not (
            CORPUS_READ_MIN_CHARS <= max_chars <= CORPUS_READ_MAX_CHARS
        ):
            raise SpaceValidationError("max_chars must be between 1000 and 200000")
        if (
            type(start_char) is not int
            or not 0 <= start_char <= WORKSPACE_MAX_FILE_BYTES
        ):
            raise SpaceValidationError("start_char is outside the supported range")
        if read_ref is not None:
            if start_char != 0:
                raise SpaceValidationError(
                    "start_char is only supported for live UTF-8 Work files"
                )
            payload = decode_space_reference("read", read_ref)
            if set(payload) != {"space_id", "connection_id", "unit_id"}:
                raise SpaceValidationError("Space read reference is invalid")
            if payload["space_id"] != canonical_space_id or (
                canonical_connection_id is not None
                and payload["connection_id"] != canonical_connection_id
            ):
                raise SpaceConflictError(
                    "Space read reference does not match the current request",
                    details={"reason": "read_ref_request_mismatch"},
                )
            resolved = self.spaces.resolve_connection(
                space_id=canonical_space_id,
                connection_id=payload["connection_id"],
                audience=audience,
                capability="source",
            )
            indexed = None
            selected_source_id = None
            for source_id in resolved["_source_ids"]:
                candidate = self.read_units(
                    source_id,
                    [payload["unit_id"]],
                    neighbor_span=neighbor_span,
                    include_structure_context=include_structure_context,
                    max_chars=max_chars,
                )
                if candidate["units"]:
                    if indexed is not None:
                        raise SpaceConflictError(
                            "Space read reference is ambiguous after Source rebinding",
                            details={"reason": "read_ref_ambiguous"},
                        )
                    indexed = candidate
                    selected_source_id = source_id
            if indexed is None or selected_source_id is None:
                raise SpaceConflictError(
                    "Space read reference no longer belongs to the selected Connection",
                    details={"reason": "read_ref_binding_changed"},
                )
            units = []
            for unit in indexed["units"]:
                units.append(
                    {
                        "connection_id": resolved["connection"]["connection_id"],
                        "relative_path": unit["relative_path"],
                        "structure_path": unit["structure_path"],
                        "unit_type": unit["unit_type"],
                        "requested": unit["requested"],
                        "dependency_state": unit["dependency_state"],
                        "untrusted_content": unit["untrusted_content"],
                        "read_ref": encode_space_reference(
                            "read",
                            {
                                "space_id": resolved["space"]["space_id"],
                                "connection_id": resolved["connection"][
                                    "connection_id"
                                ],
                                "unit_id": unit["unit_id"],
                            },
                        ),
                    }
                )
            return {
                **self._space_connection_response(resolved),
                "source_kind": "indexed_source",
                "count": len(units),
                "units": units,
                "content_is_untrusted": True,
            }

        resolved = self.spaces.resolve_connection(
            space_id=canonical_space_id,
            connection_id=canonical_connection_id,
            audience=audience,
            capability="read",
        )
        workspace_id = resolved["_workspace_id"]
        if workspace_id is None:
            raise SpaceValidationError(
                "Source-only Connection reads require a read_ref returned by Space search"
            )
        if encoding == "base64" and start_char != 0:
            raise SpaceValidationError(
                "start_char is only supported for live UTF-8 Work files"
            )
        live = self.workspaces.read(
            workspace_id=workspace_id,
            relative_path=relative_path,
            encoding=encoding,
            max_bytes=max_bytes,
            audience=audience,
        )
        file_info = dict(live["file"])
        file_info["content_capability"] = self._space_content_capability(
            relative_path=file_info["relative_path"],
            kind="file",
            size=file_info["size"],
            residency_state="resident",
        )
        content = live["content"]
        complete = True
        next_start_char = None
        if encoding == "utf8":
            total_chars = len(content)
            if start_char > total_chars:
                raise SpaceValidationError(
                    "start_char is past the end of the Work file"
                )
            end_char = min(total_chars, start_char + max_chars)
            content = content[start_char:end_char]
            complete = start_char == 0 and end_char == total_chars
            if end_char < total_chars:
                next_start_char = end_char
        response = {
            **self._space_connection_response(
                resolved, work_folder=live["work_folder"]
            ),
            "source_kind": "live_file",
            "file": file_info,
            "encoding": live["encoding"],
            "content": content,
            "content_is_untrusted": True,
        }
        if next_start_char is not None:
            response["next_start_char"] = next_start_char
        if complete:
            response["content_sha256"] = live["content_sha256"]
        return self._bounded_space_response(
            response,
            maximum_bytes=CORPUS_READ_MAX_SERIALIZED_BYTES,
            surface="Space file read",
        )

    def space_file_write(
        self,
        *,
        space_id: str,
        relative_path: str,
        content: str,
        content_encoding: str,
        expected_version: str,
        replace_start_marker: str | None = None,
        replace_end_marker: str | None = None,
        connection_id: str | None = None,
        make_current: bool = False,
        audience: str = "local_cli",
    ) -> dict:
        resolved = self.spaces.resolve_connection(
            space_id=space_id,
            connection_id=connection_id,
            audience=audience,
            capability="write",
        )
        if replace_start_marker is not None or replace_end_marker is not None:
            if (
                replace_start_marker is None
                or replace_end_marker is None
                or expected_version == "absent"
                or content_encoding != "utf8"
                or not isinstance(replace_start_marker, str)
                or not isinstance(replace_end_marker, str)
                or not isinstance(content, str)
                or not replace_start_marker
                or not replace_end_marker
                or replace_start_marker == replace_end_marker
            ):
                raise SpaceValidationError("marker-range replacement is invalid")
            current = self.workspaces.read(
                workspace_id=resolved["_workspace_id"],
                relative_path=relative_path,
                encoding="utf8",
                max_bytes=WORKSPACE_MAX_FILE_BYTES,
                audience=audience,
            )
            current_content = current["content"]
            start_index = current_content.find(replace_start_marker) + len(
                replace_start_marker
            )
            end_index = current_content.find(replace_end_marker)
            if (
                current_content.count(replace_start_marker) != 1
                or current_content.count(replace_end_marker) != 1
                or end_index < start_index
            ):
                raise WorkspaceConflictError(
                    "replacement markers do not identify one ordered range in the current file",
                    details={"reason": "marker_range_changed"},
                )
            content = (
                current_content[:start_index] + content + current_content[end_index:]
            )
        result = self.workspaces.write(
            workspace_id=resolved["_workspace_id"],
            relative_path=relative_path,
            content=content,
            content_encoding=content_encoding,
            expected_version=expected_version,
            make_current=make_current,
            audience=audience,
        )
        return {
            **self._space_connection_response(
                resolved,
                work_folder=result["work_folder"],
            ),
            "file": result["file"],
            "created": result["created"],
            "recovery_id": result["recovery_id"],
            "undo_available": result["undo_available"],
            "index_state": result["index_state"],
        }

    def space_file_select_current(
        self,
        *,
        space_id: str,
        relative_path: str,
        connection_id: str | None = None,
        audience: str = "local_cli",
    ) -> dict:
        resolved = self.spaces.resolve_connection(
            space_id=space_id,
            connection_id=connection_id,
            audience=audience,
            capability="write",
        )
        result = self.workspaces.select_current(
            workspace_id=resolved["_workspace_id"],
            relative_path=relative_path,
            audience=audience,
        )
        return {
            **self._space_connection_response(
                resolved,
                work_folder=result["work_folder"],
            ),
            "file": result["file"],
            "generation": result["work_folder"]["generation"],
            "current_file": result["work_folder"]["current_file"],
        }

    def space_file_delete(
        self,
        *,
        space_id: str,
        relative_path: str,
        expected_version: str,
        confirm_delete: bool,
        connection_id: str | None = None,
        audience: str = "local_cli",
    ) -> dict:
        resolved = self.spaces.resolve_connection(
            space_id=space_id,
            connection_id=connection_id,
            audience=audience,
            capability="write",
        )
        result = self.workspaces.delete(
            workspace_id=resolved["_workspace_id"],
            relative_path=relative_path,
            expected_version=expected_version,
            confirm_delete=confirm_delete,
            audience=audience,
        )
        return {
            **self._space_connection_response(
                resolved,
                work_folder=result["work_folder"],
            ),
            "relative_path": result["relative_path"],
            "deleted": result["deleted"],
            "index_state": result["index_state"],
        }

    def space_file_restore(
        self,
        *,
        space_id: str,
        recovery_id: str,
        expected_version: str,
        connection_id: str | None = None,
        audience: str = "local_cli",
    ) -> dict:
        resolved = self.spaces.resolve_connection(
            space_id=space_id,
            connection_id=connection_id,
            audience=audience,
            capability="write",
        )
        result = self.workspaces.restore(
            workspace_id=resolved["_workspace_id"],
            recovery_id=recovery_id,
            expected_version=expected_version,
            audience=audience,
        )
        return {
            **self._space_connection_response(
                resolved,
                work_folder=result["work_folder"],
            ),
            "file": result["file"],
            "recovery_id": result["recovery_id"],
            "restored": result["restored"],
            "recovery_metadata_recorded": result["recovery_metadata_recorded"],
            "index_state": result["index_state"],
        }

    def workspace_connect(
        self,
        *,
        workspace_id: str | None = None,
        context_id: str | None = None,
        display_name: str | None = None,
        root: Path,
        execution_policy: str,
    ) -> dict:
        return self.workspaces.connect(
            workspace_id=workspace_id,
            context_id=context_id,
            display_name=display_name,
            root=root,
            execution_policy=execution_policy,
        )

    def workspace_disconnect(
        self,
        *,
        workspace_id: str,
    ) -> dict:
        return self.workspaces.disconnect(
            workspace_id=workspace_id,
        )

    def workspace_list(self, *, audience: str = "local_cli") -> dict:
        return self.workspaces.list(audience=audience)

    def workspace_status(
        self,
        *,
        workspace_id: str,
        audience: str = "local_cli",
    ) -> dict:
        return self.workspaces.status(
            workspace_id=workspace_id,
            audience=audience,
        )

    @staticmethod
    def _workspace_state_matches_document(state: object, document: dict) -> bool:
        return bool(
            getattr(state, "state", None) == "ready"
            and getattr(state, "size", None) == document["logical_size"]
            and getattr(state, "modified_ns", None) == document["modified_ns"]
            and getattr(state, "changed_ns", None) == document["changed_ns"]
            and getattr(state, "inode", None) == document["inode"]
            and getattr(state, "mode", None) == document["mode"]
            and getattr(state, "flags", None) == document["flags"]
            and int(document["is_dataless"]) == 0
        )

    def _reconcile_workspace_index_changes(self, corpus_id: str) -> int:
        """Clear durable dirty paths only after scan/extraction is current."""

        guard = self.workspaces.promoted_source_guard(corpus_id)
        if guard is None or not guard["changes"]:
            return 0
        from . import workspace_access

        current_paths: set[str] = set()
        try:
            with (
                workspace_access.opened_workspace_root(
                    guard["root"],
                    guard["identity"],
                ) as root_descriptor,
                corpus_read_connection(self.data_root, corpus_id) as connection,
            ):
                latest_scan = connection.execute(
                    """
                    SELECT status FROM scan_runs ORDER BY rowid DESC LIMIT 1
                    """
                ).fetchone()
                for relative_path in sorted(guard["changes"])[:256]:
                    row = connection.execute(
                        """
                        SELECT d.*, r.source_size AS revision_source_size,
                               r.source_modified_ns AS revision_source_modified_ns,
                               r.source_changed_ns AS revision_source_changed_ns,
                               r.source_device AS revision_source_device,
                               r.source_inode AS revision_source_inode,
                               p.projection_id AS active_projection_id,
                               p.adapter_id AS projection_adapter_id,
                               p.adapter_version AS projection_adapter_version,
                               p.config_hash AS projection_config_hash,
                       EXISTS(SELECT 1 FROM extraction_issues progress
                           WHERE progress.projection_id = p.projection_id
                             AND progress.lifecycle_state = 'active'
                             AND progress.code IN ('pdf_page_range_pending', 'pdf_page_limit_reached')) AS projection_can_continue
                        FROM documents d
                        LEFT JOIN revisions r ON r.revision_id = d.current_revision_id
                        LEFT JOIN extraction_projections p
                          ON p.revision_id = d.current_revision_id AND p.is_active = 1
                        WHERE d.relative_path_nfc = ? AND d.deleted_at IS NULL
                        LIMIT 1
                        """,
                        (unicodedata.normalize("NFC", relative_path),),
                    ).fetchone()
                    if row is None:
                        if (
                            latest_scan is not None
                            and latest_scan["status"] == "complete"
                        ):
                            current_paths.add(relative_path)
                        continue
                    document = dict(row)
                    try:
                        state = (
                            workspace_access.workspace_file_state_from_root_descriptor(
                                root_descriptor,
                                relative_path,
                            )
                        )
                    except CorpusError:
                        continue
                    if not self._workspace_state_matches_document(state, document):
                        continue
                    if document["eligibility_state"] != "supported":
                        current_paths.add(relative_path)
                        continue
                    index_state, _reasons = self._document_index_state(document)
                    if index_state == "current":
                        current_paths.add(relative_path)
        except CorpusError:
            return 0
        return self.workspaces.clear_index_changes(
            corpus_id=corpus_id,
            relative_paths=current_paths,
        )

    def corpus_source_read(
        self,
        *,
        corpus_id: str,
        binding_id: str | None = None,
        record_state: str = "active",
        occurred_after: str | None = None,
        limit: int = 100,
        offset: int = 0,
        audience: str = "local_cli",
    ) -> dict:
        return self.contexts.source_read(
            corpus_id=corpus_id,
            binding_id=binding_id,
            record_state=record_state,
            occurred_after=occurred_after,
            limit=limit,
            offset=offset,
            audience=audience,
        )

    def corpus_source_update(
        self,
        *,
        action: str,
        corpus_id: str,
        binding_id: str,
        payload: dict,
        audience: str = "local_cli",
    ) -> dict:
        return self.contexts.source_update(
            action=action,
            corpus_id=corpus_id,
            binding_id=binding_id,
            payload=payload,
            audience=audience,
        )

    def corpus_source_fetch(
        self,
        *,
        corpus_id: str,
        binding_id: str,
        external_id: str,
        max_chars: int = SESSION_SOURCE_FETCH_DEFAULT_CHARS,
        audience: str = "local_cli",
    ) -> dict:
        return self.contexts.source_fetch(
            corpus_id=corpus_id,
            binding_id=binding_id,
            external_id=external_id,
            max_chars=max_chars,
            audience=audience,
        )

    def scan(self, corpus_id: str) -> dict:
        corpus_id = normalize_corpus_id(corpus_id)
        paths = self._paths(corpus_id)
        paths.ensure()
        with writer_lock(paths.corpus_root / "writer.lock"):
            result = scan_corpus(self.data_root, corpus_id)
            self._prune_corpus_history_locked(corpus_id)
        self._reconcile_workspace_index_changes(corpus_id)
        result["source_state"] = self._space_source_state(corpus_id)
        return result

    def cleanup_source_copies(
        self,
        corpus_id: str,
        *,
        confirm_delete: bool = False,
    ) -> dict:
        """Plan or remove retained source bytes without changing extracted units."""

        corpus_id = normalize_corpus_id(corpus_id)
        get_corpus(self.data_root, corpus_id)
        paths = self._paths(corpus_id)
        paths.ensure()
        with writer_lock(paths.corpus_root / "writer.lock"):
            cleanup = cleanup_source_copies(paths, delete=False)
            if confirm_delete:
                cleanup = cleanup_source_copies(paths, delete=True)
            references_marked_ephemeral = 0
            with corpus_connection(self.data_root, corpus_id) as connection:
                source_units_preserved = int(
                    connection.execute("SELECT COUNT(*) FROM source_units").fetchone()[
                        0
                    ]
                )
                legacy_reference_rows = connection.execute(
                    """
                    SELECT revision_id, sha256, immutable_blob_ref
                    FROM revisions
                    WHERE immutable_blob_ref LIKE 'blobs/%'
                    """
                ).fetchall()
                canonical_reference_rows = [
                    row
                    for row in legacy_reference_rows
                    if row["immutable_blob_ref"]
                    == _canonical_legacy_blob_ref(row["sha256"])
                ]
                present_digests = set(cleanup.canonical_blob_digests)
                missing_legacy_references = sum(
                    1
                    for row in canonical_reference_rows
                    if row["sha256"] not in present_digests
                )
                if confirm_delete:
                    for row in canonical_reference_rows:
                        cursor = connection.execute(
                            """
                            UPDATE revisions
                            SET immutable_blob_ref = ?
                            WHERE revision_id = ?
                              AND immutable_blob_ref = ?
                            """,
                            (
                                _ephemeral_capture_ref(row["sha256"]),
                                row["revision_id"],
                                row["immutable_blob_ref"],
                            ),
                        )
                        references_marked_ephemeral += cursor.rowcount
            result = cleanup.as_dict(deleted=confirm_delete)
            result.update(
                {
                    "corpus_id": corpus_id,
                    "confirmation_required": not confirm_delete,
                    "references_marked_ephemeral": references_marked_ephemeral,
                    "canonical_legacy_references": len(canonical_reference_rows),
                    "missing_legacy_references": missing_legacy_references,
                    "noncanonical_legacy_references_skipped": (
                        len(legacy_reference_rows) - len(canonical_reference_rows)
                    ),
                    "source_units_preserved": source_units_preserved,
                    "search_index_preserved": True,
                }
            )
            return result

    def _paths(self, corpus_id: str) -> RuntimePaths:
        corpus_id = normalize_corpus_id(corpus_id)
        return RuntimePaths(data_root=self.data_root, corpus_id=corpus_id)

    def status(
        self,
        corpus_id: str,
    ) -> dict:
        corpus = get_corpus(self.data_root, corpus_id)
        with corpus_read_connection(self.data_root, corpus_id) as connection:
            scan = connection.execute(
                "SELECT * FROM scan_runs ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            document_counts = {
                row["state"]: row["count"]
                for row in connection.execute(
                    """
                    SELECT eligibility_state || ':' || residency_state AS state, COUNT(*) AS count
                    FROM documents WHERE deleted_at IS NULL GROUP BY state
                    """
                )
            }
            totals = dict(
                connection.execute(
                    """
                    SELECT
                        COUNT(*) AS documents,
                        COALESCE(SUM(logical_size), 0) AS logical_bytes,
                        COALESCE(SUM(allocated_size), 0) AS allocated_bytes,
                        SUM(CASE WHEN is_dataless = 1 THEN 1 ELSE 0 END) AS dataless_documents,
                        SUM(CASE WHEN eligibility_state = 'supported' THEN 1 ELSE 0 END)
                            AS supported_documents,
                        SUM(
                            CASE WHEN eligibility_state = 'supported' AND EXISTS (
                                SELECT 1 FROM extraction_projections p
                                JOIN revisions r ON r.revision_id = p.revision_id
                                WHERE p.revision_id = documents.current_revision_id
                                  AND p.is_active = 1
                                  AND r.source_size = documents.logical_size
                                  AND r.source_modified_ns = documents.modified_ns
                                  AND r.source_changed_ns = documents.changed_ns
                                  AND r.source_inode = documents.inode
                            ) THEN 1 ELSE 0 END
                        ) AS indexed_documents
                    FROM documents
                    WHERE deleted_at IS NULL
                    """
                ).fetchone()
            )
            revisions = connection.execute("SELECT COUNT(*) FROM revisions").fetchone()[
                0
            ]
            units = connection.execute("SELECT COUNT(*) FROM source_units").fetchone()[
                0
            ]
            active_units = connection.execute(
                """
                SELECT COUNT(*)
                FROM source_units u
                JOIN extraction_projections p ON p.projection_id = u.projection_id
                JOIN revisions r ON r.revision_id = u.revision_id
                JOIN documents d ON d.current_revision_id = u.revision_id
                WHERE p.is_active = 1 AND d.deleted_at IS NULL
                  AND d.eligibility_state = 'supported'
                  AND r.source_size = d.logical_size
                  AND r.source_modified_ns = d.modified_ns
                  AND r.source_changed_ns = d.changed_ns
                  AND r.source_inode = d.inode
                """
            ).fetchone()[0]
            projections = {
                row["state"]: row["count"]
                for row in connection.execute(
                    """
                    SELECT
                        CASE
                            WHEN is_active = 1
                            THEN 'active:' || completeness_state || ':' || assurance_state
                            ELSE 'historical:' || completeness_state || ':' || assurance_state
                        END AS state,
                        COUNT(*) AS count
                    FROM extraction_projections
                    GROUP BY state
                    """
                )
            }
            attempts = {
                row["state"]: row["count"]
                for row in connection.execute(
                    """
                    SELECT state, COUNT(*) AS count
                    FROM extraction_attempts
                    GROUP BY state
                    """
                )
            }
            active_projection_rows = connection.execute(
                """
                SELECT d.extension, p.adapter_id, p.adapter_version, p.config_hash,
                       p.completeness_state
                FROM documents d
                JOIN revisions r ON r.revision_id = d.current_revision_id
                JOIN extraction_projections p
                  ON p.revision_id = d.current_revision_id AND p.is_active = 1
                WHERE d.deleted_at IS NULL
                  AND d.eligibility_state = 'supported'
                  AND r.source_size = d.logical_size
                  AND r.source_modified_ns = d.modified_ns
                  AND r.source_changed_ns = d.changed_ns
                  AND r.source_inode = d.inode
                """
            ).fetchall()
            issues = connection.execute(
                "SELECT COUNT(*) FROM extraction_issues"
            ).fetchone()[0]
            issue_lifecycle = {
                row["lifecycle_state"]: row["count"]
                for row in connection.execute(
                    """
                    SELECT lifecycle_state, COUNT(*) AS count
                    FROM extraction_issues
                    GROUP BY lifecycle_state
                    """
                )
            }
            partial_by_format = dict(
                connection.execute(
                    """SELECT d.extension, COUNT(*) FROM documents d
                   JOIN extraction_projections p ON p.revision_id = d.current_revision_id AND p.is_active = 1
                   WHERE d.deleted_at IS NULL AND p.completeness_state = 'partial'
                   GROUP BY d.extension"""
                ).fetchall()
            )
            partial_by_issue = dict(
                connection.execute(
                    """SELECT i.code, COUNT(DISTINCT d.document_id) FROM documents d
                   JOIN extraction_projections p ON p.revision_id = d.current_revision_id AND p.is_active = 1
                   JOIN extraction_issues i ON i.projection_id = p.projection_id AND i.lifecycle_state = 'active'
                   WHERE d.deleted_at IS NULL AND p.completeness_state = 'partial'
                     AND i.severity IN ('warning', 'error') GROUP BY i.code"""
                ).fetchall()
            )
        outdated_projections = 0
        partial_projections = 0
        for row in active_projection_rows:
            if row["completeness_state"] != "complete":
                partial_projections += 1
            if not self.adapter_registry.accepts_projection(
                row["extension"],
                row["adapter_id"],
                row["adapter_version"],
                row["config_hash"],
            ):
                outdated_projections += 1
        supported_documents = int(totals["supported_documents"] or 0)
        indexed_documents = int(totals["indexed_documents"] or 0)
        coverage_gaps = {
            "supported_documents_without_usable_projection": max(
                0, supported_documents - indexed_documents
            ),
            "partial_active_projections": partial_projections,
            "outdated_active_projections": outdated_projections,
        }
        staging_observation = observe_staging(self._paths(corpus_id))
        response = {
            "corpus": corpus,
            "data_root": str(self.data_root),
            "latest_scan": dict(scan) if scan else None,
            "totals": totals,
            "document_states": document_counts,
            "revisions": revisions,
            "source_units": units,
            "active_source_units": active_units,
            "extraction_projections": projections,
            "extraction_attempts": attempts,
            "source_state": self._space_source_state(corpus_id),
            "coverage_gaps": coverage_gaps,
            "partial_extraction": {
                "by_format": partial_by_format,
                "by_issue": partial_by_issue,
                "issue_document_counts_overlap": True,
            },
            "issues": issues,
            "issue_lifecycle": issue_lifecycle,
            "extraction_adapters": [
                descriptor.to_dict() for descriptor in self.adapter_registry.descriptors
            ],
            "authority": {
                "source": "registered source bytes",
                "extracted_projection": "source_units",
                "request_time_interpretation": "temporary interpretation for the current question",
            },
            "source_copy_retention": {
                "default": "ephemeral",
                "persistent_source_bytes_required_for_search": False,
                "intentional_absence_marker": "ephemeral:sha256:<digest>",
                "staging_observation": staging_observation,
            },
        }
        return response

    def _document_index_state(self, document: dict) -> tuple[str, list[str]]:
        if document["eligibility_state"] != "supported":
            return "not_applicable", [f"eligibility:{document['eligibility_state']}"]
        if document["current_revision_id"] is None:
            return "unindexed", ["no_current_revision"]

        reasons: list[str] = []
        if document["active_projection_id"] is None:
            reasons.append("active_projection_missing")
        source_observation_current = (
            document["revision_source_size"] == document["logical_size"]
            and document["revision_source_modified_ns"] == document["modified_ns"]
            and document["revision_source_changed_ns"] == document["changed_ns"]
            and document["revision_source_inode"] == document["inode"]
        )
        if not source_observation_current:
            reasons.append("source_observation_changed")

        adapter_current = document[
            "active_projection_id"
        ] is not None and self.adapter_registry.accepts_projection(
            document["extension"],
            document["projection_adapter_id"],
            document["projection_adapter_version"],
            document["projection_config_hash"],
        )
        if document["active_projection_id"] is not None and not adapter_current:
            reasons.append("outdated_adapter")
        if (
            adapter_current
            and source_observation_current
            and document.get("projection_can_continue")
            and callable(
                getattr(
                    self.adapter_registry.resolve(document["extension"]), "resume", None
                )
            )
        ):
            reasons.append("extraction_continuation")
        return ("refresh_required", reasons) if reasons else ("current", [])

    def inventory(
        self,
        corpus_id: str,
        *,
        path_contains: str | None = None,
        eligibility_state: str = "supported",
        residency_state: str = "all",
        index_state: str = "all",
        extension: str | None = None,
        max_logical_bytes: int | None = None,
        limit: int = CORPUS_INVENTORY_DEFAULT_LIMIT,
        offset: int = 0,
    ) -> dict:
        if eligibility_state not in _INVENTORY_ELIGIBILITY_STATES:
            raise ConfigurationError(
                "unsupported inventory eligibility state",
                details={
                    "eligibility_state": eligibility_state,
                    "allowed": sorted(_INVENTORY_ELIGIBILITY_STATES),
                },
            )
        if residency_state not in _INVENTORY_RESIDENCY_STATES:
            raise ConfigurationError(
                "unsupported inventory residency state",
                details={
                    "residency_state": residency_state,
                    "allowed": sorted(_INVENTORY_RESIDENCY_STATES),
                },
            )
        if index_state not in _INVENTORY_INDEX_STATES:
            raise ConfigurationError(
                "unsupported inventory index state",
                details={
                    "index_state": index_state,
                    "allowed": sorted(_INVENTORY_INDEX_STATES),
                },
            )
        if not 1 <= limit <= CORPUS_INVENTORY_MAX_LIMIT:
            raise BudgetExceededError(
                "inventory limit must be between 1 and 200",
                details={
                    "limit": limit,
                    "maximum": CORPUS_INVENTORY_MAX_LIMIT,
                },
            )
        if not 0 <= offset <= CORPUS_INVENTORY_MAX_OFFSET:
            raise BudgetExceededError(
                "inventory offset is outside the supported range",
                details={
                    "offset": offset,
                    "maximum": CORPUS_INVENTORY_MAX_OFFSET,
                },
            )
        if max_logical_bytes is not None and not (
            1 <= max_logical_bytes <= CORPUS_INVENTORY_MAX_LOGICAL_BYTES
        ):
            raise BudgetExceededError(
                "inventory maximum logical size is outside the supported range",
                details={
                    "max_logical_bytes": max_logical_bytes,
                    "maximum": CORPUS_INVENTORY_MAX_LOGICAL_BYTES,
                },
            )

        normalized_path_filter = _normalize_inventory_path_filter(path_contains)
        normalized_extension = _normalize_inventory_extension(extension)
        with corpus_read_connection(self.data_root, corpus_id) as connection:
            latest_scan = connection.execute(
                """
                SELECT scan_id, status, completed_at
                FROM scan_runs
                ORDER BY rowid DESC
                LIMIT 1
                """
            ).fetchone()
            rows = connection.execute(
                """
                SELECT d.document_id, d.relative_path, d.relative_path_nfc,
                       d.extension, d.media_type, d.logical_size, d.modified_ns,
                       d.residency_state, d.eligibility_state,
                       d.current_revision_id, d.device, d.inode, d.changed_ns,
                       r.source_size AS revision_source_size,
                       r.source_modified_ns AS revision_source_modified_ns,
                       r.source_changed_ns AS revision_source_changed_ns,
                       r.source_device AS revision_source_device,
                       r.source_inode AS revision_source_inode,
                       p.projection_id AS active_projection_id,
                       p.adapter_id AS projection_adapter_id,
                       p.adapter_version AS projection_adapter_version,
                       p.config_hash AS projection_config_hash,
                       EXISTS(SELECT 1 FROM extraction_issues progress
                           WHERE progress.projection_id = p.projection_id
                             AND progress.lifecycle_state = 'active'
                             AND progress.code IN ('pdf_page_range_pending', 'pdf_page_limit_reached')) AS projection_can_continue,
                       p.completeness_state AS projection_completeness
                FROM documents d
                LEFT JOIN revisions r ON r.revision_id = d.current_revision_id
                LEFT JOIN extraction_projections p
                  ON p.revision_id = d.current_revision_id AND p.is_active = 1
                WHERE d.deleted_at IS NULL
                ORDER BY d.relative_path_nfc COLLATE BINARY, d.document_id
                """
            ).fetchall()

        path_filter_key = (
            normalized_path_filter.casefold()
            if normalized_path_filter is not None
            else None
        )
        documents = []
        for row in rows:
            document = dict(row)
            if not _safe_relative_inventory_path(document["relative_path"]):
                raise ConfigurationError(
                    "inventory contains an unsafe relative document locator",
                    details={"document_id": document["document_id"]},
                )
            if (
                eligibility_state != "all"
                and document["eligibility_state"] != eligibility_state
            ):
                continue
            if (
                residency_state != "all"
                and document["residency_state"] != residency_state
            ):
                continue
            if normalized_extension is not None and (
                document["extension"] != normalized_extension
            ):
                continue
            if (
                max_logical_bytes is not None
                and document["logical_size"] > max_logical_bytes
            ):
                continue
            if path_filter_key is not None and path_filter_key not in (
                unicodedata.normalize(
                    "NFC",
                    document["relative_path_nfc"],
                ).casefold()
            ):
                continue

            document_index_state, refresh_reasons = self._document_index_state(document)
            if index_state != "all" and document_index_state != index_state:
                continue
            documents.append(
                {
                    "document_id": document["document_id"],
                    "relative_path": document["relative_path"],
                    "extension": document["extension"],
                    "media_type": document["media_type"],
                    "logical_size": document["logical_size"],
                    "modified_ns": document["modified_ns"],
                    "residency_state": document["residency_state"],
                    "eligibility_state": document["eligibility_state"],
                    "current_revision_id": document["current_revision_id"],
                    "active_projection_id": document["active_projection_id"],
                    "projection_completeness": document["projection_completeness"],
                    "index_state": document_index_state,
                    "refresh_reasons": refresh_reasons,
                }
            )

        total_matching = len(documents)
        page = documents[offset : offset + limit]
        next_offset = offset + len(page)
        has_more = next_offset < total_matching
        response = {
            "corpus_id": normalize_corpus_id(corpus_id),
            "observation": {
                "latest_scan_id": latest_scan["scan_id"] if latest_scan else None,
                "latest_scan_status": latest_scan["status"] if latest_scan else None,
                "scan_completed_at": latest_scan["completed_at"]
                if latest_scan
                else None,
                "inventory_complete": bool(
                    latest_scan is not None and latest_scan["status"] == "complete"
                ),
                "source_state": self._space_source_state(corpus_id),
            },
            "filters": {
                "path_contains": normalized_path_filter,
                "eligibility_state": eligibility_state,
                "residency_state": residency_state,
                "index_state": index_state,
                "extension": normalized_extension,
                "max_logical_bytes": max_logical_bytes,
            },
            "offset": offset,
            "limit": limit,
            "returned_count": len(page),
            "total_matching": total_matching,
            "has_more": has_more,
            "next_offset": next_offset if has_more else None,
            "documents": page,
            "metadata_only": True,
            "relevance_assessed": False,
            "notice": (
                "Inventory metadata supports exact document selection only. "
                "Filenames are untrusted metadata, not evidence of document content. "
                "A complete inventory does not establish content absence."
            ),
        }
        serialized_bytes = len(encode_json(response).encode())
        if serialized_bytes > CORPUS_INVENTORY_MAX_SERIALIZED_BYTES:
            raise BudgetExceededError(
                "inventory response exceeds the serialized response budget",
                details={
                    "requested_limit": limit,
                    "offset": offset,
                    "returned_document_count": len(page),
                    "serialized_bytes": serialized_bytes,
                    "maximum_serialized_bytes": (CORPUS_INVENTORY_MAX_SERIALIZED_BYTES),
                    "retry_with_lower": ["limit"],
                    "retry_with_narrower": [
                        "path_contains",
                        "eligibility_state",
                        "residency_state",
                        "index_state",
                        "extension",
                        "max_logical_bytes",
                    ],
                },
            )
        return response

    def _pending_documents(
        self,
        corpus_id: str,
        *,
        include_remote: bool,
        max_file_bytes: int,
        remote_only: bool = False,
        observed_scan_id: str | None = None,
    ) -> tuple[list[dict], dict]:
        with corpus_read_connection(self.data_root, corpus_id) as connection:
            rows = connection.execute(
                """
                SELECT d.*, r.source_size AS revision_source_size,
                       r.source_modified_ns AS revision_source_modified_ns,
                       r.source_changed_ns AS revision_source_changed_ns,
                       r.source_device AS revision_source_device,
                       r.source_inode AS revision_source_inode,
                       p.projection_id AS active_projection_id,
                       p.adapter_id AS projection_adapter_id,
                       p.adapter_version AS projection_adapter_version,
                       p.config_hash AS projection_config_hash,
                       EXISTS(SELECT 1 FROM extraction_issues progress
                           WHERE progress.projection_id = p.projection_id
                             AND progress.lifecycle_state = 'active'
                             AND progress.code IN ('pdf_page_range_pending', 'pdf_page_limit_reached')) AS projection_can_continue,
                       failed.adapter_id AS failed_adapter_id,
                       failed.adapter_version AS failed_adapter_version,
                       failed.config_hash AS failed_config_hash
                FROM documents d
                LEFT JOIN revisions r ON r.revision_id = d.current_revision_id
                LEFT JOIN extraction_projections p
                  ON p.revision_id = d.current_revision_id AND p.is_active = 1
                LEFT JOIN extraction_attempts failed
                  ON failed.attempt_id = (
                    SELECT candidate.attempt_id
                    FROM extraction_attempts candidate
                    WHERE candidate.revision_id = d.current_revision_id
                      AND candidate.state = 'failed'
                    ORDER BY candidate.completed_at DESC, candidate.attempt_id DESC
                    LIMIT 1
                  )
                WHERE d.deleted_at IS NULL
                  AND d.eligibility_state = 'supported'
                  AND (? IS NULL OR d.last_seen_scan_id = ?)
                ORDER BY d.is_dataless ASC, d.logical_size ASC, d.relative_path_nfc ASC
                """,
                (observed_scan_id, observed_scan_id),
            ).fetchall()
        pending: list[dict] = []
        skipped = {
            "current": 0,
            "remote": 0,
            "local": 0,
            "too_large": 0,
            "not_selected": 0,
            "failed": 0,
        }
        for row in rows:
            document = dict(row)
            document_index_state, reasons = self._document_index_state(document)
            if document_index_state == "current":
                skipped["current"] += 1
                continue
            descriptor = self.adapter_registry.resolve(document["extension"]).descriptor
            if (
                "source_observation_changed" not in reasons
                and document.get("failed_adapter_id") == descriptor.adapter_id
                and document.get("failed_adapter_version") == descriptor.adapter_version
                and document.get("failed_config_hash") == descriptor.config_hash
            ):
                skipped["failed"] += 1
                continue
            if document["logical_size"] > max_file_bytes:
                skipped["too_large"] += 1
                continue
            if remote_only and not document["is_dataless"]:
                skipped["local"] += 1
                continue
            if document["is_dataless"] and not include_remote:
                skipped["remote"] += 1
                continue
            pending.append(document)
        return pending, skipped

    @staticmethod
    def _safe_selection_extension(document: dict) -> str | None:
        extension = document.get("extension")
        if not isinstance(extension, str):
            return None
        normalized = extension.strip().lower().removeprefix(".")
        if not normalized:
            return ""
        if len(normalized) > CORPUS_INVENTORY_MAX_EXTENSION_CHARS or not all(
            character.isascii() and character.isalnum() for character in normalized
        ):
            return None
        return normalized

    def _exact_document_candidates(
        self,
        corpus_id: str,
        *,
        document_ids: list[str],
        include_remote: bool,
        max_file_bytes: int,
        remote_only: bool,
    ) -> tuple[list[dict], dict[str, dict], dict]:
        placeholders = ",".join("?" for _ in document_ids)
        with corpus_read_connection(self.data_root, corpus_id) as connection:
            rows = connection.execute(
                f"""
                SELECT d.*, r.source_size AS revision_source_size,
                       r.source_modified_ns AS revision_source_modified_ns,
                       r.source_changed_ns AS revision_source_changed_ns,
                       r.source_device AS revision_source_device,
                       r.source_inode AS revision_source_inode,
                       p.projection_id AS active_projection_id,
                       p.adapter_id AS projection_adapter_id,
                       p.adapter_version AS projection_adapter_version,
                       p.config_hash AS projection_config_hash,
                       EXISTS(SELECT 1 FROM extraction_issues progress
                           WHERE progress.projection_id = p.projection_id
                             AND progress.lifecycle_state = 'active'
                             AND progress.code IN ('pdf_page_range_pending', 'pdf_page_limit_reached')) AS projection_can_continue
                FROM documents d
                LEFT JOIN revisions r ON r.revision_id = d.current_revision_id
                LEFT JOIN extraction_projections p
                  ON p.revision_id = d.current_revision_id AND p.is_active = 1
                WHERE d.document_id IN ({placeholders})
                """,
                document_ids,
            ).fetchall()
        documents = {row["document_id"]: dict(row) for row in rows}
        candidates: list[dict] = []
        outcomes: dict[str, dict] = {}
        skipped = {
            "current": 0,
            "remote": 0,
            "local": 0,
            "too_large": 0,
            "not_selected": 0,
            "unknown": 0,
            "deleted": 0,
            "unsupported": 0,
            "max_files_deferred": 0,
            "max_bytes_deferred": 0,
        }

        def record_outcome(
            document_id: str,
            outcome: str,
            document: dict | None = None,
        ) -> None:
            item: dict[str, object] = {
                "document_id": document_id,
                "outcome": outcome,
            }
            if document is not None:
                extension = self._safe_selection_extension(document)
                if extension is not None:
                    item["extension"] = extension
            outcomes[document_id] = item

        for document_id in document_ids:
            document = documents.get(document_id)
            if document is None:
                skipped["unknown"] += 1
                record_outcome(document_id, "unknown")
                continue
            if document["deleted_at"] is not None:
                skipped["deleted"] += 1
                record_outcome(document_id, "deleted", document)
                continue
            if document["eligibility_state"] != "supported":
                skipped["unsupported"] += 1
                record_outcome(document_id, "unsupported", document)
                continue
            document_index_state, _ = self._document_index_state(document)
            if document_index_state == "current":
                skipped["current"] += 1
                record_outcome(document_id, "current", document)
                continue
            if document["logical_size"] > max_file_bytes:
                skipped["too_large"] += 1
                record_outcome(document_id, "too_large", document)
                continue
            if remote_only and not document["is_dataless"]:
                skipped["local"] += 1
                record_outcome(document_id, "remote_disallowed", document)
                continue
            if document["is_dataless"] and not include_remote:
                skipped["remote"] += 1
                record_outcome(document_id, "remote_disallowed", document)
                continue
            candidates.append(document)
        return candidates, outcomes, skipped

    def _pending_state_summary(
        self,
        corpus_id: str,
        *,
        max_file_bytes: int,
        include_remote: bool,
        observed_scan_id: str | None = None,
    ) -> dict:
        with corpus_read_connection(self.data_root, corpus_id) as connection:
            rows = connection.execute(
                """
                SELECT d.*, r.source_size AS revision_source_size,
                       r.source_modified_ns AS revision_source_modified_ns,
                       r.source_changed_ns AS revision_source_changed_ns,
                       r.source_device AS revision_source_device,
                       r.source_inode AS revision_source_inode,
                       p.projection_id AS active_projection_id,
                       p.adapter_id AS projection_adapter_id,
                       p.adapter_version AS projection_adapter_version,
                       p.config_hash AS projection_config_hash,
                       EXISTS(SELECT 1 FROM extraction_issues progress
                           WHERE progress.projection_id = p.projection_id
                             AND progress.lifecycle_state = 'active'
                             AND progress.code IN ('pdf_page_range_pending', 'pdf_page_limit_reached')) AS projection_can_continue,
                       failed.adapter_id AS failed_adapter_id,
                       failed.adapter_version AS failed_adapter_version,
                       failed.config_hash AS failed_config_hash
                FROM documents d
                LEFT JOIN revisions r ON r.revision_id = d.current_revision_id
                LEFT JOIN extraction_projections p
                  ON p.revision_id = d.current_revision_id AND p.is_active = 1
                LEFT JOIN extraction_attempts failed
                  ON failed.attempt_id = (
                    SELECT candidate.attempt_id
                    FROM extraction_attempts candidate
                    WHERE candidate.revision_id = d.current_revision_id
                      AND candidate.state = 'failed'
                    ORDER BY candidate.completed_at DESC, candidate.attempt_id DESC
                    LIMIT 1
                  )
                WHERE d.deleted_at IS NULL
                  AND d.eligibility_state = 'supported'
                  AND (? IS NULL OR d.last_seen_scan_id = ?)
                """,
                (observed_scan_id, observed_scan_id),
            ).fetchall()
        result = {
            "remaining": 0,
            "refreshable": 0,
            "pending_remote": 0,
            "too_large": 0,
            "failed": 0,
            "coverage_gaps": 0,
        }
        outdated = {
            "total": 0,
            "refreshable": 0,
            "too_large": 0,
            "pending_remote": 0,
            "failed": 0,
        }
        for row in rows:
            document = dict(row)
            document_index_state, reasons = self._document_index_state(document)
            if document_index_state == "current":
                continue
            descriptor = self.adapter_registry.resolve(document["extension"]).descriptor
            failed = (
                "source_observation_changed" not in reasons
                and document.get("failed_adapter_id") == descriptor.adapter_id
                and document.get("failed_adapter_version") == descriptor.adapter_version
                and document.get("failed_config_hash") == descriptor.config_hash
            )
            if (
                "outdated_adapter" in reasons
                and "source_observation_changed" not in reasons
            ):
                category = (
                    "too_large"
                    if document["logical_size"] > max_file_bytes
                    else "pending_remote"
                    if document["is_dataless"] and not include_remote
                    else "failed"
                    if failed
                    else "refreshable"
                )
                outdated["total"] += 1
                outdated[category] += 1
            if document["logical_size"] > max_file_bytes:
                result["too_large"] += 1
                result["coverage_gaps"] += 1
                continue
            if document["is_dataless"] and not include_remote:
                result["pending_remote"] += 1
                result["coverage_gaps"] += 1
                continue
            if failed:
                result["failed"] += 1
                result["coverage_gaps"] += 1
                continue
            result["refreshable"] += 1
            result["remaining"] += 1
        result["outdated"] = outdated
        return result

    def _ingest_locked(
        self,
        *,
        corpus: dict,
        paths: RuntimePaths,
        max_files: int,
        max_bytes: int,
        max_file_bytes: int,
        include_remote: bool,
        remote_only: bool,
        document_ids: list[str] | None,
        timeout_seconds: float,
        observed_scan_id: str | None = None,
    ) -> dict:
        corpus_id = corpus["corpus_id"]
        abandoned_staging_cleanup = cleanup_abandoned_staging(paths)
        exact_selection = document_ids is not None
        if document_ids is None:
            pending, skipped = self._pending_documents(
                corpus_id,
                include_remote=include_remote,
                max_file_bytes=max_file_bytes,
                remote_only=remote_only,
                observed_scan_id=observed_scan_id,
            )
            outcome_by_id: dict[str, dict] = {}
        else:
            pending, outcome_by_id, skipped = self._exact_document_candidates(
                corpus_id,
                document_ids=document_ids,
                include_remote=include_remote,
                max_file_bytes=max_file_bytes,
                remote_only=remote_only,
            )
        selected: list[dict] = []
        selected_bytes = 0
        for document in pending:
            if len(selected) >= max_files:
                if exact_selection:
                    skipped["max_files_deferred"] += 1
                    deferred = {
                        "document_id": document["document_id"],
                        "outcome": "max_files_deferred",
                    }
                    extension = self._safe_selection_extension(document)
                    if extension is not None:
                        deferred["extension"] = extension
                    outcome_by_id[document["document_id"]] = deferred
                    continue
                break
            if selected_bytes + document["logical_size"] > max_bytes:
                if exact_selection:
                    skipped["max_bytes_deferred"] += 1
                    deferred = {
                        "document_id": document["document_id"],
                        "outcome": "max_bytes_deferred",
                    }
                    extension = self._safe_selection_extension(document)
                    if extension is not None:
                        deferred["extension"] = extension
                    outcome_by_id[document["document_id"]] = deferred
                continue
            selected.append(document)
            selected_bytes += document["logical_size"]

        attempted_results: list[dict] = []
        for document in selected:
            try:
                result = self._ingest_document(
                    corpus=corpus,
                    document=document,
                    allow_hydration=include_remote,
                    maximum_bytes=document["logical_size"],
                    timeout_seconds=timeout_seconds,
                )
            # Preserve per-document failure isolation for batch refreshes.
            except Exception as exc:  # noqa: BLE001
                result = {
                    "document_id": document["document_id"],
                    "relative_path": document["relative_path"],
                    "state": "failed",
                    "error_code": getattr(exc, "code", "unexpected_error"),
                    "message": str(exc),
                    "details": getattr(exc, "details", {}),
                }
                cleanup_failure = getattr(exc, "source_copy_cleanup", None)
                if isinstance(cleanup_failure, dict):
                    result["source_copy_retention"] = "cleanup_failed"
                    result["source_copy_cleanup"] = cleanup_failure
            result["outcome"] = (
                "refreshed"
                if result["state"] in {"indexed", "already_indexed"}
                else "selected"
            )
            attempted_results.append(result)
            if exact_selection:
                outcome_by_id[document["document_id"]] = result
        results = (
            [outcome_by_id[document_id] for document_id in document_ids]
            if document_ids is not None
            else attempted_results
        )
        return {
            "policy": {
                "include_remote": include_remote,
                "remote_only": remote_only,
                "selection_mode": "exact" if exact_selection else "automatic",
                "document_ids": document_ids or [],
                "max_files": max_files,
                "max_bytes": max_bytes,
                "max_file_bytes": max_file_bytes,
                "timeout_seconds": timeout_seconds,
                "concurrency": 1,
                "abandoned_staging_cleanup": abandoned_staging_cleanup,
            },
            "selected_files": len(selected),
            "selected_logical_bytes": selected_bytes,
            "skipped": skipped,
            "results": results,
            "summary": {
                **{
                    state: sum(
                        1 for result in attempted_results if result["state"] == state
                    )
                    for state in ("indexed", "already_indexed", "failed")
                },
                "source_copy_cleanup_failed": sum(
                    1
                    for result in attempted_results
                    if result.get("source_copy_cleanup", {}).get("state") == "failed"
                ),
            },
        }

    def ingest(
        self,
        corpus_id: str,
        *,
        max_files: int = 10,
        max_bytes: int = 50 * 1024 * 1024,
        max_file_bytes: int = 25 * 1024 * 1024,
        include_remote: bool = False,
        remote_only: bool = False,
        document_ids: list[str] | None = None,
        timeout_seconds: float = 120,
    ) -> dict:
        corpus_id = normalize_corpus_id(corpus_id)
        corpus = get_corpus(self.data_root, corpus_id)
        _validate_ingest_budgets(
            max_files=max_files,
            max_bytes=max_bytes,
            max_file_bytes=max_file_bytes,
            timeout_seconds=timeout_seconds,
            exact_selection=document_ids is not None and not include_remote,
        )
        if document_ids is not None and not document_ids:
            raise InvalidRequestError(
                "explicit ingest document selection must not be empty",
                details={"minimum_document_ids": 1},
            )
        if document_ids is not None and (
            len(document_ids) > _MAX_INGEST_DOCUMENT_IDS
            or any(
                not isinstance(document_id, str)
                or not document_id
                or len(document_id) > 200
                for document_id in document_ids
            )
        ):
            raise BudgetExceededError(
                "ingest document selection exceeds the supported request bounds",
                details={
                    "document_id_count": len(document_ids),
                    "max_document_ids": _MAX_INGEST_DOCUMENT_IDS,
                    "max_document_id_chars": 200,
                },
            )
        if document_ids is not None and len(set(document_ids)) != len(document_ids):
            raise InvalidRequestError(
                "ingest document ids must be unique",
                details={"reason": "duplicate_document_ids"},
            )
        if remote_only and not include_remote:
            raise BudgetExceededError(
                "remote-only selection requires explicit --include-remote",
                details={"remote_only": True, "include_remote": False},
            )
        paths = self._paths(corpus_id)
        paths.ensure()
        with writer_lock(paths.corpus_root / "writer.lock"):
            result = self._ingest_locked(
                corpus=corpus,
                paths=paths,
                max_files=max_files,
                max_bytes=max_bytes,
                max_file_bytes=max_file_bytes,
                include_remote=include_remote,
                remote_only=remote_only,
                document_ids=document_ids,
                timeout_seconds=timeout_seconds,
            )
            self._prune_corpus_history_locked(corpus_id)
        self._reconcile_workspace_index_changes(corpus_id)
        result["source_state"] = self._space_source_state(corpus_id)
        return {
            "corpus_id": corpus_id,
            **result,
        }

    def _validate_sync_boundary(self, corpus: dict) -> RuntimePaths:
        source_root = Path(corpus["source_root"]).expanduser().resolve(strict=False)
        paths = self._paths(corpus["corpus_id"])
        runtime_roots = {
            "data_root": self.data_root.expanduser().resolve(strict=False),
            "staging_root": paths.staging.expanduser().resolve(strict=False),
        }
        overlaps = [
            name
            for name, runtime_root in runtime_roots.items()
            if is_within(runtime_root, source_root)
            or is_within(source_root, runtime_root)
        ]
        if overlaps:
            raise SourceBoundaryError(
                "runtime data and source roots must not overlap",
                details={
                    "source_root": str(source_root),
                    "runtime_roots": {
                        name: str(runtime_roots[name]) for name in overlaps
                    },
                },
            )
        return paths

    def sync(
        self,
        corpus_id: str,
        *,
        max_files: int = 10,
        max_bytes: int = 50 * 1024 * 1024,
        max_file_bytes: int = 25 * 1024 * 1024,
        include_remote: bool = False,
        timeout_seconds: float = 120,
    ) -> dict:
        """Scan metadata and refresh only documents whose source index is pending."""

        _validate_ingest_budgets(
            max_files=max_files,
            max_bytes=max_bytes,
            max_file_bytes=max_file_bytes,
            timeout_seconds=timeout_seconds,
            exact_selection=False,
        )
        corpus_id = normalize_corpus_id(corpus_id)
        corpus = get_corpus(self.data_root, corpus_id)
        paths = self._validate_sync_boundary(corpus)
        paths.ensure()
        with writer_lock(paths.corpus_root / "writer.lock"):
            scan = dict(scan_corpus(self.data_root, corpus_id))
            inventory_complete = bool(scan.get("observation_complete"))
            observed_scan_id = None if inventory_complete else scan["scan_id"]
            ingested = self._ingest_locked(
                corpus=corpus,
                paths=paths,
                max_files=max_files,
                max_bytes=max_bytes,
                max_file_bytes=max_file_bytes,
                include_remote=include_remote,
                remote_only=False,
                document_ids=None,
                timeout_seconds=timeout_seconds,
                observed_scan_id=observed_scan_id,
            )
            pending_state = self._pending_state_summary(
                corpus_id,
                max_file_bytes=max_file_bytes,
                include_remote=include_remote,
                observed_scan_id=observed_scan_id,
            )
            self._prune_corpus_history_locked(corpus_id)
        self._reconcile_workspace_index_changes(corpus_id)
        scan.pop("source_root", None)
        change_counts = dict(scan.get("change_counts", {}))
        inventory = {
            "scan_id": scan["scan_id"],
            "inventory_complete": inventory_complete,
            "directories": scan["directories"],
            "files": scan["files"],
            "dataless_files": scan["dataless_files"],
            "logical_bytes": scan["logical_bytes"],
            "supported_files": int(
                scan.get("eligibility_counts", {}).get("supported", 0)
            ),
            "completeness_failure_count": scan["completeness_failure_count"],
            "changed_documents": int(scan.get("changed_documents", 0)),
            "change_counts": change_counts,
        }
        policy = {
            "include_remote": include_remote,
            "max_files": max_files,
            "max_bytes": max_bytes,
            "max_file_bytes": max_file_bytes,
            "timeout_seconds": timeout_seconds,
            "concurrency": 1,
        }
        refreshable = int(pending_state["refreshable"])
        pending_remote = int(pending_state["pending_remote"])
        too_large = int(pending_state["too_large"])
        coverage_gaps = int(pending_state["coverage_gaps"])
        remaining = int(pending_state["remaining"])
        refresh_summary = ingested["summary"]
        failed = int(pending_state["failed"])
        if remaining:
            state = "pending"
        elif not inventory_complete or coverage_gaps:
            state = "partial"
        else:
            state = "complete"
        summary = {
            "added": int(change_counts.get("added", 0)),
            "changed": int(scan.get("changed_documents", 0)),
            "reappeared": int(change_counts.get("reappeared", 0)),
            "deleted": int(change_counts.get("deleted", 0)),
            "indexed": int(refresh_summary["indexed"]),
            "reused": int(refresh_summary["already_indexed"]),
            "failed": failed,
            "pending_remote": pending_remote,
            "too_large": too_large,
            "remaining": remaining,
            "coverage_gaps": coverage_gaps,
        }
        return {
            "corpus_id": corpus_id,
            "state": state,
            "policy": policy,
            "inventory": inventory,
            "refresh": {
                "state": (
                    "completed"
                    if int(refresh_summary["failed"]) == 0
                    else "completed_with_failures"
                ),
                "selected_files": ingested["selected_files"],
                "selected_logical_bytes": ingested["selected_logical_bytes"],
                "skipped": ingested["skipped"],
                "results": ingested["results"],
                "source_copy_cleanup_failed": refresh_summary[
                    "source_copy_cleanup_failed"
                ],
                "abandoned_staging_cleanup": ingested["policy"][
                    "abandoned_staging_cleanup"
                ],
            },
            "pending": {
                "remaining": remaining,
                "refreshable": refreshable,
                "pending_remote": pending_remote,
                "too_large": too_large,
                "failed": failed,
                "coverage_gaps": coverage_gaps,
                "outdated": pending_state["outdated"],
            },
            "summary": summary,
            "source_state": self._space_source_state(corpus_id),
        }

    def _ingest_document(
        self,
        *,
        corpus: dict,
        document: dict,
        allow_hydration: bool,
        maximum_bytes: int,
        timeout_seconds: float,
    ) -> dict:
        source_root = Path(corpus["source_root"])
        source = Path(document["absolute_path"])
        paths = self._paths(corpus["corpus_id"])
        scanned_key = (
            document["logical_size"],
            document["modified_ns"],
            document["changed_ns"],
            document["device"],
            document["inode"],
        )
        try:
            captured = capture_to_staging(
                paths=paths,
                source_root=source_root,
                source=source,
                allow_hydration=allow_hydration,
                maximum_bytes=maximum_bytes,
                timeout_seconds=timeout_seconds,
                expected_source_identity=scanned_key,
            )
        except SourceBoundaryError as exc:
            if not _source_is_missing(exc):
                raise
            raise SourceUnavailableError(
                "source bytes are unavailable; refresh requires the registered original",
                details={
                    "document_id": document["document_id"],
                    "relative_path": document["relative_path"],
                    "existing_sqlite_index_unchanged": bool(
                        document.get("current_revision_id")
                    ),
                    "source_error": exc.details,
                },
            ) from exc

        result: dict | None = None
        primary_error: BaseException | None = None
        try:
            revision_id = _revision_id(document["document_id"], captured.sha256)
            capture_ref = _ephemeral_capture_ref(captured.sha256)
            adapter = self.adapter_registry.resolve(document["extension"])
            descriptor = adapter.descriptor
            previous = None

            with corpus_connection(self.data_root, corpus["corpus_id"]) as connection:
                existing = connection.execute(
                    """
                    SELECT p.projection_id, p.adapter_id, p.adapter_version, p.config_hash
                    FROM revisions r
                    LEFT JOIN extraction_projections p
                      ON p.revision_id = r.revision_id AND p.is_active = 1
                    WHERE r.revision_id = ?
                    """,
                    (revision_id,),
                ).fetchone()
                if (
                    existing
                    and existing["projection_id"] is not None
                    and existing["adapter_id"] == descriptor.adapter_id
                    and existing["adapter_version"] == descriptor.adapter_version
                    and existing["config_hash"] == descriptor.config_hash
                ):
                    if callable(getattr(adapter, "resume", None)):
                        previous = self._continuation_envelope(
                            connection, existing["projection_id"], descriptor
                        )
                    if previous is None:
                        self._reactivate_existing_projection(
                            connection,
                            document=document,
                            revision_id=revision_id,
                            captured=captured,
                            blob_ref=capture_ref,
                        )
                        result = {
                            "document_id": document["document_id"],
                            "relative_path": document["relative_path"],
                            "revision_id": revision_id,
                            "projection_id": existing["projection_id"],
                            "sha256": captured.sha256,
                            "state": "already_indexed",
                            "hydrated": captured.hydration_was_required,
                            "source_copy_retention": "ephemeral",
                            "source_copy_cleanup": {"state": "deleted"},
                        }
                        return result

            try:
                extraction = (
                    adapter.resume(
                        captured.capture_path,
                        format_id=document["extension"],
                        previous=previous,
                    )
                    if previous is not None
                    else adapter.extract(
                        captured.capture_path, format_id=document["extension"]
                    )
                )
            except (ExtractionError, BudgetExceededError) as exc:
                self._record_failed_extraction(
                    corpus_id=corpus["corpus_id"],
                    document=document,
                    captured=captured,
                    revision_id=revision_id,
                    blob_ref=capture_ref,
                    descriptor=descriptor,
                    error=exc,
                )
                raise

            committed = self._commit_extraction(
                corpus_id=corpus["corpus_id"],
                document=document,
                captured=captured,
                revision_id=revision_id,
                blob_ref=capture_ref,
                extraction=extraction,
            )
            result = {
                "document_id": document["document_id"],
                "relative_path": document["relative_path"],
                "revision_id": revision_id,
                "projection_id": committed["projection_id"],
                "sha256": captured.sha256,
                "state": "indexed",
                "source_units": committed["source_units"],
                "completeness_state": committed["completeness_state"],
                "hydrated": captured.hydration_was_required,
                "native_capture": captured.used_native_helper,
                "bytes_copied": captured.bytes_copied,
                "source_copy_retention": "ephemeral",
                "source_copy_cleanup": {"state": "deleted"},
                "extraction_issues": committed["extraction_issues"],
            }
            return result
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            try:
                discard_staged_capture(paths, captured)
            except Exception as exc:
                cleanup_failure = {
                    "state": "failed",
                    "error_code": getattr(exc, "code", "unexpected_error"),
                    "message": str(exc),
                    "details": getattr(exc, "details", {}),
                }
                if result is not None:
                    result["source_copy_retention"] = "cleanup_failed"
                    result["source_copy_cleanup"] = cleanup_failure
                elif primary_error is not None:
                    primary_error.source_copy_cleanup = cleanup_failure
                else:
                    raise

    @staticmethod
    def _continuation_envelope(connection, projection_id, descriptor):
        """Give a resumable adapter neutral current data, never core IDs or paths."""
        raw_issues = [
            json.loads(row["details_json"])
            for row in connection.execute(
                """SELECT details_json FROM extraction_issues
               WHERE projection_id = ? AND lifecycle_state = 'active'
                 AND json_extract(details_json, '$.details.unit_ordinal') IS NULL
               ORDER BY rowid""",
                (projection_id,),
            )
        ]
        if not any(i.get("code") == "pdf_page_range_pending" for i in raw_issues):
            return None
        issues = tuple(
            ExtractionIssue(
                **{
                    k: value
                    for k, value in issue.items()
                    if k in {"code", "message", "severity", "details"}
                }
            )
            for issue in raw_issues
        )
        units = []
        for row in connection.execute(
            "SELECT * FROM source_units WHERE projection_id = ? ORDER BY ordinal",
            (projection_id,),
        ):
            units.append(
                ExtractedUnit(
                    unit_type=row["unit_type"],
                    structure_path=json.loads(row["structure_path_json"]),
                    content=row["normalized_content"],
                    derivation_method=row["derivation_method"],
                    geometry=json.loads(row["geometry_json"]),
                    confidence=row["confidence"],
                    quality_flags=tuple(json.loads(row["quality_flags_json"])),
                    issues=tuple(
                        ExtractionIssue(**i)
                        for i in json.loads(row["extraction_issues_json"])
                    ),
                )
            )
        return ExtractionEnvelope.create(
            descriptor=descriptor, completeness="partial", units=units, issues=issues
        )

    def _set_document_current(
        self,
        connection,
        document: dict,
        revision_id: str,
        captured: CapturedSource,
    ) -> None:
        post_identity = captured.post_identity
        connection.execute(
            """
            UPDATE documents SET
                current_revision_id = ?, logical_size = ?, modified_ns = ?,
                changed_ns = ?, device = ?, inode = ?, mode = ?, flags = ?,
                is_dataless = ?, residency_state = ?, allocated_size = ?,
                last_seen_at = ?
            WHERE document_id = ?
            """,
            (
                revision_id,
                post_identity.size,
                post_identity.modified_ns,
                post_identity.changed_ns,
                post_identity.device,
                post_identity.inode,
                post_identity.mode,
                post_identity.flags,
                int(post_identity.dataless),
                "remote_only" if post_identity.dataless else "resident",
                post_identity.allocated_size,
                utc_now(),
                document["document_id"],
            ),
        )

    def _refresh_revision_observation(
        self,
        connection,
        *,
        revision_id: str,
        captured: CapturedSource,
        blob_ref: str,
    ) -> None:
        now = utc_now()
        connection.execute(
            """
            UPDATE revisions SET
                immutable_blob_ref = ?,
                source_size = ?,
                source_modified_ns = ?,
                source_changed_ns = ?,
                source_device = ?,
                source_inode = ?,
                observed_at = ?,
                captured_at = ?
            WHERE revision_id = ?
            """,
            (
                blob_ref,
                captured.post_identity.size,
                captured.post_identity.modified_ns,
                captured.post_identity.changed_ns,
                captured.post_identity.device,
                captured.post_identity.inode,
                now,
                now,
                revision_id,
            ),
        )

    def _reactivate_existing_projection(
        self,
        connection,
        *,
        document: dict,
        revision_id: str,
        captured: CapturedSource,
        blob_ref: str,
    ) -> None:
        self._refresh_revision_observation(
            connection,
            revision_id=revision_id,
            captured=captured,
            blob_ref=blob_ref,
        )
        self._set_document_current(connection, document, revision_id, captured)

    def _insert_revision(
        self,
        connection,
        *,
        document: dict,
        captured: CapturedSource,
        revision_id: str,
        blob_ref: str,
        extraction_state: str,
        extractor_version: str,
    ) -> str | None:
        predecessor = document.get("current_revision_id")
        now = utc_now()
        connection.execute(
            """
            INSERT INTO revisions(
                revision_id, document_id, sha256, immutable_blob_ref,
                source_size, source_modified_ns, source_changed_ns,
                source_device, source_inode,
                observed_at, captured_at, extraction_state, extractor_version,
                predecessor_revision_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(revision_id) DO UPDATE SET
                immutable_blob_ref = excluded.immutable_blob_ref,
                source_size = excluded.source_size,
                source_modified_ns = excluded.source_modified_ns,
                source_changed_ns = excluded.source_changed_ns,
                source_device = excluded.source_device,
                source_inode = excluded.source_inode,
                observed_at = excluded.observed_at,
                captured_at = excluded.captured_at,
                extraction_state = CASE
                    WHEN revisions.extraction_state = 'complete'
                         AND excluded.extraction_state = 'failed'
                    THEN revisions.extraction_state
                    ELSE excluded.extraction_state
                END,
                extractor_version = CASE
                    WHEN revisions.extraction_state = 'complete'
                         AND excluded.extraction_state = 'failed'
                    THEN revisions.extractor_version
                    ELSE excluded.extractor_version
                END
            """,
            (
                revision_id,
                document["document_id"],
                captured.sha256,
                blob_ref,
                captured.post_identity.size,
                captured.post_identity.modified_ns,
                captured.post_identity.changed_ns,
                captured.post_identity.device,
                captured.post_identity.inode,
                now,
                now,
                extraction_state,
                extractor_version,
                predecessor if predecessor != revision_id else None,
            ),
        )
        self._set_document_current(connection, document, revision_id, captured)
        return predecessor

    def _record_failed_extraction(
        self,
        *,
        corpus_id: str,
        document: dict,
        captured: CapturedSource,
        revision_id: str,
        blob_ref: str,
        descriptor: AdapterDescriptor,
        error: ExtractionError,
    ) -> None:
        with corpus_connection(self.data_root, corpus_id) as connection:
            self._insert_revision(
                connection,
                document=document,
                captured=captured,
                revision_id=revision_id,
                blob_ref=blob_ref,
                extraction_state="failed",
                extractor_version=descriptor.adapter_version,
            )
            now = utc_now()
            attempt_id = f"attempt_{uuid.uuid4().hex}"
            connection.execute(
                """
                INSERT INTO extraction_attempts(
                    attempt_id, revision_id, adapter_id, adapter_version,
                    config_hash, state, error_json, started_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, 'failed', ?, ?, ?)
                """,
                (
                    attempt_id,
                    revision_id,
                    descriptor.adapter_id,
                    descriptor.adapter_version,
                    descriptor.config_hash,
                    encode_json(
                        {
                            "code": error.code,
                            "message": str(error),
                            "details": error.details,
                        }
                    ),
                    now,
                    now,
                ),
            )
            structural_locator, locator_key = _issue_locator(error.details)
            connection.execute(
                """
                INSERT INTO extraction_issues(
                    issue_id, document_id, revision_id, attempt_id,
                    stage, severity, code, message, details_json,
                    structural_locator_json, locator_key, lifecycle_state, created_at
                ) VALUES (?, ?, ?, ?, 'extract', 'error', ?, ?, ?, ?, ?, 'active', ?)
                """,
                (
                    f"issue_{uuid.uuid4().hex}",
                    document["document_id"],
                    revision_id,
                    attempt_id,
                    error.code,
                    str(error),
                    encode_json(error.details),
                    encode_json(structural_locator),
                    locator_key,
                    now,
                ),
            )

    def _commit_extraction(
        self,
        *,
        corpus_id: str,
        document: dict,
        captured: CapturedSource,
        revision_id: str,
        blob_ref: str,
        extraction: ExtractionEnvelope,
    ) -> dict:
        descriptor = extraction.descriptor
        adapter_id = descriptor.adapter_id
        result_manifest_hash = extraction.manifest_hash
        projection_id = _projection_id(
            revision_id,
            adapter_id,
            descriptor.adapter_version,
            descriptor.config_hash,
            result_manifest_hash,
        )
        completeness_state = extraction.completeness
        capability_manifest = descriptor.capabilities.to_dict()
        content_hashes = [
            hashlib.sha256(unit.content.encode("utf-8")).hexdigest()
            for unit in extraction.units
        ]
        unit_ids = [
            _unit_id(projection_id, ordinal, content_hash)
            for ordinal, content_hash in enumerate(content_hashes, start=1)
        ]
        with corpus_connection(self.data_root, corpus_id) as connection:
            self._insert_revision(
                connection,
                document=document,
                captured=captured,
                revision_id=revision_id,
                blob_ref=blob_ref,
                extraction_state="complete",
                extractor_version=descriptor.adapter_version,
            )
            now = utc_now()
            attempt_id = f"attempt_{uuid.uuid4().hex}"
            connection.execute(
                """
                INSERT INTO extraction_attempts(
                    attempt_id, revision_id, adapter_id, adapter_version,
                    config_hash, state, started_at
                ) VALUES (?, ?, ?, ?, ?, 'running', ?)
                """,
                (
                    attempt_id,
                    revision_id,
                    adapter_id,
                    descriptor.adapter_version,
                    descriptor.config_hash,
                    now,
                ),
            )
            old_projection_ids = [
                row["projection_id"]
                for row in connection.execute(
                    """
                    SELECT projection_id
                    FROM extraction_projections
                    WHERE revision_id = ? AND is_active = 1 AND projection_id != ?
                    """,
                    (revision_id, projection_id),
                )
            ]
            existing_projection = connection.execute(
                """
                SELECT projection_id
                FROM extraction_projections
                WHERE projection_id = ?
                """,
                (projection_id,),
            ).fetchone()
            if existing_projection is None:
                connection.execute(
                    """
                    INSERT INTO extraction_projections(
                        projection_id, revision_id, adapter_id, adapter_version,
                        config_hash, result_manifest_hash, completeness_state,
                        capability_manifest_json, assurance_state, is_active, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'declared', 0, ?)
                    """,
                    (
                        projection_id,
                        revision_id,
                        adapter_id,
                        descriptor.adapter_version,
                        descriptor.config_hash,
                        result_manifest_hash,
                        completeness_state,
                        encode_json(capability_manifest),
                        now,
                    ),
                )

                for index, unit in enumerate(extraction.units):
                    ordinal = index + 1
                    unit_id = unit_ids[index]
                    unit_payload = unit.to_dict()
                    structure = unit_payload["structure_path"]
                    anchor = {
                        "schema_version": 2,
                        "extraction_schema_version": EXTRACTION_SCHEMA_VERSION,
                        "document_id": document["document_id"],
                        "revision_id": revision_id,
                        "projection_id": projection_id,
                        "content_hash": captured.sha256,
                        "canonical_locator": document["relative_path"],
                        "absolute_path": document["absolute_path"],
                        "structural_locator": structure,
                        "source_span": _source_span(structure),
                        "surface_open_target": document["absolute_path"],
                    }
                    previous_unit_id = unit_ids[index - 1] if index > 0 else None
                    next_unit_id = (
                        unit_ids[index + 1] if index + 1 < len(unit_ids) else None
                    )
                    connection.execute(
                        """
                        INSERT INTO source_units(
                            unit_id, revision_id, projection_id, ordinal, unit_type,
                            structure_path_json, source_anchor_json, normalized_content,
                            content_sha256, previous_unit_id, next_unit_id,
                            extraction_issues_json, derivation_method, geometry_json,
                            confidence, quality_flags_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            unit_id,
                            revision_id,
                            projection_id,
                            ordinal,
                            unit.unit_type,
                            encode_json(structure),
                            encode_json(anchor),
                            unit.content,
                            content_hashes[index],
                            previous_unit_id,
                            next_unit_id,
                            encode_json(unit_payload["issues"]),
                            unit.derivation_method,
                            encode_json(unit_payload["geometry"]),
                            unit.confidence,
                            encode_json(unit_payload["quality_flags"]),
                        ),
                    )
                    if unit.content.strip():
                        connection.execute(
                            """
                            INSERT INTO source_units_fts(
                                unit_id, document_id, relative_path,
                                structure_path, normalized_content
                            ) VALUES (?, ?, ?, ?, ?)
                            """,
                            (
                                unit_id,
                                document["document_id"],
                                document["relative_path"],
                                json.dumps(structure, ensure_ascii=False),
                                unit.content,
                            ),
                        )

            connection.execute(
                """
                UPDATE extraction_projections
                SET is_active = 0
                WHERE revision_id = ? AND projection_id != ?
                """,
                (revision_id, projection_id),
            )
            connection.execute(
                "UPDATE extraction_projections SET is_active = 1 WHERE projection_id = ?",
                (projection_id,),
            )
            if old_projection_ids:
                placeholders = ",".join("?" for _ in old_projection_ids)
                connection.execute(
                    f"""
                    UPDATE extraction_issues
                    SET lifecycle_state = 'superseded'
                    WHERE projection_id IN ({placeholders})
                      AND lifecycle_state = 'active'
                    """,
                    old_projection_ids,
                )
            connection.execute(
                """
                UPDATE extraction_issues
                SET lifecycle_state = 'resolved'
                WHERE revision_id = ?
                  AND projection_id IS NULL
                  AND stage = 'extract'
                  AND lifecycle_state = 'active'
                """,
                (revision_id,),
            )
            registry_issues = [(issue, issue.to_dict()) for issue in extraction.issues]
            for ordinal, unit in enumerate(extraction.units, start=1):
                structure = unit.to_dict()["structure_path"]
                for issue in unit.issues:
                    issue_payload = issue.to_dict()
                    issue_payload["structural_locator"] = structure
                    issue_payload["details"] = {
                        **issue_payload.get("details", {}),
                        "unit_ordinal": ordinal,
                        "unit_type": unit.unit_type,
                    }
                    registry_issues.append((issue, issue_payload))
            for issue, issue_payload in registry_issues:
                structural_locator, locator_key = _issue_locator(issue_payload)
                connection.execute(
                    """
                    INSERT INTO extraction_issues(
                        issue_id, document_id, revision_id, attempt_id, projection_id,
                        stage, severity, code, message, details_json,
                        structural_locator_json, locator_key, lifecycle_state, created_at
                    ) VALUES (?, ?, ?, ?, ?, 'extract', ?, ?, ?, ?, ?, ?, 'active', ?)
                    ON CONFLICT(projection_id, stage, code, locator_key)
                    WHERE projection_id IS NOT NULL AND lifecycle_state = 'active'
                    DO UPDATE SET
                        attempt_id = excluded.attempt_id,
                        severity = excluded.severity,
                        message = excluded.message,
                        details_json = excluded.details_json
                    """,
                    (
                        f"issue_{uuid.uuid4().hex}",
                        document["document_id"],
                        revision_id,
                        attempt_id,
                        projection_id,
                        issue.severity,
                        issue.code,
                        issue.message,
                        encode_json(issue_payload),
                        encode_json(structural_locator),
                        locator_key,
                        now,
                    ),
                )
            connection.execute(
                """
                UPDATE extraction_attempts
                SET state = 'succeeded', projection_id = ?, completed_at = ?
                WHERE attempt_id = ?
                """,
                (projection_id, now, attempt_id),
            )
        return {
            "projection_id": projection_id,
            "source_units": len(extraction.units),
            "completeness_state": completeness_state,
            "extraction_issues": len(registry_issues),
        }

    def search(self, corpus_id: str, query: str, *, limit: int = 20) -> dict:
        if not 1 <= limit <= _MAX_SEARCH_RESULTS:
            raise BudgetExceededError(
                "search limit must be between 1 and 200",
                details={"limit": limit, "maximum": _MAX_SEARCH_RESULTS},
            )
        if len(query) > 2_000:
            raise BudgetExceededError(
                "search query must contain at most 2000 characters",
                details={"query_chars": len(query), "maximum": 2_000},
            )
        normalized = query.strip()
        if not normalized:
            return {
                "query": query,
                "strategy": "lexical_candidate_acquisition",
                "query_mode": "exact_phrase_fts",
                "zero_results_establish_absence": False,
                "candidates": [],
                "count": 0,
            }
        fts_query = (
            '{normalized_content relative_path} : "'
            + normalized.replace('"', '""')
            + '"'
        )
        query_mode = "exact_phrase_fts"
        guard = self.workspaces.promoted_source_guard(corpus_id)
        workspace_context = nullcontext(None)
        if guard is not None:
            from . import workspace_access

            workspace_context = workspace_access.opened_workspace_root(
                guard["root"],
                guard["identity"],
            )
        with (
            workspace_context as workspace_root_descriptor,
            corpus_read_connection(self.data_root, corpus_id) as connection,
        ):
            connection.create_function(
                "corpus_projection_is_current",
                4,
                self._projection_uses_current_adapter,
                deterministic=True,
            )
            live_clause = ""
            if guard is not None:
                observation_cache: dict[str, object] = {}

                def workspace_observation_is_current(
                    relative_path: str,
                    logical_size: int,
                    modified_ns: int,
                    changed_ns: int,
                    device: int,
                    inode: int,
                    mode: int,
                    flags: int,
                    is_dataless: int,
                ) -> int:
                    canonical = unicodedata.normalize("NFC", relative_path)
                    if canonical in guard["changes"]:
                        return 0
                    try:
                        state = observation_cache.get(canonical)
                        if state is None:
                            state = workspace_access.workspace_file_state_from_root_descriptor(
                                workspace_root_descriptor,
                                canonical,
                            )
                            observation_cache[canonical] = state
                        return int(
                            self._workspace_state_matches_document(
                                state,
                                {
                                    "logical_size": logical_size,
                                    "modified_ns": modified_ns,
                                    "changed_ns": changed_ns,
                                    "device": device,
                                    "inode": inode,
                                    "mode": mode,
                                    "flags": flags,
                                    "is_dataless": is_dataless,
                                },
                            )
                        )
                    except (CorpusError, OSError, TypeError, ValueError):
                        return 0

                connection.create_function(
                    "corpus_workspace_observation_is_current",
                    9,
                    workspace_observation_is_current,
                    deterministic=False,
                )
                live_clause = """
                  AND corpus_workspace_observation_is_current(
                          d.relative_path,
                          d.logical_size,
                          d.modified_ns,
                          d.changed_ns,
                          d.device,
                          d.inode,
                          d.mode,
                          d.flags,
                          d.is_dataless
                      ) = 1
                """
            search_sql = f"""
                SELECT f.unit_id, f.document_id, f.relative_path, f.structure_path,
                       instr(u.normalized_content, ?) AS literal_position,
                       LENGTH(CAST(u.normalized_content AS BLOB))
                           AS source_content_bytes,
                       bm25(source_units_fts) AS lexical_score,
                       u.revision_id, u.projection_id, u.unit_type,
                       u.derivation_method, u.confidence, u.quality_flags_json,
                       u.source_anchor_json, u.trust_lineage,
                       p.completeness_state
                FROM source_units_fts f
                JOIN source_units u ON u.unit_id = f.unit_id
                JOIN extraction_projections p ON p.projection_id = u.projection_id
                JOIN revisions r ON r.revision_id = u.revision_id
                JOIN documents d ON d.document_id = f.document_id
                WHERE source_units_fts MATCH ?
                  AND d.current_revision_id = u.revision_id
                  AND p.is_active = 1
                  AND d.deleted_at IS NULL
                  AND d.eligibility_state = 'supported'
                  AND corpus_projection_is_current(
                          d.extension,
                          p.adapter_id,
                          p.adapter_version,
                          p.config_hash
                      ) = 1
                  AND r.source_size = d.logical_size
                  AND r.source_modified_ns = d.modified_ns
                  AND r.source_changed_ns = d.changed_ns
                  AND r.source_inode = d.inode
                  {live_clause}
                ORDER BY bm25(source_units_fts)
                LIMIT ?
                """
            excerpt_anchor = normalized
            rows = connection.execute(
                search_sql,
                (excerpt_anchor, fts_query, limit),
            ).fetchall()
            terms = []
            seen_terms: set[str] = set()
            for term in _SEARCH_TOKEN_RE.findall(normalized):
                folded = term.casefold()
                if folded in seen_terms:
                    continue
                seen_terms.add(folded)
                terms.append(term)
                if len(terms) == _MAX_SEARCH_TERMS:
                    break
            if not rows and len(terms) > 1:
                query_mode = "all_terms_fts"
                excerpt_anchor = terms[0]
                fallback_query = (
                    "{normalized_content relative_path} : ("
                    + " AND ".join(f'"{term}"' for term in terms)
                    + ")"
                )
                rows = connection.execute(
                    search_sql,
                    (excerpt_anchor, fallback_query, limit),
                ).fetchall()
            excerpt_details = {}
            for row in rows:
                literal_position = int(row["literal_position"])
                excerpt_start = (
                    max(
                        1,
                        literal_position - CORPUS_SEARCH_EXCERPT_CONTEXT_BEFORE_CHARS,
                    )
                    if literal_position > 0
                    else 1
                )
                excerpt_probe = connection.execute(
                    """
                    SELECT substr(normalized_content, ?, ?) AS excerpt_probe
                    FROM source_units
                    WHERE unit_id = ?
                    """,
                    (
                        excerpt_start,
                        CORPUS_SEARCH_EXCERPT_MAX_CHARS + 1,
                        row["unit_id"],
                    ),
                ).fetchone()["excerpt_probe"]
                excerpt_details[row["unit_id"]] = {
                    "excerpt_probe": excerpt_probe,
                    "excerpt_start": excerpt_start,
                    "generation": (
                        (
                            "literal_query_window"
                            if query_mode == "exact_phrase_fts"
                            else "term_query_window"
                        )
                        if literal_position > 0
                        else "bounded_content_prefix"
                    ),
                    "source_content_bytes": row["source_content_bytes"],
                }
        candidates = []
        truncated_excerpt_count = 0
        for row in rows:
            item = dict(row)
            item["structure_path"] = json.loads(item["structure_path"])
            item["source_anchor"] = json.loads(item.pop("source_anchor_json"))
            item["quality_flags"] = json.loads(item.pop("quality_flags_json"))
            item.pop("literal_position")
            item.pop("source_content_bytes")
            excerpt = excerpt_details[item["unit_id"]]
            excerpt_probe = excerpt["excerpt_probe"]
            excerpt_truncated = (
                excerpt["excerpt_start"] > 1
                or excerpt["source_content_bytes"] > len(excerpt_probe.encode())
                or len(excerpt_probe) > CORPUS_SEARCH_EXCERPT_MAX_CHARS
            )
            if excerpt_truncated:
                truncated_excerpt_count += 1
            item["untrusted_excerpt"] = excerpt_probe[:CORPUS_SEARCH_EXCERPT_MAX_CHARS]
            item["excerpt_truncated"] = excerpt_truncated
            item["excerpt_max_characters"] = CORPUS_SEARCH_EXCERPT_MAX_CHARS
            item["excerpt_generation"] = excerpt["generation"]
            item["surfaced_by"] = "lexical_fts"
            item["ranking_is_evidence"] = False
            candidates.append(item)
        response = {
            "query": query,
            "strategy": "lexical_candidate_acquisition",
            "query_mode": query_mode,
            "zero_results_establish_absence": False,
            "count": len(candidates),
            "candidates": candidates,
            "truncated_excerpt_count": truncated_excerpt_count,
            "notice": (
                "Candidate excerpts may be truncated and require interpretation. "
                "Use corpus_read for exact source content."
            ),
        }
        serialized_bytes = len(encode_json(response).encode())
        if serialized_bytes > CORPUS_SEARCH_MAX_SERIALIZED_BYTES:
            raise BudgetExceededError(
                "search response exceeds the serialized response budget",
                details={
                    "candidate_count": len(candidates),
                    "serialized_bytes": serialized_bytes,
                    "maximum_serialized_bytes": (CORPUS_SEARCH_MAX_SERIALIZED_BYTES),
                },
            )
        return response

    def read_units(
        self,
        corpus_id: str,
        unit_ids: list[str],
        *,
        neighbor_span: int = 0,
        include_structure_context: bool = False,
        max_chars: int = CORPUS_READ_DEFAULT_CHARS,
    ) -> dict:
        if type(include_structure_context) is not bool:
            raise ConfigurationError("include_structure_context must be a boolean")
        if len(unit_ids) > _MAX_READ_UNITS:
            raise BudgetExceededError(
                "source unit selection must contain at most 200 ids",
                details={"unit_id_count": len(unit_ids), "maximum": _MAX_READ_UNITS},
            )
        if any(not unit_id or len(unit_id) > 200 for unit_id in unit_ids):
            raise BudgetExceededError(
                "source unit ids must contain between 1 and 200 characters",
                details={"maximum_unit_id_chars": 200},
            )
        if not 0 <= neighbor_span <= _MAX_NEIGHBOR_SPAN:
            raise BudgetExceededError(
                "neighbor span must be between 0 and 10",
                details={"neighbor_span": neighbor_span, "maximum": _MAX_NEIGHBOR_SPAN},
            )
        if not CORPUS_READ_MIN_CHARS <= max_chars <= CORPUS_READ_MAX_CHARS:
            raise BudgetExceededError(
                "source unit max_chars must be between 1000 and 200000",
                details={
                    "max_chars": max_chars,
                    "minimum": CORPUS_READ_MIN_CHARS,
                    "maximum": CORPUS_READ_MAX_CHARS,
                },
            )
        if not unit_ids:
            return {"units": [], "count": 0}
        requested_ids = list(dict.fromkeys(unit_ids))
        requested = set(requested_ids)
        with corpus_read_connection(self.data_root, corpus_id) as connection:
            connection.create_function(
                "corpus_character_length",
                1,
                lambda value: len(value or ""),
                deterministic=True,
            )
            placeholders = ",".join("?" for _ in requested_ids)
            seed_rows = connection.execute(
                f"""
                SELECT u.unit_id, u.projection_id, u.ordinal, u.structure_path_json
                FROM source_units u
                JOIN revisions r ON r.revision_id = u.revision_id
                JOIN documents d ON d.document_id = r.document_id
                WHERE u.unit_id IN ({placeholders})
                  AND d.deleted_at IS NULL
                """,
                requested_ids,
            ).fetchall()
            seed_by_id = {row["unit_id"]: row for row in seed_rows}
            seeds = [
                seed_by_id[unit_id]
                for unit_id in requested_ids
                if unit_id in seed_by_id
            ]
            selected_ids: list[str] = []
            selected: set[str] = set()
            for seed in seeds:
                inventory = connection.execute(
                    """
                    SELECT unit_id
                    FROM source_units u
                    WHERE u.projection_id = ? AND u.ordinal BETWEEN ? AND ?
                    ORDER BY ordinal
                    """,
                    (
                        seed["projection_id"],
                        max(1, seed["ordinal"] - neighbor_span),
                        seed["ordinal"] + neighbor_span,
                    ),
                ).fetchall()
                if include_structure_context:
                    structure = json.loads(seed["structure_path_json"])
                    table = structure.get("table")
                    inventory.extend(
                        connection.execute(
                            """
                        SELECT unit_id FROM source_units
                        WHERE projection_id = ?
                          AND json_extract(structure_path_json, '$.section') IS ?
                          AND json_extract(structure_path_json, '$.part') IS ?
                          AND json_extract(structure_path_json, '$.page') IS ?
                          AND (
                            (json_extract(structure_path_json, '$.table') = ? AND (
                                ? IS NULL OR json_extract(structure_path_json, '$.row') = ?
                                OR json_extract(structure_path_json, '$.is_header') = 1
                                OR unit_type = 'table'
                                OR json_extract(structure_path_json, '$.container_kind') = 'caption'))
                            OR json_extract(structure_path_json, '$.note') = ?
                            OR json_extract(structure_path_json, '$.object') = ?
                            OR json_extract(structure_path_json, '$.owner_paragraph_record') = ?
                            OR json_extract(structure_path_json, '$.owner_paragraph') = ?
                            OR json_extract(structure_path_json, '$.paragraph_record') = ?
                            OR json_extract(structure_path_json, '$.paragraph_element') = ?
                          )
                        ORDER BY ordinal LIMIT ?
                        """,
                            (
                                seed["projection_id"],
                                structure.get("section"),
                                structure.get("part"),
                                structure.get("page"),
                                table,
                                structure.get("row"),
                                structure.get("row"),
                                structure.get("note"),
                                None if table is not None else structure.get("object"),
                                structure.get("paragraph_record"),
                                structure.get("paragraph_element"),
                                structure.get("owner_paragraph_record"),
                                structure.get("owner_paragraph"),
                                CORPUS_READ_MAX_SELECTED_UNITS + 1,
                            ),
                        ).fetchall()
                    )
                for row in inventory:
                    if row["unit_id"] in selected:
                        continue
                    selected.add(row["unit_id"])
                    selected_ids.append(row["unit_id"])

            if len(selected_ids) > CORPUS_READ_MAX_SELECTED_UNITS:
                raise BudgetExceededError(
                    "source unit response exceeds the aggregate read budget",
                    details={
                        "selected_unit_count": len(selected_ids),
                        "maximum_selected_units": CORPUS_READ_MAX_SELECTED_UNITS,
                    },
                )
            if not selected_ids:
                return {"units": [], "count": 0}

            selected_placeholders = ",".join("?" for _ in selected_ids)
            inventory_rows = connection.execute(
                f"""
                SELECT unit_id,
                       corpus_character_length(normalized_content)
                           AS content_chars,
                       (
                           LENGTH(CAST(unit_id AS BLOB))
                           + LENGTH(CAST(revision_id AS BLOB))
                           + LENGTH(CAST(projection_id AS BLOB))
                           + LENGTH(CAST(unit_type AS BLOB))
                           + LENGTH(CAST(structure_path_json AS BLOB))
                           + LENGTH(CAST(source_anchor_json AS BLOB))
                           + LENGTH(CAST(normalized_content AS BLOB))
                           + LENGTH(CAST(content_sha256 AS BLOB))
                           + LENGTH(CAST(COALESCE(previous_unit_id, '') AS BLOB))
                           + LENGTH(CAST(COALESCE(next_unit_id, '') AS BLOB))
                           + LENGTH(CAST(extraction_issues_json AS BLOB))
                           + LENGTH(CAST(derivation_method AS BLOB))
                           + LENGTH(CAST(geometry_json AS BLOB))
                           + LENGTH(CAST(quality_flags_json AS BLOB))
                           + LENGTH(CAST(trust_lineage AS BLOB))
                           + 1024
                       ) AS payload_bytes
                FROM source_units
                WHERE unit_id IN ({selected_placeholders})
                """,
                selected_ids,
            ).fetchall()
            selected_content_chars = sum(row["content_chars"] for row in inventory_rows)
            selected_payload_bytes = sum(row["payload_bytes"] for row in inventory_rows)
            if (
                selected_content_chars > max_chars
                or selected_payload_bytes > CORPUS_READ_MAX_SERIALIZED_BYTES
            ):
                raise BudgetExceededError(
                    "source unit response exceeds the aggregate read budget",
                    details={
                        "selected_unit_count": len(selected_ids),
                        "maximum_selected_units": CORPUS_READ_MAX_SELECTED_UNITS,
                        "selected_content_chars": selected_content_chars,
                        "max_chars": max_chars,
                        "selected_payload_bytes": selected_payload_bytes,
                        "maximum_serialized_bytes": (CORPUS_READ_MAX_SERIALIZED_BYTES),
                    },
                )

            body_rows = connection.execute(
                f"""
                SELECT u.*, d.document_id, d.relative_path, d.current_revision_id,
                       active.projection_id AS active_projection_id,
                       projection.completeness_state,
                       projection.assurance_state,
                       projection.adapter_id,
                       projection.adapter_version,
                       projection.config_hash AS projection_config_hash,
                       d.extension AS document_extension,
                       d.logical_size AS document_logical_size,
                       d.modified_ns AS document_modified_ns,
                       d.changed_ns AS document_changed_ns,
                       d.device AS document_device,
                       d.inode AS document_inode,
                       d.mode AS document_mode,
                       d.flags AS document_flags,
                       d.is_dataless AS document_is_dataless,
                       CASE WHEN
                           r.source_size = d.logical_size
                           AND r.source_modified_ns = d.modified_ns
                           AND r.source_changed_ns = d.changed_ns
                           AND r.source_inode = d.inode
                       THEN 1 ELSE 0 END AS source_observation_current
                FROM source_units u
                JOIN revisions r ON r.revision_id = u.revision_id
                JOIN documents d ON d.document_id = r.document_id
                JOIN extraction_projections projection
                  ON projection.projection_id = u.projection_id
                LEFT JOIN extraction_projections active
                  ON active.revision_id = u.revision_id AND active.is_active = 1
                WHERE u.unit_id IN ({selected_placeholders})
                  AND d.deleted_at IS NULL
                """,
                selected_ids,
            ).fetchall()
            row_by_id = {row["unit_id"]: row for row in body_rows}
            rows = [
                row_by_id[unit_id] for unit_id in selected_ids if unit_id in row_by_id
            ]
        promoted_live_current: dict[str, bool] = {}
        guard = self.workspaces.promoted_source_guard(corpus_id)
        if guard is not None:
            from . import workspace_access

            try:
                with workspace_access.opened_workspace_root(
                    guard["root"],
                    guard["identity"],
                ) as root_descriptor:
                    for row in rows:
                        relative_path = unicodedata.normalize(
                            "NFC",
                            row["relative_path"],
                        )
                        if relative_path in promoted_live_current:
                            continue
                        if relative_path in guard["changes"]:
                            promoted_live_current[relative_path] = False
                            continue
                        try:
                            live_state = workspace_access.workspace_file_state_from_root_descriptor(
                                root_descriptor,
                                relative_path,
                            )
                            promoted_live_current[relative_path] = (
                                self._workspace_state_matches_document(
                                    live_state,
                                    {
                                        "logical_size": row["document_logical_size"],
                                        "modified_ns": row["document_modified_ns"],
                                        "changed_ns": row["document_changed_ns"],
                                        "device": row["document_device"],
                                        "inode": row["document_inode"],
                                        "mode": row["document_mode"],
                                        "flags": row["document_flags"],
                                        "is_dataless": row["document_is_dataless"],
                                    },
                                )
                            )
                        except CorpusError:
                            promoted_live_current[relative_path] = False
            except CorpusError:
                promoted_live_current = {
                    unicodedata.normalize("NFC", row["relative_path"]): False
                    for row in rows
                }
        units = []
        for row in rows:
            item = dict(row)
            item["structure_path"] = json.loads(item.pop("structure_path_json"))
            item["source_anchor"] = json.loads(item.pop("source_anchor_json"))
            item["extraction_issues"] = json.loads(item.pop("extraction_issues_json"))
            item["geometry"] = json.loads(item.pop("geometry_json"))
            item["quality_flags"] = json.loads(item.pop("quality_flags_json"))
            item["requested"] = item["unit_id"] in requested
            source_observation_current = bool(item.pop("source_observation_current"))
            relative_path_nfc = unicodedata.normalize("NFC", item["relative_path"])
            live_source_observation_current = promoted_live_current.get(
                relative_path_nfc,
                True,
            )
            for field in (
                "document_logical_size",
                "document_modified_ns",
                "document_changed_ns",
                "document_device",
                "document_inode",
                "document_mode",
                "document_flags",
                "document_is_dataless",
            ):
                item.pop(field)
            projection_current = self._projection_uses_current_adapter(
                item.pop("document_extension"),
                item["adapter_id"],
                item["adapter_version"],
                item.pop("projection_config_hash"),
            )
            if item["revision_id"] != item["current_revision_id"]:
                item["dependency_state"] = "stale_source_revision"
            elif not source_observation_current or not live_source_observation_current:
                item["dependency_state"] = "stale_source_observation"
            elif item["projection_id"] != item["active_projection_id"]:
                item["dependency_state"] = "stale_extraction_projection"
            elif not projection_current:
                item["dependency_state"] = "stale_extraction_adapter"
            else:
                item["dependency_state"] = "valid"
            item["untrusted_content"] = item.pop("normalized_content")
            units.append(item)
        response = {"count": len(units), "units": units}
        serialized_bytes = len(encode_json(response).encode())
        if serialized_bytes > CORPUS_READ_MAX_SERIALIZED_BYTES:
            raise BudgetExceededError(
                "source unit response exceeds the serialized response budget",
                details={
                    "serialized_bytes": serialized_bytes,
                    "maximum_serialized_bytes": CORPUS_READ_MAX_SERIALIZED_BYTES,
                },
            )
        return response
