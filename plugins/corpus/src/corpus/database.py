"""Transactional persistence helpers."""

from __future__ import annotations

import json
import os
import sqlite3
import unicodedata
import uuid
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .config import (
    EXECUTION_POLICIES,
    RuntimePaths,
    normalize_corpus_id,
    normalize_source_scope,
    open_private_file_at,
    private_directory,
    validate_source_root,
)
from .errors import (
    ConfigurationError,
    ContextNotFoundError,
    CorpusNotFoundError,
    MigrationRequiredError,
    SpaceNotFoundError,
    UnsupportedSchemaError,
    WorkspaceNotFoundError,
)
from .migrations import (
    backup_database_to_private_subdirectory,
    inspect_schema,
    migrate_corpus_database,
    require_current_schema,
)
from .schema import (
    CATALOG_SCHEMA,
    CONTEXT_SCHEMA,
    CONTEXT_SCHEMA_VERSION,
    CORPUS_SCHEMA,
    CORPUS_SCHEMA_VERSION,
    SPACE_SCHEMA,
    SPACE_SCHEMA_VERSION,
    WORKSPACE_SCHEMA,
    WORKSPACE_SCHEMA_VERSION,
)
from .source_access import opened_source_root


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


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


def _configure_write_connection(connection: sqlite3.Connection, *, path: Path) -> None:
    connection.execute("PRAGMA busy_timeout = 30000")
    connection.execute("PRAGMA secure_delete = ON")
    secure_delete = int(connection.execute("PRAGMA secure_delete").fetchone()[0])
    if secure_delete != 1:
        raise ConfigurationError(
            "database could not enable secure row deletion",
            details={"path": str(path), "reason": "secure_delete_unavailable"},
        )
    current = str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()
    if current != "delete":
        current = str(connection.execute("PRAGMA journal_mode = DELETE").fetchone()[0]).lower()
    if current != "delete":
        raise ConfigurationError(
            "database could not enter rollback-journal mode",
            details={"path": str(path), "journal_mode": current},
        )


def connect(path: Path) -> sqlite3.Connection:
    _require_existing_database(path)
    connection = sqlite3.connect(path, timeout=30)
    try:
        connection.row_factory = sqlite3.Row
        _configure_write_connection(connection, path=path)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA synchronous = FULL")
        return connection
    except Exception:
        connection.close()
        raise


def connect_readonly(path: Path) -> sqlite3.Connection:
    _require_existing_database(path, require_rollback_journal=True)
    connection = sqlite3.connect(
        f"{path.absolute().as_uri()}?mode=ro",
        uri=True,
        timeout=30,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


@contextmanager
def _database_parent(path: Path) -> Iterator[int]:
    if path.name in {
        "catalog.sqlite",
        "contexts.sqlite3",
        "spaces.sqlite3",
        "workspaces.sqlite3",
    }:
        with private_directory(path.parent) as descriptor:
            yield descriptor
        return
    if path.name == "corpus.sqlite" and path.parent.parent.name == "corpora":
        paths = RuntimePaths(
            data_root=path.parent.parent.parent,
            corpus_id=path.parent.name,
        )
        with paths.open_corpus_root() as descriptor:
            yield descriptor
        return
    raise ConfigurationError(
        "private database location is not recognized",
        details={"path": str(path), "reason": "unrecognized_database_location"},
    )


def _require_existing_database(
    path: Path,
    *,
    corpus_id: str | None = None,
    require_rollback_journal: bool = False,
) -> None:
    try:
        with _database_parent(path) as parent_descriptor:
            descriptor, _ = open_private_file_at(
                parent_descriptor,
                path.name,
                path=path,
            )
            try:
                header = os.pread(descriptor, 100, 0)
            finally:
                os.close(descriptor)
    except ConfigurationError as exc:
        if exc.details.get("reason") != "missing":
            raise
        raise CorpusNotFoundError(
            "corpus is not registered" if corpus_id else "corpus catalog does not exist",
            details={"corpus_id": corpus_id} if corpus_id else {},
        ) from exc
    if require_rollback_journal:
        _require_rollback_journal_header(path, header)


def _ensure_private_database(path: Path, *, parent_descriptor: int) -> bool:
    descriptor, created = open_private_file_at(
        parent_descriptor,
        path.name,
        path=path,
        flags=os.O_RDWR,
        create=True,
    )
    os.close(descriptor)
    return created


def ensure_catalog(data_root: Path) -> Path:
    path = data_root / "catalog.sqlite"
    with private_directory(data_root, create=True) as parent_descriptor:
        _ensure_private_database(path, parent_descriptor=parent_descriptor)
    with closing(connect(path)) as connection, connection:
        connection.executescript(CATALOG_SCHEMA)
        columns = {
            column["name"] for column in connection.execute("PRAGMA table_info(corpora)").fetchall()
        }
        if "source_scope_json" not in columns:
            connection.execute(
                """
                ALTER TABLE corpora
                ADD COLUMN source_scope_json TEXT NOT NULL
                    DEFAULT '{"exclude_directory_names":[],"exclude_path_prefixes":[]}'
                """
            )
    return path


def ensure_corpus_db(paths: RuntimePaths) -> Path:
    paths.ensure()
    with paths.open_corpus_root() as parent_descriptor:
        created = _ensure_private_database(
            paths.corpus_db,
            parent_descriptor=parent_descriptor,
        )
    if not created:
        require_current_schema(paths.corpus_db)
        return paths.corpus_db
    with closing(connect(paths.corpus_db)) as connection, connection:
        connection.executescript(CORPUS_SCHEMA)
        connection.execute(f"PRAGMA user_version = {CORPUS_SCHEMA_VERSION}")
    return paths.corpus_db


def _require_current_context_schema(path: Path) -> None:
    with closing(connect_readonly(path)) as connection:
        user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        row = connection.execute(
            "SELECT version FROM schema_info ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
    schema_version = int(row["version"]) if row is not None else 0
    if user_version == CONTEXT_SCHEMA_VERSION and schema_version == CONTEXT_SCHEMA_VERSION:
        return
    details = {
        "path": str(path),
        "current_version": max(user_version, schema_version),
        "target_version": CONTEXT_SCHEMA_VERSION,
    }
    if user_version in {1, 2, 3, 4} and schema_version == user_version:
        details["command"] = "corpus context migrate"
        raise MigrationRequiredError(
            "context database migration is required",
            details=details,
        )
    raise UnsupportedSchemaError(
        "context database schema is not supported",
        details=details,
    )


def ensure_context_db(data_root: Path) -> Path:
    path = data_root / "contexts.sqlite3"
    with private_directory(data_root, create=True) as parent_descriptor:
        created = _ensure_private_database(path, parent_descriptor=parent_descriptor)
    if not created:
        _require_current_context_schema(path)
        return path
    with closing(connect(path)) as connection, connection:
        connection.executescript(CONTEXT_SCHEMA)
        connection.execute(f"PRAGMA user_version = {CONTEXT_SCHEMA_VERSION}")
    return path


def _require_current_workspace_schema(path: Path) -> None:
    try:
        with closing(connect_readonly(path)) as connection:
            user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            rows = connection.execute("SELECT version FROM schema_info").fetchall()
    except sqlite3.DatabaseError as exc:
        raise UnsupportedSchemaError(
            "workspace database schema is not supported",
            details={
                "path": str(path),
                "supported_version": WORKSPACE_SCHEMA_VERSION,
            },
        ) from exc
    schema_version = int(rows[0]["version"]) if len(rows) == 1 else 0
    if (
        user_version == WORKSPACE_SCHEMA_VERSION
        and schema_version == WORKSPACE_SCHEMA_VERSION
    ):
        return
    raise UnsupportedSchemaError(
        "workspace database schema is not supported",
        details={
            "path": str(path),
            "current_version": max(user_version, schema_version),
            "supported_version": WORKSPACE_SCHEMA_VERSION,
        },
    )


def ensure_workspace_db(data_root: Path) -> Path:
    path = data_root / "workspaces.sqlite3"
    with private_directory(data_root, create=True) as parent_descriptor:
        created = _ensure_private_database(path, parent_descriptor=parent_descriptor)
    if not created:
        _require_current_workspace_schema(path)
        return path
    with closing(connect(path)) as connection, connection:
        connection.executescript(WORKSPACE_SCHEMA)
        connection.execute(f"PRAGMA user_version = {WORKSPACE_SCHEMA_VERSION}")
    return path


def _require_current_space_schema(path: Path) -> None:
    try:
        with closing(connect_readonly(path)) as connection:
            user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            rows = connection.execute("SELECT version FROM schema_info").fetchall()
    except sqlite3.DatabaseError as exc:
        raise UnsupportedSchemaError(
            "space database schema is not supported",
            details={
                "path": str(path),
                "supported_version": SPACE_SCHEMA_VERSION,
            },
        ) from exc
    schema_version = int(rows[0]["version"]) if len(rows) == 1 else 0
    if user_version == SPACE_SCHEMA_VERSION and schema_version == SPACE_SCHEMA_VERSION:
        return
    raise UnsupportedSchemaError(
        "space database schema is not supported",
        details={
            "path": str(path),
            "current_version": max(user_version, schema_version),
            "supported_version": SPACE_SCHEMA_VERSION,
        },
    )


def ensure_space_db(data_root: Path) -> Path:
    path = data_root / "spaces.sqlite3"
    with private_directory(data_root, create=True) as parent_descriptor:
        created = _ensure_private_database(path, parent_descriptor=parent_descriptor)
    if not created:
        _require_current_space_schema(path)
        return path
    with closing(connect(path)) as connection, connection:
        connection.executescript(SPACE_SCHEMA)
        connection.execute(f"PRAGMA user_version = {SPACE_SCHEMA_VERSION}")
    return path


def migrate_context_database(data_root: Path) -> dict:
    """Explicitly create or migrate the private context database."""

    path = data_root / "contexts.sqlite3"
    if not path.exists():
        ensure_context_db(data_root)
        return {
            "database": "contexts",
            "from_version": 0,
            "to_version": CONTEXT_SCHEMA_VERSION,
            "migrated": True,
        }

    _require_existing_database(path, require_rollback_journal=True)
    with closing(connect(path)) as connection:
        user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        try:
            row = connection.execute(
                "SELECT version FROM schema_info ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise UnsupportedSchemaError(
                "context database schema is not supported",
                details={
                    "path": str(path),
                    "current_version": user_version,
                    "target_version": CONTEXT_SCHEMA_VERSION,
                },
            ) from exc
        schema_version = int(row["version"]) if row is not None else 0
        if user_version == CONTEXT_SCHEMA_VERSION and schema_version == CONTEXT_SCHEMA_VERSION:
            return {
                "database": "contexts",
                "from_version": CONTEXT_SCHEMA_VERSION,
                "to_version": CONTEXT_SCHEMA_VERSION,
                "migrated": False,
            }
        if user_version not in {1, 2, 3, 4} or schema_version != user_version:
            raise UnsupportedSchemaError(
                "context database schema is not supported",
                details={
                    "path": str(path),
                    "current_version": max(user_version, schema_version),
                    "target_version": CONTEXT_SCHEMA_VERSION,
                },
            )

        from_version = user_version
        with connection:
            if from_version == 1:
                columns = {
                    column["name"]
                    for column in connection.execute("PRAGMA table_info(context_items)").fetchall()
                }
                if "disclosure_state" not in columns:
                    connection.execute(
                        """
                        ALTER TABLE context_items
                        ADD COLUMN disclosure_state TEXT NOT NULL DEFAULT 'restricted'
                            CHECK (
                                disclosure_state IN ('restricted', 'general_candidate')
                            )
                        """
                    )
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS context_release_manifests (
                        release_id TEXT PRIMARY KEY,
                        context_id TEXT NOT NULL,
                        public_collection_id TEXT NOT NULL,
                        input_sha256 TEXT NOT NULL,
                        release_number INTEGER NOT NULL CHECK (release_number >= 1),
                        public_title TEXT NOT NULL,
                        public_purpose TEXT NOT NULL,
                        review_json TEXT NOT NULL,
                        state TEXT NOT NULL CHECK (state IN ('active', 'superseded')),
                        supersedes_release_id TEXT,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY(context_id)
                            REFERENCES contexts(context_id) ON DELETE CASCADE,
                        FOREIGN KEY(supersedes_release_id)
                            REFERENCES context_release_manifests(release_id)
                    );
                    CREATE UNIQUE INDEX IF NOT EXISTS
                        idx_context_release_active_context
                        ON context_release_manifests(context_id)
                        WHERE state = 'active';
                    CREATE UNIQUE INDEX IF NOT EXISTS
                        idx_context_release_active_public_collection
                        ON context_release_manifests(public_collection_id)
                        WHERE state = 'active';
                    CREATE INDEX IF NOT EXISTS idx_context_release_history
                        ON context_release_manifests(context_id, release_number);
                    CREATE TABLE IF NOT EXISTS context_release_items (
                        release_id TEXT NOT NULL,
                        item_id TEXT NOT NULL,
                        public_id TEXT NOT NULL,
                        position INTEGER NOT NULL CHECK (position >= 0),
                        PRIMARY KEY(release_id, item_id),
                        UNIQUE(release_id, public_id),
                        UNIQUE(release_id, position),
                        FOREIGN KEY(release_id)
                            REFERENCES context_release_manifests(release_id)
                            ON DELETE CASCADE,
                        FOREIGN KEY(item_id) REFERENCES context_items(item_id)
                    );
                    CREATE INDEX IF NOT EXISTS idx_context_release_items_item
                        ON context_release_items(item_id, release_id);
                    """
                )
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS corpus_source_bindings (
                    binding_id TEXT PRIMARY KEY,
                    corpus_id TEXT NOT NULL,
                    provider_kind TEXT NOT NULL,
                    selector_json TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (state IN ('active', 'archived')),
                    last_complete_run_id TEXT,
                    last_complete_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(corpus_id, provider_kind, selector_json)
                );
                CREATE INDEX IF NOT EXISTS idx_corpus_source_bindings_corpus
                    ON corpus_source_bindings(corpus_id, state, binding_id);
                CREATE TABLE IF NOT EXISTS external_source_runs (
                    run_id TEXT PRIMARY KEY,
                    binding_id TEXT NOT NULL,
                    base_complete_run_id TEXT,
                    status TEXT NOT NULL CHECK (status IN ('incomplete', 'complete')),
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    superseded_at TEXT,
                    FOREIGN KEY(binding_id)
                        REFERENCES corpus_source_bindings(binding_id) ON DELETE CASCADE,
                    FOREIGN KEY(base_complete_run_id)
                        REFERENCES external_source_runs(run_id)
                );
                CREATE INDEX IF NOT EXISTS idx_external_source_runs_binding
                    ON external_source_runs(binding_id, started_at);
                CREATE TABLE IF NOT EXISTS external_source_records (
                    source_record_id TEXT PRIMARY KEY,
                    binding_id TEXT NOT NULL,
                    external_id TEXT NOT NULL,
                    parent_external_id TEXT,
                    occurred_at TEXT,
                    title TEXT,
                    participants_json TEXT NOT NULL,
                    label_ids_json TEXT NOT NULL,
                    attachments_json TEXT NOT NULL,
                    provider_metadata_json TEXT NOT NULL DEFAULT '{}',
                    locator_json TEXT NOT NULL DEFAULT '{}',
                    freshness_identity TEXT,
                    metadata_sha256 TEXT NOT NULL,
                    membership_state TEXT NOT NULL
                        CHECK (membership_state IN ('active', 'removed')),
                    last_seen_run_id TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    UNIQUE(binding_id, external_id),
                    FOREIGN KEY(binding_id)
                        REFERENCES corpus_source_bindings(binding_id) ON DELETE CASCADE,
                    FOREIGN KEY(last_seen_run_id)
                        REFERENCES external_source_runs(run_id)
                );
                CREATE INDEX IF NOT EXISTS idx_external_source_records_binding
                    ON external_source_records(
                        binding_id, membership_state, occurred_at, external_id
                    );
                CREATE INDEX IF NOT EXISTS idx_external_source_records_parent
                    ON external_source_records(binding_id, parent_external_id);
                CREATE TABLE IF NOT EXISTS context_external_sources (
                    source_ref_id TEXT PRIMARY KEY,
                    item_id TEXT NOT NULL,
                    corpus_id TEXT NOT NULL,
                    binding_id TEXT NOT NULL,
                    source_record_id TEXT NOT NULL,
                    link_role TEXT NOT NULL
                        CHECK (link_role IN ('direct', 'context', 'contrast')),
                    observed_metadata_sha256 TEXT NOT NULL,
                    UNIQUE(item_id, source_record_id),
                    FOREIGN KEY(item_id)
                        REFERENCES context_items(item_id) ON DELETE CASCADE,
                    FOREIGN KEY(binding_id)
                        REFERENCES corpus_source_bindings(binding_id),
                    FOREIGN KEY(source_record_id)
                        REFERENCES external_source_records(source_record_id)
                );
                CREATE INDEX IF NOT EXISTS idx_context_external_sources_item
                    ON context_external_sources(item_id);
                CREATE INDEX IF NOT EXISTS idx_context_external_sources_record
                    ON context_external_sources(source_record_id, item_id);
                """
            )
            record_columns = {
                column["name"]
                for column in connection.execute(
                    "PRAGMA table_info(external_source_records)"
                ).fetchall()
            }
            if "provider_metadata_json" not in record_columns:
                connection.execute(
                    """
                    ALTER TABLE external_source_records
                    ADD COLUMN provider_metadata_json TEXT NOT NULL DEFAULT '{}'
                    """
                )
            if "locator_json" not in record_columns:
                connection.execute(
                    """
                    ALTER TABLE external_source_records
                    ADD COLUMN locator_json TEXT NOT NULL DEFAULT '{}'
                    """
                )
            if "freshness_identity" not in record_columns:
                connection.execute(
                    """
                    ALTER TABLE external_source_records
                    ADD COLUMN freshness_identity TEXT
                    """
                )
            run_columns = {
                column["name"]
                for column in connection.execute(
                    "PRAGMA table_info(external_source_runs)"
                ).fetchall()
            }
            if "base_complete_run_id" not in run_columns:
                connection.execute(
                    """
                    ALTER TABLE external_source_runs
                    ADD COLUMN base_complete_run_id TEXT
                        REFERENCES external_source_runs(run_id)
                    """
                )
            if "superseded_at" not in run_columns:
                connection.execute(
                    """
                    ALTER TABLE external_source_runs
                    ADD COLUMN superseded_at TEXT
                    """
                )
            connection.execute(
                """
                UPDATE external_source_runs AS current_run
                SET base_complete_run_id = (
                    SELECT previous_run.run_id
                    FROM external_source_runs AS previous_run
                    WHERE previous_run.binding_id = current_run.binding_id
                      AND previous_run.run_id != current_run.run_id
                      AND previous_run.status = 'complete'
                      AND previous_run.completed_at IS NOT NULL
                      AND previous_run.completed_at <= current_run.started_at
                    ORDER BY previous_run.completed_at DESC, previous_run.run_id DESC
                    LIMIT 1
                )
                WHERE current_run.base_complete_run_id IS NULL
                """
            )
            migration_time = utc_now()
            connection.execute(
                """
                UPDATE external_source_runs AS older_run
                SET superseded_at = ?
                WHERE older_run.status = 'incomplete'
                  AND older_run.superseded_at IS NULL
                  AND EXISTS (
                      SELECT 1
                      FROM external_source_runs AS newer_run
                      WHERE newer_run.binding_id = older_run.binding_id
                        AND newer_run.status = 'incomplete'
                        AND newer_run.superseded_at IS NULL
                        AND (
                            newer_run.started_at > older_run.started_at
                            OR (
                                newer_run.started_at = older_run.started_at
                                AND newer_run.run_id > older_run.run_id
                            )
                        )
                  )
                """,
                (migration_time,),
            )
            connection.execute(
                "UPDATE schema_info SET version = ?",
                (CONTEXT_SCHEMA_VERSION,),
            )
            connection.execute(f"PRAGMA user_version = {CONTEXT_SCHEMA_VERSION}")

    _require_current_context_schema(path)
    return {
        "database": "contexts",
        "from_version": from_version,
        "to_version": CONTEXT_SCHEMA_VERSION,
        "migrated": True,
    }


def migrate_corpus(data_root: Path, corpus_id: str) -> dict:
    corpus_id = normalize_corpus_id(corpus_id)
    catalog = data_root / "catalog.sqlite"
    with closing(connect(catalog)):
        pass
    get_corpus(data_root, corpus_id)
    paths = RuntimePaths(data_root=data_root, corpus_id=corpus_id)
    paths.ensure()
    return migrate_corpus_database(paths)


def corpus_schema_status(data_root: Path, corpus_id: str) -> dict:
    corpus_id = normalize_corpus_id(corpus_id)
    get_corpus(data_root, corpus_id)
    paths = RuntimePaths(data_root=data_root, corpus_id=corpus_id)
    _require_existing_database(paths.corpus_db, corpus_id=corpus_id)
    state = inspect_schema(paths.corpus_db)
    return {
        "corpus_id": corpus_id,
        "current_version": state.current_version,
        "target_version": state.target_version,
        "migration_required": state.migration_required,
    }


def register_corpus(
    *,
    data_root: Path,
    corpus_id: str,
    source_root: Path,
    execution_policy: str,
    provider_kind: str = "filesystem",
    source_scope: dict | None = None,
) -> dict:
    corpus_id = normalize_corpus_id(corpus_id)
    if execution_policy not in EXECUTION_POLICIES:
        raise ConfigurationError(
            "unsupported execution policy",
            details={"execution_policy": execution_policy, "allowed": sorted(EXECUTION_POLICIES)},
        )
    source_root = validate_source_root(source_root, data_root)
    catalog = ensure_catalog(data_root)
    now = utc_now()
    with closing(connect(catalog)) as connection, connection:
        existing = connection.execute(
            "SELECT * FROM corpora WHERE corpus_id = ?", (corpus_id,)
        ).fetchone()
        if existing and Path(existing["source_root"]) != source_root:
            raise ConfigurationError(
                "corpus id is already registered to a different source root",
                details={
                    "corpus_id": corpus_id,
                    "existing_root": existing["source_root"],
                    "requested_root": str(source_root),
                },
            )
        if source_scope is None and existing is not None:
            normalized_scope = _decode_source_scope(existing["source_scope_json"])
        elif source_scope is None:
            normalized_scope = normalize_source_scope()
        else:
            if not isinstance(source_scope, dict):
                raise ConfigurationError("source_scope must be an object")
            normalized_scope = normalize_source_scope(
                exclude_directory_names=source_scope.get(
                    "exclude_directory_names",
                    (),
                ),
                exclude_path_prefixes=source_scope.get(
                    "exclude_path_prefixes",
                    (),
                ),
            )
        source_scope_json = encode_json(normalized_scope)
        connection.execute(
            """
            INSERT INTO corpora(
                corpus_id, source_root, source_root_nfc, execution_policy,
                provider_kind, source_scope_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(corpus_id) DO UPDATE SET
                execution_policy = excluded.execution_policy,
                provider_kind = excluded.provider_kind,
                source_scope_json = excluded.source_scope_json,
                updated_at = excluded.updated_at
            """,
            (
                corpus_id,
                str(source_root),
                unicodedata.normalize("NFC", str(source_root)),
                execution_policy,
                provider_kind,
                source_scope_json,
                now,
                now,
            ),
        )
    paths = RuntimePaths(data_root=data_root, corpus_id=corpus_id)
    ensure_corpus_db(paths)
    return get_corpus(data_root, corpus_id)


def _expected_source_root_nfc(source_root: Path) -> str:
    resolved = source_root.expanduser().resolve(strict=False)
    return unicodedata.normalize("NFC", str(resolved))


def rebind_corpus_source_root(
    *,
    data_root: Path,
    corpus_id: str,
    source_root: Path,
    expected_source_root: Path,
) -> dict:
    """Replace one registered root after validation and a private catalog backup."""

    corpus_id = normalize_corpus_id(corpus_id)
    try:
        source_root = validate_source_root(source_root, data_root)
    except OSError as exc:
        raise ConfigurationError(
            "source root could not be resolved",
            details={
                "source_root": str(source_root),
                "reason": f"resolve_failed:{exc.errno}",
            },
        ) from exc
    source_root_nfc = unicodedata.normalize("NFC", str(source_root))
    expected_root_nfc = _expected_source_root_nfc(expected_source_root)
    catalog = ensure_catalog(data_root)

    with opened_source_root(source_root), closing(connect(catalog)) as connection:
        existing = connection.execute(
            "SELECT * FROM corpora WHERE corpus_id = ?", (corpus_id,)
        ).fetchone()
        if existing is None:
            raise CorpusNotFoundError(
                "corpus is not registered",
                details={"corpus_id": corpus_id},
            )
        existing_root_nfc = unicodedata.normalize(
            "NFC",
            str(existing["source_root"]),
        )
        if existing_root_nfc != expected_root_nfc:
            raise ConfigurationError(
                "registered source root does not match the expected root",
                details={
                    "corpus_id": corpus_id,
                    "existing_root": existing["source_root"],
                    "expected_root": str(expected_source_root),
                },
            )
        if existing_root_nfc == source_root_nfc:
            return {
                "changed": False,
                "previous_root": existing["source_root"],
                "source_root": str(source_root),
                "backup": None,
                "corpus": _corpus_row(existing),
            }
        conflicting = connection.execute(
            """
            SELECT corpus_id, source_root
            FROM corpora
            WHERE corpus_id != ?
              AND (source_root = ? OR source_root_nfc = ?)
            LIMIT 1
            """,
            (corpus_id, str(source_root), source_root_nfc),
        ).fetchone()
        if conflicting is not None:
            raise ConfigurationError(
                "source root is already registered to another corpus",
                details={
                    "corpus_id": corpus_id,
                    "requested_root": str(source_root),
                    "conflicting_corpus_id": conflicting["corpus_id"],
                    "conflicting_root": conflicting["source_root"],
                },
            )

        backup_name = (
            f"catalog-source-root-rebind-{corpus_id}-"
            f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}-"
            f"{uuid.uuid4().hex[:12]}.sqlite"
        )
        with private_directory(data_root) as data_root_descriptor:
            backup_path = backup_database_to_private_subdirectory(
                connection,
                parent_descriptor=data_root_descriptor,
                backup_directory_name="backups",
                backup_directory=data_root / "backups",
                backup_name=backup_name,
            )

        with connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT * FROM corpora WHERE corpus_id = ?", (corpus_id,)
            ).fetchone()
            if current is None:
                raise CorpusNotFoundError(
                    "corpus is not registered",
                    details={"corpus_id": corpus_id},
                )
            current_root_nfc = unicodedata.normalize(
                "NFC",
                str(current["source_root"]),
            )
            if current_root_nfc != expected_root_nfc:
                raise ConfigurationError(
                    "registered source root changed before rebind",
                    details={
                        "corpus_id": corpus_id,
                        "existing_root": current["source_root"],
                        "expected_root": str(expected_source_root),
                        "backup": str(backup_path),
                    },
                )
            conflicting = connection.execute(
                """
                SELECT corpus_id, source_root
                FROM corpora
                WHERE corpus_id != ?
                  AND (source_root = ? OR source_root_nfc = ?)
                LIMIT 1
                """,
                (corpus_id, str(source_root), source_root_nfc),
            ).fetchone()
            if conflicting is not None:
                raise ConfigurationError(
                    "source root became registered to another corpus before rebind",
                    details={
                        "corpus_id": corpus_id,
                        "requested_root": str(source_root),
                        "conflicting_corpus_id": conflicting["corpus_id"],
                        "conflicting_root": conflicting["source_root"],
                        "backup": str(backup_path),
                    },
                )
            connection.execute(
                """
                UPDATE corpora
                SET source_root = ?, source_root_nfc = ?, updated_at = ?
                WHERE corpus_id = ?
                """,
                (str(source_root), source_root_nfc, utc_now(), corpus_id),
            )

    return {
        "changed": True,
        "previous_root": existing["source_root"],
        "source_root": str(source_root),
        "backup": str(backup_path),
        "corpus": get_corpus(data_root, corpus_id),
    }


def configure_corpus_source_scope(
    *,
    data_root: Path,
    corpus_id: str,
    exclude_directory_names: object = (),
    exclude_path_prefixes: object = (),
) -> dict:
    corpus_id = normalize_corpus_id(corpus_id)
    catalog = ensure_catalog(data_root)
    normalized_scope = normalize_source_scope(
        exclude_directory_names=exclude_directory_names,
        exclude_path_prefixes=exclude_path_prefixes,
    )
    with closing(connect(catalog)) as connection, connection:
        updated = connection.execute(
            """
            UPDATE corpora
            SET source_scope_json = ?, updated_at = ?
            WHERE corpus_id = ?
            """,
            (encode_json(normalized_scope), utc_now(), corpus_id),
        ).rowcount
    if updated != 1:
        raise CorpusNotFoundError(
            "corpus is not registered",
            details={"corpus_id": corpus_id},
        )
    return get_corpus(data_root, corpus_id)


def _decode_source_scope(raw_scope: object) -> dict[str, list[str]]:
    try:
        parsed = json.loads(str(raw_scope))
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(
            "registered corpus source scope is invalid",
        ) from exc
    if not isinstance(parsed, dict):
        raise ConfigurationError("registered corpus source scope is invalid")
    return normalize_source_scope(
        exclude_directory_names=parsed.get("exclude_directory_names", ()),
        exclude_path_prefixes=parsed.get("exclude_path_prefixes", ()),
    )


def _corpus_row(row: sqlite3.Row) -> dict:
    corpus = dict(row)
    raw_scope = corpus.pop("source_scope_json", None)
    corpus["source_scope"] = (
        normalize_source_scope() if raw_scope is None else _decode_source_scope(raw_scope)
    )
    return corpus


def get_corpus(data_root: Path, corpus_id: str) -> dict:
    corpus_id = normalize_corpus_id(corpus_id)
    catalog = data_root / "catalog.sqlite"
    _require_existing_database(catalog, corpus_id=corpus_id)
    with closing(connect_readonly(catalog)) as connection:
        row = connection.execute(
            "SELECT * FROM corpora WHERE corpus_id = ?", (corpus_id,)
        ).fetchone()
    if row is None:
        raise CorpusNotFoundError(
            "corpus is not registered",
            details={"corpus_id": corpus_id},
        )
    return _corpus_row(row)


def list_corpora(data_root: Path) -> list[dict]:
    catalog = data_root / "catalog.sqlite"
    if not catalog.exists():
        return []
    _require_existing_database(catalog)
    with closing(connect_readonly(catalog)) as connection:
        rows = connection.execute("SELECT * FROM corpora ORDER BY corpus_id").fetchall()
    return [_corpus_row(row) for row in rows]


def list_corpora_page(
    data_root: Path,
    *,
    limit: int,
    offset: int = 0,
) -> dict[str, object]:
    """Read one bounded catalog page without materializing the full registry."""

    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ConfigurationError("corpus page limit must be a positive integer")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ConfigurationError("corpus page offset must be a non-negative integer")
    catalog = data_root / "catalog.sqlite"
    if not catalog.exists():
        return {
            "offset": offset,
            "limit": limit,
            "returned_count": 0,
            "has_more": False,
            "next_offset": None,
            "corpora": [],
        }
    _require_existing_database(catalog)
    with closing(connect_readonly(catalog)) as connection:
        rows = connection.execute(
            "SELECT * FROM corpora ORDER BY corpus_id LIMIT ? OFFSET ?",
            (limit + 1, offset),
        ).fetchall()
    has_more = len(rows) > limit
    selected = rows[:limit]
    next_offset = offset + len(selected)
    return {
        "offset": offset,
        "limit": limit,
        "returned_count": len(selected),
        "has_more": has_more,
        "next_offset": next_offset if has_more else None,
        "corpora": [_corpus_row(row) for row in selected],
    }


@contextmanager
def corpus_connection(data_root: Path, corpus_id: str) -> Iterator[sqlite3.Connection]:
    corpus_id = normalize_corpus_id(corpus_id)
    get_corpus(data_root, corpus_id)
    paths = RuntimePaths(data_root=data_root, corpus_id=corpus_id)
    ensure_corpus_db(paths)
    connection = connect(paths.corpus_db)
    try:
        connection.execute("BEGIN")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


@contextmanager
def corpus_read_connection(
    data_root: Path,
    corpus_id: str,
) -> Iterator[sqlite3.Connection]:
    corpus_id = normalize_corpus_id(corpus_id)
    get_corpus(data_root, corpus_id)
    paths = RuntimePaths(data_root=data_root, corpus_id=corpus_id)
    _require_existing_database(paths.corpus_db, corpus_id=corpus_id)
    require_current_schema(paths.corpus_db)
    connection = connect_readonly(paths.corpus_db)
    try:
        connection.execute("BEGIN")
        yield connection
    finally:
        connection.rollback()
        connection.close()


@contextmanager
def context_connection(data_root: Path) -> Iterator[sqlite3.Connection]:
    path = ensure_context_db(data_root)
    connection = connect(path)
    try:
        connection.execute("BEGIN")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


@contextmanager
def context_read_connection(data_root: Path) -> Iterator[sqlite3.Connection]:
    path = data_root / "contexts.sqlite3"
    if not path.exists():
        raise ContextNotFoundError("context database does not exist")
    _require_current_context_schema(path)
    connection = connect_readonly(path)
    try:
        connection.execute("BEGIN")
        yield connection
    finally:
        connection.rollback()
        connection.close()


@contextmanager
def space_connection(data_root: Path) -> Iterator[sqlite3.Connection]:
    path = ensure_space_db(data_root)
    connection = connect(path)
    try:
        connection.execute("BEGIN")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


@contextmanager
def space_read_connection(data_root: Path) -> Iterator[sqlite3.Connection]:
    path = data_root / "spaces.sqlite3"
    if not path.exists():
        raise SpaceNotFoundError("space database does not exist")
    _require_current_space_schema(path)
    connection = connect_readonly(path)
    try:
        connection.execute("BEGIN")
        yield connection
    finally:
        connection.rollback()
        connection.close()


@contextmanager
def workspace_connection(data_root: Path) -> Iterator[sqlite3.Connection]:
    path = ensure_workspace_db(data_root)
    connection = connect(path)
    try:
        connection.execute("BEGIN")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


@contextmanager
def workspace_read_connection(data_root: Path) -> Iterator[sqlite3.Connection]:
    path = data_root / "workspaces.sqlite3"
    if not path.exists():
        raise WorkspaceNotFoundError("workspace database does not exist")
    _require_current_workspace_schema(path)
    connection = connect_readonly(path)
    try:
        connection.execute("BEGIN")
        yield connection
    finally:
        connection.rollback()
        connection.close()


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return dict(row)


def encode_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
