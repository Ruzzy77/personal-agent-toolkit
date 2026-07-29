"""Explicit, transactional migrations for per-corpus databases."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import uuid
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .config import (
    RuntimePaths,
    ensure_private_directory_at,
    open_private_file_at,
    private_directory,
)
from .errors import (
    ConfigurationError,
    MigrationError,
    MigrationRequiredError,
    UnsupportedSchemaError,
)
from .schema import (
    CORPUS_SCHEMA_VERSION,
    EXTRACTION_SCHEMA_VERSION,
    PROVENANCE_GUARD_SCHEMA,
)


@dataclass(frozen=True)
class SchemaState:
    current_version: int
    target_version: int

    @property
    def migration_required(self) -> bool:
        return self.current_version < self.target_version


def _paths_for_corpus_database(path: Path) -> RuntimePaths:
    if path.name != "corpus.sqlite" or path.parent.parent.name != "corpora":
        raise ConfigurationError(
            "corpus database location is not recognized",
            details={
                "path": str(path),
                "reason": "unrecognized_corpus_database_location",
            },
        )
    return RuntimePaths(
        data_root=path.parent.parent.parent,
        corpus_id=path.parent.name,
    )


def _require_rollback_journal_header(path: Path, header: bytes) -> None:
    if (
        len(header) >= 20
        and header.startswith(b"SQLite format 3\x00")
        and (header[18] == 2 or header[19] == 2)
    ):
        raise MigrationRequiredError(
            "database journal mode requires explicit normalization",
            details={
                "path": str(path),
                "reason": "wal_journal_mode_requires_explicit_normalization",
                "command": "corpus migrate --corpus <corpus-id>",
            },
        )


def _require_corpus_database(
    path: Path,
    *,
    require_rollback_journal: bool = False,
) -> bool:
    paths = _paths_for_corpus_database(path)
    try:
        with paths.open_corpus_root() as corpus_descriptor:
            database_descriptor, _ = open_private_file_at(
                corpus_descriptor,
                "corpus.sqlite",
                path=path,
            )
            try:
                header = os.pread(database_descriptor, 100, 0)
            finally:
                os.close(database_descriptor)
    except ConfigurationError as exc:
        if exc.details.get("reason") == "missing":
            return False
        raise
    if require_rollback_journal:
        _require_rollback_journal_header(path, header)
    return True


def _configure_write_connection(connection: sqlite3.Connection, *, path: Path) -> None:
    connection.execute("PRAGMA busy_timeout = 30000")
    current = str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()
    if current != "delete":
        current = str(
            connection.execute("PRAGMA journal_mode = DELETE").fetchone()[0]
        ).lower()
    if current != "delete":
        raise MigrationError(
            "database could not enter rollback-journal mode",
            details={"path": str(path), "journal_mode": current},
        )


def _connect(path: Path) -> sqlite3.Connection:
    if not _require_corpus_database(path):
        raise MigrationError(
            "corpus database does not exist",
            details={"path": str(path)},
        )
    connection = sqlite3.connect(path, timeout=30)
    try:
        connection.row_factory = sqlite3.Row
        _configure_write_connection(connection, path=path)
        connection.execute("PRAGMA synchronous = FULL")
        return connection
    except Exception:
        connection.close()
        raise


def _connect_readonly(path: Path) -> sqlite3.Connection:
    if not _require_corpus_database(path, require_rollback_journal=True):
        raise MigrationError(
            "corpus database does not exist",
            details={"path": str(path)},
        )
    connection = sqlite3.connect(
        f"{path.absolute().as_uri()}?mode=ro",
        uri=True,
        timeout=30,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


def _schema_version(connection: sqlite3.Connection) -> int | None:
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_info'"
    ).fetchone()
    if exists is None:
        return None
    rows = connection.execute("SELECT version FROM schema_info").fetchall()
    if len(rows) != 1:
        raise MigrationError(
            "schema_info must contain exactly one version row",
            details={"row_count": len(rows)},
        )
    try:
        return int(rows[0]["version"])
    except (TypeError, ValueError) as exc:
        raise MigrationError("schema_info contains an invalid version") from exc


IMMUTABLE_REVISION_GUARD = "guard_revisions_document_ownership_update"


def _assert_v2_structure(connection: sqlite3.Connection) -> None:
    required_columns = {
        "extraction_attempts": {"attempt_id", "revision_id", "projection_id", "state"},
        "extraction_projections": {
            "projection_id",
            "revision_id",
            "result_manifest_hash",
            "is_active",
        },
        "source_units": {"unit_id", "revision_id", "projection_id", "derivation_method"},
        "interpretation_queue": {"queue_id", "revision_id", "projection_id", "state"},
        "snapshots": {"snapshot_id", "extraction_projection_set_hash"},
        "snapshot_documents": {"snapshot_id", "revision_id", "projection_id"},
        "extraction_issues": {
            "issue_id",
            "attempt_id",
            "projection_id",
            "lifecycle_state",
        },
    }
    missing: dict[str, list[str]] = {}
    for table, columns in required_columns.items():
        actual = {
            row["name"]
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        absent = sorted(columns - actual)
        if absent:
            missing[table] = absent
    required_indexes = {
        "idx_attempts_identity",
        "idx_projections_active_revision",
        "idx_projections_identity",
        "idx_revisions_identity",
        "idx_units_identity",
        "idx_units_projection",
        "idx_active_projection_issue",
    }
    actual_indexes = {
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
        ).fetchall()
    }
    absent_indexes = sorted(required_indexes - actual_indexes)
    required_triggers = {
        "guard_documents_current_revision_insert",
        "guard_documents_current_revision_update",
        "guard_revisions_predecessor_insert",
        "guard_revisions_predecessor_update",
    }
    actual_triggers = {
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger'"
        ).fetchall()
    }
    absent_triggers = sorted(required_triggers - actual_triggers)
    if missing or absent_indexes or absent_triggers:
        raise MigrationError(
            "corpus database does not satisfy the v2 schema contract",
            details={
                "missing_columns": missing,
                "missing_indexes": absent_indexes,
                "missing_triggers": absent_triggers,
            },
        )


def _assert_v3_structure(connection: sqlite3.Connection) -> None:
    _assert_v2_structure(connection)
    trigger = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'trigger' AND name = ?",
        (IMMUTABLE_REVISION_GUARD,),
    ).fetchone()
    if trigger is None:
        raise MigrationError(
            "corpus database does not satisfy the v3 schema contract",
            details={"missing_triggers": [IMMUTABLE_REVISION_GUARD]},
        )


def _assert_v4_structure(connection: sqlite3.Connection) -> None:
    _assert_v3_structure(connection)
    revision_columns = {
        row["name"]: row
        for row in connection.execute("PRAGMA table_info(revisions)").fetchall()
    }
    source_changed_ns = revision_columns.get("source_changed_ns")
    if source_changed_ns is None or not source_changed_ns["notnull"]:
        raise MigrationError(
            "corpus database does not satisfy the v4 schema contract",
            details={"missing_columns": {"revisions": ["source_changed_ns"]}},
        )


def inspect_schema(path: Path) -> SchemaState:
    if not _require_corpus_database(path):
        return SchemaState(CORPUS_SCHEMA_VERSION, CORPUS_SCHEMA_VERSION)
    connection = _connect_readonly(path)
    try:
        version = _schema_version(connection)
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version in {2, 3, CORPUS_SCHEMA_VERSION}:
            if user_version != version:
                raise MigrationError(
                    "schema_info and PRAGMA user_version disagree",
                    details={
                        "path": str(path),
                        "schema_info_version": version,
                        "user_version": user_version,
                    },
                )
            if version == 2:
                _assert_v2_structure(connection)
            elif version == 3:
                _assert_v3_structure(connection)
            else:
                _assert_v4_structure(connection)
    finally:
        connection.close()
    if version is None:
        raise UnsupportedSchemaError(
            "corpus database has no schema_info table",
            details={"path": str(path)},
        )
    if version > CORPUS_SCHEMA_VERSION:
        raise UnsupportedSchemaError(
            "corpus database was created by a newer Corpus runtime",
            details={
                "path": str(path),
                "current_version": version,
                "supported_version": CORPUS_SCHEMA_VERSION,
            },
        )
    return SchemaState(version, CORPUS_SCHEMA_VERSION)


def require_current_schema(path: Path) -> None:
    state = inspect_schema(path)
    if state.migration_required:
        raise MigrationRequiredError(
            "corpus database requires an explicit schema migration",
            details={
                "path": str(path),
                "current_version": state.current_version,
                "target_version": state.target_version,
                "command": "corpus migrate --corpus <corpus-id>",
            },
        )


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _stable_id(prefix: str, *parts: str) -> str:
    joined = "\0".join(parts)
    digest = hashlib.sha256(f"{prefix}\0{joined}".encode()).hexdigest()
    return f"{prefix}_{digest[:32]}"


def _execute_transactional_script(connection: sqlite3.Connection, script: str) -> None:
    statement = ""
    for line in script.splitlines():
        statement = f"{statement}\n{line}".strip()
        if statement and sqlite3.complete_statement(statement):
            connection.execute(statement)
            statement = ""
    if statement.strip():
        raise MigrationError(
            "migration contains an incomplete SQL statement",
            details={"statement": statement},
        )


def _legacy_capabilities() -> dict:
    return {
        "schema_version": 1,
        "body_text": "unknown",
        "page_text": "unknown",
        "tables": "unknown",
        "footnotes": "unknown",
        "sheet_cells": "unknown",
        "ocr_text": "unknown",
        "migration_note": "legacy projection; capabilities were not recorded",
    }


def _validate_legacy_provenance_pairs(connection: sqlite3.Connection) -> None:
    invalid_current = [
        dict(row)
        for row in connection.execute(
            """
            SELECT d.document_id, d.current_revision_id
            FROM documents d
            LEFT JOIN revisions r
              ON r.revision_id = d.current_revision_id
             AND r.document_id = d.document_id
            WHERE d.current_revision_id IS NOT NULL
              AND r.revision_id IS NULL
            LIMIT 20
            """
        )
    ]
    invalid_predecessors = [
        dict(row)
        for row in connection.execute(
            """
            SELECT r.revision_id, r.predecessor_revision_id, r.document_id
            FROM revisions r
            LEFT JOIN revisions predecessor
              ON predecessor.revision_id = r.predecessor_revision_id
             AND predecessor.document_id = r.document_id
            WHERE r.predecessor_revision_id IS NOT NULL
              AND predecessor.revision_id IS NULL
            LIMIT 20
            """
        )
    ]
    if invalid_current or invalid_predecessors:
        raise MigrationError(
            "legacy corpus contains cross-document revision relationships",
            details={
                "invalid_current_revisions": invalid_current,
                "invalid_predecessors": invalid_predecessors,
            },
        )


def _create_projection_tables(connection: sqlite3.Connection) -> None:
    _execute_transactional_script(
        connection,
        """
        CREATE UNIQUE INDEX idx_revisions_identity
            ON revisions(revision_id, document_id);

        CREATE TABLE extraction_projections (
            projection_id TEXT PRIMARY KEY,
            revision_id TEXT NOT NULL,
            adapter_id TEXT NOT NULL,
            adapter_version TEXT NOT NULL,
            config_hash TEXT NOT NULL,
            result_manifest_hash TEXT NOT NULL,
            completeness_state TEXT NOT NULL
                CHECK (completeness_state IN ('complete', 'partial')),
            capability_manifest_json TEXT NOT NULL,
            assurance_state TEXT NOT NULL
                CHECK (assurance_state IN ('declared', 'legacy_unverified')),
            is_active INTEGER NOT NULL DEFAULT 0 CHECK (is_active IN (0, 1)),
            created_at TEXT NOT NULL,
            FOREIGN KEY(revision_id) REFERENCES revisions(revision_id)
        );

        CREATE INDEX idx_projections_revision
            ON extraction_projections(revision_id, created_at);
        CREATE UNIQUE INDEX idx_projections_active_revision
            ON extraction_projections(revision_id) WHERE is_active = 1;
        CREATE UNIQUE INDEX idx_projections_identity
            ON extraction_projections(projection_id, revision_id);

        CREATE TABLE extraction_attempts (
            attempt_id TEXT PRIMARY KEY,
            revision_id TEXT NOT NULL,
            adapter_id TEXT NOT NULL,
            adapter_version TEXT NOT NULL,
            config_hash TEXT NOT NULL,
            state TEXT NOT NULL CHECK (state IN ('running', 'succeeded', 'failed')),
            projection_id TEXT,
            error_json TEXT,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            CHECK (
                (state = 'running' AND projection_id IS NULL
                    AND error_json IS NULL AND completed_at IS NULL)
                OR (state = 'succeeded' AND projection_id IS NOT NULL
                    AND error_json IS NULL AND completed_at IS NOT NULL)
                OR (state = 'failed' AND projection_id IS NULL
                    AND error_json IS NOT NULL AND completed_at IS NOT NULL)
            ),
            FOREIGN KEY(revision_id) REFERENCES revisions(revision_id),
            FOREIGN KEY(projection_id) REFERENCES extraction_projections(projection_id),
            FOREIGN KEY(projection_id, revision_id)
                REFERENCES extraction_projections(projection_id, revision_id)
        );

        CREATE INDEX idx_attempts_revision
            ON extraction_attempts(revision_id, started_at);
        CREATE UNIQUE INDEX idx_attempts_identity
            ON extraction_attempts(attempt_id, revision_id);
        """,
    )


def _legacy_projection_map(connection: sqlite3.Connection) -> tuple[dict[str, str], dict[str, str]]:
    config_hash = hashlib.sha256(b"{}").hexdigest()
    projection_by_revision: dict[str, str] = {}
    attempt_by_revision: dict[str, str] = {}
    revisions = connection.execute(
        """
        SELECT r.*, d.adapter
        FROM revisions r
        JOIN documents d ON d.document_id = r.document_id
        ORDER BY r.captured_at, r.revision_id
        """
    ).fetchall()
    for revision in revisions:
        revision_id = revision["revision_id"]
        adapter_id = f"builtin:{revision['adapter'] or 'unknown'}"
        adapter_version = revision["extractor_version"] or "legacy-unknown"
        units = [
            {
                "unit_id": row["unit_id"],
                "ordinal": row["ordinal"],
                "unit_type": row["unit_type"],
                "structure_path_json": row["structure_path_json"],
                "content_sha256": row["content_sha256"],
            }
            for row in connection.execute(
                """
                SELECT unit_id, ordinal, unit_type, structure_path_json, content_sha256
                FROM source_units
                WHERE revision_id = ?
                ORDER BY ordinal
                """,
                (revision_id,),
            )
        ]
        has_preserved_projection = revision["extraction_state"] == "complete" or bool(units)
        if has_preserved_projection:
            projection_adapter_version = (
                adapter_version
                if revision["extraction_state"] == "complete"
                else "legacy-preserved"
            )
            result_manifest_hash = hashlib.sha256(_canonical_json(units).encode()).hexdigest()
            projection_id = _stable_id(
                "projection",
                revision_id,
                adapter_id,
                projection_adapter_version,
                config_hash,
                result_manifest_hash,
            )
            projection_by_revision[revision_id] = projection_id
            connection.execute(
                """
                INSERT INTO extraction_projections(
                    projection_id, revision_id, adapter_id, adapter_version,
                    config_hash, result_manifest_hash, completeness_state,
                    capability_manifest_json, assurance_state, is_active, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'partial', ?, 'legacy_unverified', 1, ?)
                """,
                (
                    projection_id,
                    revision_id,
                    adapter_id,
                    projection_adapter_version,
                    config_hash,
                    result_manifest_hash,
                    _canonical_json(_legacy_capabilities()),
                    revision["captured_at"],
                ),
            )
            succeeded_attempt_id = _stable_id(
                "attempt",
                revision_id,
                adapter_id,
                projection_adapter_version,
                config_hash,
                "legacy-succeeded",
            )
            connection.execute(
                """
                INSERT INTO extraction_attempts(
                    attempt_id, revision_id, adapter_id, adapter_version,
                    config_hash, state, projection_id, started_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, 'succeeded', ?, ?, ?)
                """,
                (
                    succeeded_attempt_id,
                    revision_id,
                    adapter_id,
                    projection_adapter_version,
                    config_hash,
                    projection_id,
                    revision["captured_at"],
                    revision["captured_at"],
                ),
            )

        if revision["extraction_state"] != "complete":
            failed_attempt_id = _stable_id(
                "attempt",
                revision_id,
                adapter_id,
                adapter_version,
                config_hash,
                "legacy-failed",
            )
            attempt_by_revision[revision_id] = failed_attempt_id
            connection.execute(
                """
                INSERT INTO extraction_attempts(
                    attempt_id, revision_id, adapter_id, adapter_version,
                    config_hash, state, error_json, started_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, 'failed', ?, ?, ?)
                """,
                (
                    failed_attempt_id,
                    revision_id,
                    adapter_id,
                    adapter_version,
                    config_hash,
                    _canonical_json(
                        {
                            "code": "legacy_failed_extraction",
                            "message": "Failure details remain in legacy extraction issues.",
                        }
                    ),
                    revision["captured_at"],
                    revision["captured_at"],
                ),
            )
        else:
            attempt_by_revision[revision_id] = succeeded_attempt_id
    return projection_by_revision, attempt_by_revision


def _rebuild_source_units(
    connection: sqlite3.Connection,
    projection_by_revision: dict[str, str],
) -> None:
    _execute_transactional_script(
        connection,
        """
        DROP INDEX IF EXISTS idx_evidence_claim;
        DROP INDEX IF EXISTS idx_evidence_revision;
        DROP INDEX IF EXISTS idx_units_revision;
        ALTER TABLE evidence_links RENAME TO evidence_links_v1;
        ALTER TABLE source_units RENAME TO source_units_v1;

        CREATE TABLE source_units (
            unit_id TEXT PRIMARY KEY,
            revision_id TEXT NOT NULL,
            projection_id TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            unit_type TEXT NOT NULL,
            structure_path_json TEXT NOT NULL,
            source_anchor_json TEXT NOT NULL,
            normalized_content TEXT NOT NULL,
            content_sha256 TEXT NOT NULL,
            previous_unit_id TEXT,
            next_unit_id TEXT,
            extraction_issues_json TEXT NOT NULL,
            derivation_method TEXT NOT NULL DEFAULT 'native_text',
            geometry_json TEXT NOT NULL DEFAULT '{}',
            confidence REAL,
            quality_flags_json TEXT NOT NULL DEFAULT '[]',
            trust_lineage TEXT NOT NULL DEFAULT 'untrusted_source_derived',
            UNIQUE(projection_id, ordinal),
            FOREIGN KEY(revision_id) REFERENCES revisions(revision_id),
            FOREIGN KEY(projection_id) REFERENCES extraction_projections(projection_id),
            FOREIGN KEY(projection_id, revision_id)
                REFERENCES extraction_projections(projection_id, revision_id)
        );

        CREATE INDEX idx_units_revision ON source_units(revision_id, ordinal);
        CREATE INDEX idx_units_projection ON source_units(projection_id, ordinal);
        CREATE UNIQUE INDEX idx_units_identity
            ON source_units(unit_id, revision_id);
        """,
    )
    rows = connection.execute(
        "SELECT * FROM source_units_v1 ORDER BY revision_id, ordinal"
    ).fetchall()
    for row in rows:
        projection_id = projection_by_revision.get(row["revision_id"])
        if projection_id is None:
            raise MigrationError(
                "legacy source unit has no successful extraction projection",
                details={"unit_id": row["unit_id"], "revision_id": row["revision_id"]},
            )
        anchor = json.loads(row["source_anchor_json"])
        anchor["projection_id"] = projection_id
        anchor["extraction_schema_version"] = EXTRACTION_SCHEMA_VERSION
        connection.execute(
            """
            INSERT INTO source_units(
                unit_id, revision_id, projection_id, ordinal, unit_type,
                structure_path_json, source_anchor_json, normalized_content,
                content_sha256, previous_unit_id, next_unit_id,
                extraction_issues_json, derivation_method, geometry_json,
                confidence, quality_flags_json, trust_lineage
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'native_text', '{}', NULL, '[]', ?)
            """,
            (
                row["unit_id"],
                row["revision_id"],
                projection_id,
                row["ordinal"],
                row["unit_type"],
                row["structure_path_json"],
                _canonical_json(anchor),
                row["normalized_content"],
                row["content_sha256"],
                row["previous_unit_id"],
                row["next_unit_id"],
                row["extraction_issues_json"],
                row["trust_lineage"],
            ),
        )

    _execute_transactional_script(
        connection,
        """
        CREATE TABLE evidence_links (
            evidence_link_id TEXT PRIMARY KEY,
            claim_id TEXT NOT NULL,
            source_unit_id TEXT NOT NULL,
            source_revision_id TEXT NOT NULL,
            source_span_json TEXT NOT NULL,
            stance TEXT NOT NULL
                CHECK (stance IN ('supports', 'qualifies', 'contradicts', 'mentions')),
            qualifier TEXT,
            applicability_json TEXT NOT NULL,
            FOREIGN KEY(claim_id) REFERENCES atomic_claims(claim_id),
            FOREIGN KEY(source_unit_id) REFERENCES source_units(unit_id),
            FOREIGN KEY(source_revision_id) REFERENCES revisions(revision_id),
            FOREIGN KEY(source_unit_id, source_revision_id)
                REFERENCES source_units(unit_id, revision_id)
        );
        INSERT INTO evidence_links SELECT * FROM evidence_links_v1;
        CREATE INDEX idx_evidence_claim ON evidence_links(claim_id);
        CREATE INDEX idx_evidence_revision ON evidence_links(source_revision_id);
        DROP TABLE evidence_links_v1;
        DROP TABLE source_units_v1;
        """,
    )


def _rebuild_interpretation_queue(
    connection: sqlite3.Connection,
    projection_by_revision: dict[str, str],
) -> None:
    rows = connection.execute("SELECT * FROM interpretation_queue").fetchall()
    _execute_transactional_script(
        connection,
        """
        ALTER TABLE interpretation_queue RENAME TO interpretation_queue_v1;
        CREATE TABLE interpretation_queue (
            queue_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            revision_id TEXT NOT NULL,
            projection_id TEXT NOT NULL UNIQUE,
            state TEXT NOT NULL
                CHECK (state IN ('pending', 'in_progress', 'complete', 'stale', 'failed')),
            reason TEXT NOT NULL,
            checkpoint_json TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(document_id) REFERENCES documents(document_id),
            FOREIGN KEY(revision_id) REFERENCES revisions(revision_id),
            FOREIGN KEY(projection_id) REFERENCES extraction_projections(projection_id),
            FOREIGN KEY(revision_id, document_id)
                REFERENCES revisions(revision_id, document_id),
            FOREIGN KEY(projection_id, revision_id)
                REFERENCES extraction_projections(projection_id, revision_id)
        );
        """,
    )
    for row in rows:
        projection_id = projection_by_revision.get(row["revision_id"])
        if projection_id is None:
            raise MigrationError(
                "legacy interpretation queue item has no extraction projection",
                details={"queue_id": row["queue_id"], "revision_id": row["revision_id"]},
            )
        connection.execute(
            """
            INSERT INTO interpretation_queue(
                queue_id, document_id, revision_id, projection_id, state,
                reason, checkpoint_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["queue_id"],
                row["document_id"],
                row["revision_id"],
                projection_id,
                row["state"],
                row["reason"],
                row["checkpoint_json"],
                row["created_at"],
                row["updated_at"],
            ),
        )
    connection.execute("DROP TABLE interpretation_queue_v1")


def _rebuild_snapshots(
    connection: sqlite3.Connection,
    projection_by_revision: dict[str, str],
) -> None:
    snapshots = connection.execute("SELECT * FROM snapshots").fetchall()
    snapshot_rows = connection.execute("SELECT * FROM snapshot_documents").fetchall()
    by_snapshot: dict[str, list[tuple[str, str, str]]] = {}
    for row in snapshot_rows:
        projection_id = projection_by_revision.get(row["revision_id"])
        if projection_id is None:
            raise MigrationError(
                "legacy snapshot revision has no extraction projection",
                details={
                    "snapshot_id": row["snapshot_id"],
                    "revision_id": row["revision_id"],
                },
            )
        by_snapshot.setdefault(row["snapshot_id"], []).append(
            (row["document_id"], row["revision_id"], projection_id)
        )

    connection.execute("PRAGMA legacy_alter_table = ON")
    _execute_transactional_script(
        connection,
        """
        ALTER TABLE snapshot_documents RENAME TO snapshot_documents_v1;
        ALTER TABLE snapshots RENAME TO snapshots_v1;

        CREATE TABLE snapshots (
            snapshot_id TEXT PRIMARY KEY,
            state TEXT NOT NULL CHECK (state IN ('complete', 'failed')),
            coverage_state TEXT NOT NULL CHECK (coverage_state IN ('complete', 'partial')),
            document_revision_set_hash TEXT NOT NULL,
            extraction_projection_set_hash TEXT NOT NULL,
            document_count INTEGER NOT NULL,
            supported_document_count INTEGER NOT NULL,
            extraction_schema_version INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            completed_at TEXT
        );

        CREATE TABLE snapshot_documents (
            snapshot_id TEXT NOT NULL,
            document_id TEXT NOT NULL,
            revision_id TEXT NOT NULL,
            projection_id TEXT NOT NULL,
            PRIMARY KEY(snapshot_id, document_id),
            FOREIGN KEY(snapshot_id) REFERENCES snapshots(snapshot_id),
            FOREIGN KEY(document_id) REFERENCES documents(document_id),
            FOREIGN KEY(revision_id) REFERENCES revisions(revision_id),
            FOREIGN KEY(projection_id) REFERENCES extraction_projections(projection_id),
            FOREIGN KEY(revision_id, document_id)
                REFERENCES revisions(revision_id, document_id),
            FOREIGN KEY(projection_id, revision_id)
                REFERENCES extraction_projections(projection_id, revision_id)
        );
        """,
    )
    for snapshot in snapshots:
        mapping = by_snapshot.get(snapshot["snapshot_id"], [])
        manifest = "\n".join(
            f"{document_id}={revision_id}@{projection_id}"
            for document_id, revision_id, projection_id in sorted(mapping)
        )
        projection_hash = hashlib.sha256(manifest.encode()).hexdigest()
        connection.execute(
            """
            INSERT INTO snapshots(
                snapshot_id, state, coverage_state, document_revision_set_hash,
                extraction_projection_set_hash, document_count,
                supported_document_count, extraction_schema_version,
                created_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot["snapshot_id"],
                snapshot["state"],
                snapshot["coverage_state"],
                snapshot["document_revision_set_hash"],
                projection_hash,
                snapshot["document_count"],
                snapshot["supported_document_count"],
                snapshot["extraction_schema_version"],
                snapshot["created_at"],
                snapshot["completed_at"],
            ),
        )

    for row in snapshot_rows:
        projection_id = projection_by_revision[row["revision_id"]]
        connection.execute(
            """
            INSERT INTO snapshot_documents(
                snapshot_id, document_id, revision_id, projection_id
            ) VALUES (?, ?, ?, ?)
            """,
            (row["snapshot_id"], row["document_id"], row["revision_id"], projection_id),
        )
    _execute_transactional_script(
        connection,
        """
        DROP TABLE snapshot_documents_v1;
        DROP TABLE snapshots_v1;
        """,
    )
    connection.execute("PRAGMA legacy_alter_table = OFF")


def _rebuild_extraction_issues(
    connection: sqlite3.Connection,
    projection_by_revision: dict[str, str],
    attempt_by_revision: dict[str, str],
) -> None:
    rows = connection.execute("SELECT * FROM extraction_issues").fetchall()
    _execute_transactional_script(
        connection,
        """
        DROP INDEX IF EXISTS idx_issues_document;
        ALTER TABLE extraction_issues RENAME TO extraction_issues_v1;
        CREATE TABLE extraction_issues (
            issue_id TEXT PRIMARY KEY,
            document_id TEXT,
            revision_id TEXT,
            attempt_id TEXT,
            projection_id TEXT,
            scan_id TEXT,
            stage TEXT NOT NULL,
            severity TEXT NOT NULL,
            code TEXT NOT NULL,
            message TEXT NOT NULL,
            details_json TEXT NOT NULL,
            structural_locator_json TEXT NOT NULL DEFAULT '{}',
            locator_key TEXT NOT NULL DEFAULT '',
            lifecycle_state TEXT NOT NULL DEFAULT 'active'
                CHECK (
                    lifecycle_state IN (
                        'active', 'resolved', 'superseded', 'legacy_unverified'
                    )
                ),
            created_at TEXT NOT NULL,
            CHECK (revision_id IS NULL OR document_id IS NOT NULL),
            CHECK (attempt_id IS NULL OR revision_id IS NOT NULL),
            CHECK (projection_id IS NULL OR revision_id IS NOT NULL),
            FOREIGN KEY(document_id) REFERENCES documents(document_id),
            FOREIGN KEY(revision_id) REFERENCES revisions(revision_id),
            FOREIGN KEY(attempt_id) REFERENCES extraction_attempts(attempt_id),
            FOREIGN KEY(projection_id) REFERENCES extraction_projections(projection_id),
            FOREIGN KEY(revision_id, document_id)
                REFERENCES revisions(revision_id, document_id),
            FOREIGN KEY(attempt_id, revision_id)
                REFERENCES extraction_attempts(attempt_id, revision_id),
            FOREIGN KEY(projection_id, revision_id)
                REFERENCES extraction_projections(projection_id, revision_id),
            FOREIGN KEY(scan_id) REFERENCES scan_runs(scan_id)
        );
        """,
    )
    for row in rows:
        revision_id = row["revision_id"]
        projection_id = projection_by_revision.get(revision_id) if revision_id else None
        attempt_id = attempt_by_revision.get(revision_id) if revision_id else None
        locator_key = hashlib.sha256(
            _canonical_json(
                {
                    "stage": row["stage"],
                    "code": row["code"],
                    "details_json": row["details_json"],
                }
            ).encode()
        ).hexdigest()
        connection.execute(
            """
            INSERT INTO extraction_issues(
                issue_id, document_id, revision_id, attempt_id, projection_id,
                scan_id, stage, severity, code, message, details_json,
                structural_locator_json, locator_key, lifecycle_state, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', ?, 'legacy_unverified', ?)
            """,
            (
                row["issue_id"],
                row["document_id"],
                revision_id,
                attempt_id,
                projection_id,
                row["scan_id"],
                row["stage"],
                row["severity"],
                row["code"],
                row["message"],
                row["details_json"],
                locator_key,
                row["created_at"],
            ),
        )
    _execute_transactional_script(
        connection,
        """
        CREATE INDEX idx_issues_document
            ON extraction_issues(document_id, created_at);
        CREATE INDEX idx_issues_projection
            ON extraction_issues(projection_id, lifecycle_state, created_at);
        CREATE UNIQUE INDEX idx_active_projection_issue
            ON extraction_issues(projection_id, stage, code, locator_key)
            WHERE projection_id IS NOT NULL AND lifecycle_state = 'active';
        DROP TABLE extraction_issues_v1;
        """,
    )


def _migrate_v1_to_v2(connection: sqlite3.Connection) -> None:
    _validate_legacy_provenance_pairs(connection)
    _create_projection_tables(connection)
    projection_by_revision, attempt_by_revision = _legacy_projection_map(connection)
    _rebuild_source_units(connection, projection_by_revision)
    _rebuild_interpretation_queue(connection, projection_by_revision)
    _rebuild_snapshots(connection, projection_by_revision)
    _rebuild_extraction_issues(
        connection,
        projection_by_revision,
        attempt_by_revision,
    )
    _execute_transactional_script(connection, PROVENANCE_GUARD_SCHEMA)
    connection.execute("UPDATE schema_info SET version = 2")
    connection.execute("PRAGMA user_version = 2")


def _migrate_v2_to_v3(connection: sqlite3.Connection) -> None:
    _execute_transactional_script(connection, PROVENANCE_GUARD_SCHEMA)
    connection.execute("UPDATE schema_info SET version = 3")
    connection.execute("PRAGMA user_version = 3")


def _migrate_v3_to_v4(connection: sqlite3.Connection) -> None:
    # A pre-v4 revision never recorded the source ctime observed at capture.
    # Backfilling from the document's current ctime would silently assert an
    # observation that did not occur, so use a fail-closed sentinel and require
    # a fresh capture before the projection or its semantic dependants can be
    # treated as current again.
    connection.execute(
        """
        ALTER TABLE revisions
        ADD COLUMN source_changed_ns INTEGER NOT NULL DEFAULT -1
        """
    )
    connection.execute(
        """
        UPDATE interpretation_queue
        SET state = 'stale', reason = 'source_identity_requires_recapture'
        WHERE state IN ('pending', 'in_progress', 'complete')
        """
    )
    connection.execute(
        """
        UPDATE atomic_claims
        SET dependency_state = 'stale'
        WHERE dependency_state = 'valid'
          AND EXISTS (
              SELECT 1
              FROM evidence_links link
              JOIN revisions revision
                ON revision.revision_id = link.source_revision_id
              WHERE link.claim_id = atomic_claims.claim_id
                AND revision.source_changed_ns = -1
          )
        """
    )
    connection.execute("UPDATE schema_info SET version = 4")
    connection.execute("PRAGMA user_version = 4")


BACKUP_COPY_CHUNK_BYTES = 1024 * 1024


def _stream_copy_descriptor(source_descriptor: int, destination_descriptor: int) -> tuple[int, str]:
    digest = hashlib.sha256()
    copied = 0
    os.lseek(source_descriptor, 0, os.SEEK_SET)
    while True:
        chunk = os.read(source_descriptor, BACKUP_COPY_CHUNK_BYTES)
        if not chunk:
            break
        digest.update(chunk)
        view = memoryview(chunk)
        while view:
            written = os.write(destination_descriptor, view)
            if written <= 0:
                raise OSError("backup write made no progress")
            view = view[written:]
            copied += written
    return copied, digest.hexdigest()


def _hash_descriptor(descriptor: int) -> str:
    digest = hashlib.sha256()
    os.lseek(descriptor, 0, os.SEEK_SET)
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            break
        digest.update(chunk)
    return digest.hexdigest()


def _stage_streaming_backup(
    connection: sqlite3.Connection,
    *,
    backup_directory_descriptor: int,
    temporary_name: str,
    temporary_path: Path,
    backup_path: Path,
) -> tuple[int, str]:
    with tempfile.TemporaryDirectory(prefix="corpus-backup-") as temporary:
        temporary_root = Path(temporary)
        temporary_database = temporary_root / "backup.sqlite"
        with private_directory(temporary_root) as temporary_root_descriptor:
            seed_descriptor, _ = open_private_file_at(
                temporary_root_descriptor,
                temporary_database.name,
                path=temporary_database,
                flags=os.O_RDWR,
                create=True,
                exclusive=True,
            )
            seed_identity = os.fstat(seed_descriptor)
            os.close(seed_descriptor)

            destination = sqlite3.connect(temporary_database, timeout=30)
            try:
                destination.execute("PRAGMA journal_mode = DELETE")
                destination.execute("PRAGMA synchronous = FULL")
                connection.backup(destination, pages=256)
            finally:
                destination.close()

            source_descriptor, _ = open_private_file_at(
                temporary_root_descriptor,
                temporary_database.name,
                path=temporary_database,
            )
            try:
                opened_source = os.fstat(source_descriptor)
                if (
                    opened_source.st_dev != seed_identity.st_dev
                    or opened_source.st_ino != seed_identity.st_ino
                ):
                    raise MigrationError(
                        "temporary migration backup changed identity",
                        details={"backup": str(backup_path)},
                    )

                temporary_descriptor, _ = open_private_file_at(
                    backup_directory_descriptor,
                    temporary_name,
                    path=temporary_path,
                    flags=os.O_WRONLY,
                    create=True,
                    exclusive=True,
                )
                try:
                    copied, expected_digest = _stream_copy_descriptor(
                        source_descriptor,
                        temporary_descriptor,
                    )
                    os.fsync(temporary_descriptor)
                    if (
                        copied <= 0
                        or os.fstat(temporary_descriptor).st_size != copied
                    ):
                        raise MigrationError(
                            "streamed migration backup has an unexpected size",
                            details={"backup": str(backup_path)},
                        )
                finally:
                    os.close(temporary_descriptor)
            finally:
                os.close(source_descriptor)
    return copied, expected_digest


def _backup_database(
    connection: sqlite3.Connection,
    *,
    paths: RuntimePaths,
    backup_name: str,
) -> Path:
    backup_directory = paths.corpus_root / "backups"
    backup_path = backup_directory / backup_name
    temporary_name = f".{backup_name}.{uuid.uuid4().hex}.tmp"
    temporary_path = backup_directory / temporary_name

    with paths.open_corpus_root() as corpus_descriptor:
        backup_directory_descriptor = ensure_private_directory_at(
            corpus_descriptor,
            "backups",
            path=backup_directory,
        )
        try:
            try:
                copied, expected_digest = _stage_streaming_backup(
                    connection,
                    backup_directory_descriptor=backup_directory_descriptor,
                    temporary_name=temporary_name,
                    temporary_path=temporary_path,
                    backup_path=backup_path,
                )
                try:
                    os.link(
                        temporary_name,
                        backup_name,
                        src_dir_fd=backup_directory_descriptor,
                        dst_dir_fd=backup_directory_descriptor,
                        follow_symlinks=False,
                    )
                except FileExistsError as exc:
                    raise MigrationError(
                        "migration backup path already exists",
                        details={"backup": str(backup_path)},
                    ) from exc
            finally:
                with suppress(FileNotFoundError):
                    os.unlink(temporary_name, dir_fd=backup_directory_descriptor)
            os.fsync(backup_directory_descriptor)
            installed_descriptor, _ = open_private_file_at(
                backup_directory_descriptor,
                backup_name,
                path=backup_path,
            )
            try:
                installed = os.fstat(installed_descriptor)
                if (
                    installed.st_size != copied
                    or _hash_descriptor(installed_descriptor) != expected_digest
                ):
                    raise MigrationError(
                        "installed migration backup failed integrity verification",
                        details={"backup": str(backup_path)},
                    )
            finally:
                os.close(installed_descriptor)
        finally:
            os.close(backup_directory_descriptor)
    return backup_path


def migrate_corpus_database(paths: RuntimePaths) -> dict:
    normalization_connection = _connect(paths.corpus_db)
    normalization_connection.close()
    state = inspect_schema(paths.corpus_db)
    if not state.migration_required:
        return {
            "migrated": False,
            "from_version": state.current_version,
            "to_version": state.target_version,
            "backup": None,
        }
    if state.current_version not in {1, 2, 3} or state.target_version != 4:
        raise UnsupportedSchemaError(
            "no migration path is available",
            details={
                "current_version": state.current_version,
                "target_version": state.target_version,
            },
        )

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_name = (
        f"corpus-schema-v{state.current_version}-{timestamp}-"
        f"{uuid.uuid4().hex[:8]}.sqlite"
    )
    backup_path: Path | None = None
    connection = _connect(paths.corpus_db)
    try:
        backup_path = _backup_database(
            connection,
            paths=paths,
            backup_name=backup_name,
        )
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("BEGIN IMMEDIATE")
        try:
            migrated_version = state.current_version
            if migrated_version == 1:
                _migrate_v1_to_v2(connection)
                _assert_v2_structure(connection)
                migrated_version = 2
            if migrated_version == 2:
                _migrate_v2_to_v3(connection)
                _assert_v3_structure(connection)
                migrated_version = 3
            if migrated_version == 3:
                _migrate_v3_to_v4(connection)
                _assert_v4_structure(connection)
            foreign_key_issues = connection.execute("PRAGMA foreign_key_check").fetchall()
            if foreign_key_issues:
                raise MigrationError(
                    "foreign-key validation failed after migration",
                    details={"issues": [tuple(row) for row in foreign_key_issues[:20]]},
                )
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise MigrationError(
                    "SQLite integrity check failed after migration",
                    details={"result": integrity},
                )
            unit_count = connection.execute("SELECT COUNT(*) FROM source_units").fetchone()[0]
            fts_count = connection.execute("SELECT COUNT(*) FROM source_units_fts").fetchone()[0]
            missing_fts = [
                row["unit_id"]
                for row in connection.execute(
                    """
                    SELECT unit_id FROM source_units
                    EXCEPT
                    SELECT unit_id FROM source_units_fts
                    LIMIT 20
                    """
                )
            ]
            stale_fts = [
                row["unit_id"]
                for row in connection.execute(
                    """
                    SELECT unit_id FROM source_units_fts
                    EXCEPT
                    SELECT unit_id FROM source_units
                    LIMIT 20
                    """
                )
            ]
            duplicate_fts = [
                row["unit_id"]
                for row in connection.execute(
                    """
                    SELECT unit_id
                    FROM source_units_fts
                    GROUP BY unit_id
                    HAVING COUNT(*) != 1
                    LIMIT 20
                    """
                )
            ]
            if unit_count != fts_count or missing_fts or stale_fts or duplicate_fts:
                raise MigrationError(
                    "FTS and source-unit identities differ after migration",
                    details={
                        "source_units": unit_count,
                        "fts_rows": fts_count,
                        "missing_fts": missing_fts,
                        "stale_fts": stale_fts,
                        "duplicate_fts": duplicate_fts,
                    },
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        connection.execute("PRAGMA foreign_keys = ON")
    except (ConfigurationError, MigrationError, UnsupportedSchemaError):
        raise
    except Exception as exc:
        raise MigrationError(
            "corpus schema migration failed",
            details={
                "path": str(paths.corpus_db),
                "backup": str(backup_path) if backup_path is not None else None,
                "error": str(exc),
            },
        ) from exc
    finally:
        connection.close()
    return {
        "migrated": True,
        "from_version": state.current_version,
        "to_version": state.target_version,
        "backup": str(backup_path),
    }
