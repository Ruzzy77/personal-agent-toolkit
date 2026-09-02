"""Private local state for locators, change coalescing, approvals, and replay."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import ConnectionConfig, SyncConfig
from .errors import SyncError
from .paths import resolve_moved_root

MAX_PENDING_CHANGES = 10_000
MAX_COMPLETED_JOBS = 2_048


def now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class SyncState:
    def __init__(self, config: SyncConfig) -> None:
        self.config = config
        self.path = config.data_root / "sync.sqlite3"
        self._prepare_database()
        self._initialize()
        self.register_connections(config.connections)

    def _prepare_database(self) -> None:
        if not self.path.exists():
            descriptor = os.open(
                self.path,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
                0o600,
            )
            os.close(descriptor)
        metadata = self.path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise SyncError(
                "unsafe_storage", "Sync state must be a regular private file"
            )
        if stat.S_IMODE(metadata.st_mode) != 0o600:
            os.chmod(self.path, 0o600)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA secure_delete = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS connections (
                    connection_key TEXT PRIMARY KEY,
                    space_id TEXT NOT NULL,
                    connection_id TEXT NOT NULL,
                    root_path TEXT NOT NULL,
                    root_device INTEGER NOT NULL,
                    root_inode INTEGER NOT NULL,
                    access_scope TEXT NOT NULL,
                    permission TEXT NOT NULL,
                    roles_json TEXT NOT NULL,
                    corpus_id TEXT,
                    analyzer_route TEXT NOT NULL,
                    max_transfer_bytes INTEGER NOT NULL,
                    generation INTEGER NOT NULL DEFAULT 1,
                    location_state TEXT NOT NULL DEFAULT 'available',
                    updated_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_connections_identity
                    ON connections(root_device, root_inode, connection_key);

                CREATE TABLE IF NOT EXISTS documents (
                    connection_key TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    local_relative_path TEXT NOT NULL,
                    relative_path_nfc TEXT NOT NULL,
                    device INTEGER NOT NULL,
                    inode INTEGER NOT NULL,
                    size INTEGER NOT NULL,
                    modified_ns INTEGER NOT NULL,
                    changed_ns INTEGER NOT NULL,
                    last_revision_sha256 TEXT,
                    last_projection_id TEXT,
                    last_seen_at TEXT NOT NULL,
                    missing_since TEXT,
                    PRIMARY KEY(connection_key, document_id),
                    UNIQUE(connection_key, device, inode),
                    UNIQUE(connection_key, relative_path_nfc),
                    FOREIGN KEY(connection_key) REFERENCES connections(connection_key)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS change_queue (
                    connection_key TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    event_kind TEXT NOT NULL,
                    relative_path_nfc TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at TEXT NOT NULL,
                    last_error_code TEXT,
                    PRIMARY KEY(connection_key, document_id),
                    FOREIGN KEY(connection_key, document_id)
                        REFERENCES documents(connection_key, document_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_change_queue_due
                    ON change_queue(next_attempt_at, first_seen_at);

                CREATE TABLE IF NOT EXISTS remote_approvals (
                    connection_key TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    revision_sha256 TEXT NOT NULL,
                    max_bytes INTEGER NOT NULL,
                    approved_at TEXT NOT NULL,
                    PRIMARY KEY(connection_key, document_id, revision_sha256),
                    FOREIGN KEY(connection_key, document_id)
                        REFERENCES documents(connection_key, document_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS completed_jobs (
                    job_id TEXT PRIMARY KEY,
                    request_sha256 TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    completed_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS migration_progress (
                    product TEXT NOT NULL,
                    item_key TEXT NOT NULL,
                    source_digest TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    PRIMARY KEY(product, item_key)
                );
                PRAGMA user_version = 1;
                """
            )

    def register_connections(self, values: tuple[ConnectionConfig, ...]) -> None:
        now = now_iso()
        with self.connect() as connection:
            for value in values:
                current = connection.execute(
                    "SELECT root_path, root_device, root_inode FROM connections WHERE connection_key = ?",
                    (value.key,),
                ).fetchone()
                try:
                    metadata = value.root.stat()
                except OSError:
                    metadata = None
                if current is None and metadata is None:
                    raise SyncError(
                        "source_unavailable",
                        "a new Connection root must be available for initial registration",
                    )
                if current is not None:
                    identity = (int(current["root_device"]), int(current["root_inode"]))
                    if (
                        metadata is None
                        or (metadata.st_dev, metadata.st_ino) != identity
                    ):
                        recovered = resolve_moved_root(
                            Path(current["root_path"]), identity[0], identity[1]
                        )
                        if recovered is None:
                            connection.execute(
                                """
                                UPDATE connections SET
                                    space_id = ?, connection_id = ?, access_scope = ?,
                                    permission = ?, roles_json = ?, corpus_id = ?,
                                    analyzer_route = ?, max_transfer_bytes = ?,
                                    generation = ?, location_state = 'unavailable',
                                    updated_at = ?
                                WHERE connection_key = ?
                                """,
                                (
                                    value.space_id,
                                    value.connection_id,
                                    value.access_scope,
                                    value.permission,
                                    canonical(sorted(value.roles)),
                                    value.corpus_id,
                                    value.analyzer_route,
                                    value.max_transfer_bytes,
                                    value.generation,
                                    now,
                                    value.key,
                                ),
                            )
                            continue
                        metadata = recovered.stat()
                        root_path = recovered
                    else:
                        root_path = value.root
                else:
                    root_path = value.root
                assert metadata is not None
                connection.execute(
                    """
                    INSERT INTO connections(
                        connection_key, space_id, connection_id, root_path,
                        root_device, root_inode, access_scope, permission, roles_json,
                        corpus_id, analyzer_route, max_transfer_bytes, generation,
                        location_state, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'available', ?)
                    ON CONFLICT(connection_key) DO UPDATE SET
                        space_id=excluded.space_id,
                        connection_id=excluded.connection_id,
                        root_path=excluded.root_path,
                        access_scope=excluded.access_scope,
                        permission=excluded.permission,
                        roles_json=excluded.roles_json,
                        corpus_id=excluded.corpus_id,
                        analyzer_route=excluded.analyzer_route,
                        max_transfer_bytes=excluded.max_transfer_bytes,
                        generation=excluded.generation,
                        location_state='available', updated_at=excluded.updated_at
                    """,
                    (
                        value.key,
                        value.space_id,
                        value.connection_id,
                        str(root_path),
                        metadata.st_dev,
                        metadata.st_ino,
                        value.access_scope,
                        value.permission,
                        canonical(sorted(value.roles)),
                        value.corpus_id,
                        value.analyzer_route,
                        value.max_transfer_bytes,
                        value.generation,
                        now,
                    ),
                )

    def seed_documents(
        self, key: str, documents: list[Mapping[str, object]]
    ) -> dict[str, int]:
        seeded = 0
        queued = 0
        now = now_iso()
        with self.connect() as connection:
            for document in documents:
                path_record = connection.execute(
                    "SELECT document_id FROM documents "
                    "WHERE connection_key = ? AND relative_path_nfc = ?",
                    (key, document["relative_path_nfc"]),
                ).fetchone()
                if (
                    path_record is not None
                    and path_record["document_id"] != document["document_id"]
                ):
                    connection.execute(
                        "DELETE FROM documents "
                        "WHERE connection_key = ? AND document_id = ?",
                        (key, path_record["document_id"]),
                    )
                identity = connection.execute(
                    "SELECT document_id, relative_path_nfc FROM documents "
                    "WHERE connection_key = ? AND device = ? AND inode = ?",
                    (key, document["device"], document["inode"]),
                ).fetchone()
                if (
                    identity is not None
                    and identity["document_id"] != document["document_id"]
                ):
                    if identity["relative_path_nfc"] != document["relative_path_nfc"]:
                        raise SyncError(
                            "migration_identity_conflict",
                            "a local file identity already belongs to a different document",
                        )
                    connection.execute(
                        "DELETE FROM documents "
                        "WHERE connection_key = ? AND document_id = ?",
                        (key, identity["document_id"]),
                    )
                connection.execute(
                    """
                    INSERT INTO documents(
                        connection_key, document_id, local_relative_path,
                        relative_path_nfc, device, inode, size, modified_ns,
                        changed_ns, last_revision_sha256, last_projection_id,
                        last_seen_at, missing_since
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                    ON CONFLICT(connection_key, document_id) DO UPDATE SET
                        local_relative_path=excluded.local_relative_path,
                        relative_path_nfc=excluded.relative_path_nfc,
                        device=excluded.device, inode=excluded.inode,
                        size=excluded.size, modified_ns=excluded.modified_ns,
                        changed_ns=excluded.changed_ns,
                        last_revision_sha256=excluded.last_revision_sha256,
                        last_projection_id=excluded.last_projection_id,
                        last_seen_at=excluded.last_seen_at, missing_since=NULL
                    """,
                    (
                        key,
                        document["document_id"],
                        document["relative_path"],
                        document["relative_path_nfc"],
                        document["device"],
                        document["inode"],
                        document["size"],
                        document["modified_ns"],
                        document["changed_ns"],
                        document.get("last_revision_sha256"),
                        document.get("last_projection_id"),
                        now,
                    ),
                )
                seeded += 1
                if document.get("needs_refresh") is True:
                    self._enqueue(
                        connection,
                        key,
                        str(document["document_id"]),
                        "reconcile",
                        str(document["relative_path_nfc"]),
                        now,
                    )
                    queued += 1
        return {"seeded": seeded, "queued": queued}

    def connection_row(self, key: str) -> sqlite3.Row:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM connections WHERE connection_key = ?", (key,)
            ).fetchone()
        if row is None:
            raise SyncError(
                "connection_not_found", "local Connection is not registered"
            )
        return row

    def connection_for_scope(self, space_id: str, connection_id: str) -> sqlite3.Row:
        return self.connection_row(f"{space_id}:{connection_id}")

    def update_root_path(self, key: str, root: Path) -> None:
        metadata = root.stat()
        with self.connect() as connection:
            current = connection.execute(
                "SELECT root_device, root_inode FROM connections WHERE connection_key = ?",
                (key,),
            ).fetchone()
            if current is None:
                raise SyncError(
                    "connection_not_found", "local Connection is not registered"
                )
            if (metadata.st_dev, metadata.st_ino) != (
                current["root_device"],
                current["root_inode"],
            ):
                raise SyncError(
                    "connection_identity_changed",
                    "recovered root identity does not match",
                )
            connection.execute(
                "UPDATE connections SET root_path = ?, location_state = 'available', updated_at = ? WHERE connection_key = ?",
                (str(root), now_iso(), key),
            )

    def set_location_state(self, key: str, state: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE connections SET location_state = ?, updated_at = ? WHERE connection_key = ?",
                (state, now_iso(), key),
            )

    @staticmethod
    def _document_id(key: str, device: int, inode: int) -> str:
        namespace = uuid.UUID("19ace14f-7a24-4e59-9c7e-0349e96eecbb")
        return f"doc_{uuid.uuid5(namespace, f'{key}:{device}:{inode}').hex}"

    def observe_file(
        self,
        key: str,
        relative_path: str,
        metadata: os.stat_result,
    ) -> tuple[str, str]:
        relative_nfc = relative_path.replace(os.sep, "/")
        relative_nfc = __import__("unicodedata").normalize("NFC", relative_nfc)
        if relative_nfc.startswith("/") or ".." in relative_nfc.split("/"):
            raise SyncError("unsafe_relative_path", "observed file path is unsafe")
        now = now_iso()
        with self.connect() as connection:
            by_identity = connection.execute(
                """
                SELECT * FROM documents
                WHERE connection_key = ? AND device = ? AND inode = ?
                """,
                (key, metadata.st_dev, metadata.st_ino),
            ).fetchone()
            event = "created"
            if by_identity is not None:
                document_id = by_identity["document_id"]
                if by_identity["relative_path_nfc"] != relative_nfc:
                    event = "moved"
                elif (
                    by_identity["size"],
                    by_identity["modified_ns"],
                    by_identity["changed_ns"],
                ) != (metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns):
                    event = "changed"
                else:
                    connection.execute(
                        """
                        UPDATE documents SET last_seen_at = ?, missing_since = NULL
                        WHERE connection_key = ? AND document_id = ?
                        """,
                        (now, key, document_id),
                    )
                    return document_id, "unchanged"
            else:
                document_id = self._document_id(key, metadata.st_dev, metadata.st_ino)
                conflicting = connection.execute(
                    """
                    SELECT document_id FROM documents
                    WHERE connection_key = ? AND relative_path_nfc = ?
                    """,
                    (key, relative_nfc),
                ).fetchone()
                if conflicting is not None:
                    # Replacement at the same path is a new document identity. Keep the old
                    # record detached so its last committed projection remains addressable.
                    connection.execute(
                        "UPDATE documents SET relative_path_nfc = relative_path_nfc || '.detached.' || document_id, missing_since = ? WHERE connection_key = ? AND document_id = ?",
                        (now, key, conflicting["document_id"]),
                    )
            connection.execute(
                """
                    INSERT INTO documents(
                    connection_key, document_id, local_relative_path, relative_path_nfc, device, inode,
                    size, modified_ns, changed_ns, last_seen_at, missing_since
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                ON CONFLICT(connection_key, document_id) DO UPDATE SET
                    local_relative_path=excluded.local_relative_path,
                    relative_path_nfc=excluded.relative_path_nfc,
                    size=excluded.size,
                    modified_ns=excluded.modified_ns,
                    changed_ns=excluded.changed_ns,
                    last_seen_at=excluded.last_seen_at,
                    missing_since=NULL
                """,
                (
                    key,
                    document_id,
                    relative_path.replace(os.sep, "/"),
                    relative_nfc,
                    metadata.st_dev,
                    metadata.st_ino,
                    metadata.st_size,
                    metadata.st_mtime_ns,
                    metadata.st_ctime_ns,
                    now,
                ),
            )
            self._enqueue(connection, key, document_id, event, relative_nfc, now)
        return document_id, event

    def _enqueue(
        self,
        connection: sqlite3.Connection,
        key: str,
        document_id: str,
        event: str,
        relative_path: str,
        now: str,
    ) -> None:
        count = connection.execute("SELECT COUNT(*) FROM change_queue").fetchone()[0]
        exists = connection.execute(
            "SELECT 1 FROM change_queue WHERE connection_key = ? AND document_id = ?",
            (key, document_id),
        ).fetchone()
        if not exists and count >= MAX_PENDING_CHANGES:
            raise SyncError("change_queue_full", "local Source change queue is full")
        connection.execute(
            """
            INSERT INTO change_queue(
                connection_key, document_id, event_kind, relative_path_nfc,
                first_seen_at, last_seen_at, attempt_count, next_attempt_at
            ) VALUES (?, ?, ?, ?, ?, ?, 0, ?)
            ON CONFLICT(connection_key, document_id) DO UPDATE SET
                event_kind=excluded.event_kind,
                relative_path_nfc=excluded.relative_path_nfc,
                last_seen_at=excluded.last_seen_at,
                next_attempt_at=excluded.next_attempt_at,
                last_error_code=NULL
            """,
            (key, document_id, event, relative_path, now, now, now),
        )

    def mark_missing(self, key: str, seen_document_ids: set[str]) -> None:
        now = now_iso()
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT document_id, relative_path_nfc, missing_since FROM documents WHERE connection_key = ?",
                (key,),
            ).fetchall()
            for row in rows:
                if (
                    row["document_id"] in seen_document_ids
                    or row["missing_since"] is not None
                ):
                    continue
                connection.execute(
                    "UPDATE documents SET missing_since = ? WHERE connection_key = ? AND document_id = ?",
                    (now, key, row["document_id"]),
                )
                self._enqueue(
                    connection,
                    key,
                    row["document_id"],
                    "deleted",
                    row["relative_path_nfc"],
                    now,
                )

    def due_changes(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT q.*, d.local_relative_path, d.size, d.modified_ns, d.changed_ns,
                       d.last_revision_sha256, d.last_projection_id,
                       c.root_path, c.root_device, c.root_inode, c.corpus_id,
                       c.analyzer_route, c.max_transfer_bytes, c.access_scope
                FROM change_queue q
                JOIN documents d USING(connection_key, document_id)
                JOIN connections c USING(connection_key)
                WHERE q.next_attempt_at <= ?
                ORDER BY q.first_seen_at LIMIT ?
                """,
                (now_iso(), limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def complete_change(
        self, key: str, document_id: str, revision: str, projection: str
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE documents SET last_revision_sha256 = ?, last_projection_id = ?
                WHERE connection_key = ? AND document_id = ?
                """,
                (revision, projection, key, document_id),
            )
            connection.execute(
                "DELETE FROM change_queue WHERE connection_key = ? AND document_id = ?",
                (key, document_id),
            )

    def complete_missing(self, key: str, document_id: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "DELETE FROM change_queue WHERE connection_key = ? AND document_id = ?",
                (key, document_id),
            )

    def fail_change(self, key: str, document_id: str, code: str) -> None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT attempt_count FROM change_queue WHERE connection_key = ? AND document_id = ?",
                (key, document_id),
            ).fetchone()
            if row is None:
                return
            attempts = int(row["attempt_count"]) + 1
            delay = min(3600, 2 ** min(attempts, 10))
            next_at = (
                datetime.fromtimestamp(datetime.now(UTC).timestamp() + delay, UTC)
                .isoformat()
                .replace("+00:00", "Z")
            )
            connection.execute(
                """
                UPDATE change_queue SET attempt_count = ?, next_attempt_at = ?,
                    last_error_code = ? WHERE connection_key = ? AND document_id = ?
                """,
                (attempts, next_at, code[:120], key, document_id),
            )

    def approve_remote(
        self, key: str, document_id: str, revision_sha256: str, max_bytes: int
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO remote_approvals(
                    connection_key, document_id, revision_sha256, max_bytes, approved_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(connection_key, document_id, revision_sha256) DO UPDATE SET
                    max_bytes=excluded.max_bytes, approved_at=excluded.approved_at
                """,
                (key, document_id, revision_sha256, max_bytes, now_iso()),
            )

    def remote_approved(
        self, key: str, document_id: str, revision_sha256: str, size: int
    ) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT max_bytes FROM remote_approvals
                WHERE connection_key = ? AND document_id = ? AND revision_sha256 = ?
                """,
                (key, document_id, revision_sha256),
            ).fetchone()
        return row is not None and size <= int(row["max_bytes"])

    @staticmethod
    def request_digest(value: Mapping[str, object]) -> str:
        return hashlib.sha256(canonical(value).encode()).hexdigest()

    def completed_job(
        self, job_id: str, request: Mapping[str, object]
    ) -> dict[str, Any] | None:
        digest = self.request_digest(request)
        with self.connect() as connection:
            row = connection.execute(
                "SELECT request_sha256, response_json FROM completed_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            return None
        if row["request_sha256"] != digest:
            raise SyncError(
                "job_replay_conflict", "job id was replayed with a different request"
            )
        return json.loads(row["response_json"])

    def remember_job(
        self, job_id: str, request: Mapping[str, object], response: Mapping[str, object]
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO completed_jobs(job_id, request_sha256, response_json, completed_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(job_id) DO NOTHING
                """,
                (job_id, self.request_digest(request), canonical(response), now_iso()),
            )
            connection.execute(
                """
                DELETE FROM completed_jobs
                WHERE job_id IN (
                    SELECT job_id FROM completed_jobs
                    ORDER BY completed_at DESC, job_id DESC
                    LIMIT -1 OFFSET ?
                )
                """,
                (MAX_COMPLETED_JOBS,),
            )

    def migration_result(
        self, product: str, item_key: str, source_digest: str
    ) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT source_digest, result_json FROM migration_progress
                WHERE product = ? AND item_key = ?
                """,
                (product, item_key),
            ).fetchone()
        if row is None or row["source_digest"] != source_digest:
            return None
        value = json.loads(row["result_json"])
        return value if isinstance(value, dict) else None

    def remember_migration(
        self,
        product: str,
        item_key: str,
        source_digest: str,
        result: Mapping[str, object],
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO migration_progress(
                    product, item_key, source_digest, result_json, completed_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(product, item_key) DO UPDATE SET
                    source_digest=excluded.source_digest,
                    result_json=excluded.result_json,
                    completed_at=excluded.completed_at
                """,
                (product, item_key, source_digest, canonical(result), now_iso()),
            )
