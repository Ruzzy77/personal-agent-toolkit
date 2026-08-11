"""SQLite persistence for the sessionless Hypes application state.

Connections are opened per operation. Nothing here depends on an MCP connection,
server process, or request ordering. Schema upgrades happen in place before an
operation sees the database.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sqlite3
import threading
import weakref
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .errors import DeletionCleanupPending, ReplayConflict, RevisionConflict

_INITIALIZATION_LOCKS: weakref.WeakValueDictionary[str, threading.Lock] = (
    weakref.WeakValueDictionary()
)
_INITIALIZATION_LOCKS_GUARD = threading.Lock()


def _initialization_lock(database_path: Path) -> threading.Lock:
    key = str(database_path)
    with _INITIALIZATION_LOCKS_GUARD:
        lock = _INITIALIZATION_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _INITIALIZATION_LOCKS[key] = lock
        return lock


def default_data_root() -> Path:
    configured = os.environ.get("HYPES_DATA_ROOT")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / "Library" / "Application Support" / "Hypes"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def relation_ref(relation_id: str, scope: dict[str, Any]) -> str:
    """Return the durable identity for one relation in one exact scope."""

    return f"rel_{_digest({'relation_id': relation_id, 'scope': scope})}"


class HypesStore:
    def __init__(self, data_root: Path | None = None) -> None:
        self.data_root = (data_root or default_data_root()).resolve()
        self.database_path = self.data_root / "hypes.sqlite3"

    @contextmanager
    def connect(
        self, *, purge_deleted_content: bool = False
    ) -> Iterator[sqlite3.Connection]:
        self.data_root.mkdir(parents=True, exist_ok=True)
        try:
            self.data_root.chmod(0o700)
        except OSError:
            pass
        connection = sqlite3.connect(self.database_path, timeout=30.0)
        committed = False
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 30000")
            connection.execute("PRAGMA foreign_keys = ON")
            secure_delete = connection.execute(
                "PRAGMA secure_delete = ON"
            ).fetchone()
            if secure_delete is None or secure_delete[0] != 1:
                raise RuntimeError("SQLite secure deletion is unavailable")
            with _initialization_lock(self.database_path):
                connection.execute("PRAGMA journal_mode = WAL")
                self._initialize(connection)
                connection.commit()
            yield connection
            connection.commit()
            committed = True
            if purge_deleted_content:
                self._purge_deleted_content(connection)
        except Exception:
            if not committed:
                connection.rollback()
            raise
        finally:
            connection.close()
            try:
                self.database_path.chmod(0o600)
            except OSError:
                pass

    @staticmethod
    def _purge_deleted_content(connection: sqlite3.Connection) -> None:
        """Remove deleted payload bytes before a forget operation reports success.

        ``secure_delete`` clears records in new database pages, but WAL can retain an
        older frame until every reader releases its snapshot.  This hook therefore
        runs only after the deletion transaction commits, compacts legacy free pages,
        and requires a final truncating checkpoint.  A busy checkpoint is recoverable:
        the durable replay lets the caller retry the same forget request safely.
        """

        if connection.in_transaction:
            raise RuntimeError("physical deletion cleanup requires a committed transaction")
        try:
            checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if checkpoint is None or checkpoint[0] != 0:
                raise DeletionCleanupPending()
            connection.execute("VACUUM")
            checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if checkpoint is None or checkpoint[0] != 0:
                raise DeletionCleanupPending()
        except DeletionCleanupPending:
            raise
        except sqlite3.Error as exc:
            raise DeletionCleanupPending() from exc

    def _initialize(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT OR IGNORE INTO metadata(key, value) VALUES ('revision', '0')"
        )
        secret = connection.execute(
            "SELECT value FROM metadata WHERE key = 'ticket_secret'"
        ).fetchone()
        if secret is None:
            connection.execute(
                "INSERT INTO metadata(key, value) VALUES ('ticket_secret', ?)",
                (os.urandom(32).hex(),),
            )

        relation_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(relations)").fetchall()
        }
        if relation_columns and "relation_ref" not in relation_columns:
            self._migrate_legacy_03(connection)
        elif relation_columns and "retention_basis" not in relation_columns:
            self._migrate_legacy_04(connection)
        else:
            self._create_schema_05(connection)
        connection.execute("DROP TABLE IF EXISTS candidates")
        connection.execute("DELETE FROM metadata WHERE key = 'episode_secret'")
        connection.execute(
            "INSERT INTO metadata(key, value) VALUES ('schema_version', '5') "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
        )
        connection.execute("PRAGMA user_version = 5")

    @staticmethod
    def _create_schema_05(connection: sqlite3.Connection) -> None:
        # Keep schema creation inside the caller's transaction. sqlite3.executescript()
        # commits a pending transaction before running, which would make a migration
        # only partially recoverable if a later copy step failed.
        statements = (
            """CREATE TABLE IF NOT EXISTS relations (
                relation_ref TEXT PRIMARY KEY,
                relation_id TEXT NOT NULL,
                scope_json TEXT NOT NULL,
                kind TEXT NOT NULL,
                statement TEXT NOT NULL,
                explanation_pattern TEXT,
                status TEXT NOT NULL,
                retention_basis TEXT NOT NULL,
                recheck_basis TEXT,
                recheck_marked_at TEXT,
                review_after TEXT NOT NULL,
                revision INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""",
            """CREATE UNIQUE INDEX IF NOT EXISTS relations_exact_identity
                ON relations(scope_json, relation_id)""",
            """CREATE INDEX IF NOT EXISTS relations_topic
                ON relations(json_extract(scope_json, '$.topic'))""",
            """CREATE TABLE IF NOT EXISTS operation_replays (
                operation TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                request_digest TEXT NOT NULL,
                response_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (operation, idempotency_key)
            )""",
        )
        for statement in statements:
            connection.execute(statement)

    def _migrate_legacy_03(self, connection: sqlite3.Connection) -> None:
        """Preserve only active/recheck relations from the pre-0.4 schema."""

        migrated_at = _now()
        migration_review = (
            datetime.now(UTC).replace(microsecond=0) + timedelta(days=180)
        ).isoformat()
        connection.execute("ALTER TABLE relations RENAME TO legacy_relations_03")
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='observations'"
        ).fetchone():
            connection.execute("DROP TABLE observations")
        self._create_schema_05(connection)

        rows = connection.execute(
            "SELECT * FROM legacy_relations_03 ORDER BY relation_id"
        ).fetchall()
        for row in rows:
            if row["status"] not in {"active", "recheck_due"}:
                continue
            scope = json.loads(row["scope_json"])
            ref = relation_ref(row["relation_id"], scope)
            is_recheck = row["status"] == "recheck_due"
            connection.execute(
                "INSERT INTO relations(relation_ref, relation_id, scope_json, kind, "
                "statement, explanation_pattern, status, retention_basis, recheck_basis, "
                "recheck_marked_at, review_after, revision, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'legacy_confirmed', ?, ?, ?, ?, ?, ?)",
                (
                    ref,
                    row["relation_id"],
                    _canonical(scope),
                    row["kind"],
                    row["statement"],
                    row["explanation_pattern"],
                    row["status"],
                    "legacy_conflict_or_review" if is_recheck else None,
                    migrated_at if is_recheck else None,
                    migration_review,
                    row["revision"],
                    row["created_at"],
                    row["updated_at"],
                ),
            )
        connection.execute("DROP TABLE legacy_relations_03")

    def _migrate_legacy_04(self, connection: sqlite3.Connection) -> None:
        """Drop candidate history and preserve durable 0.4 relations in schema 5."""

        migrated_at = _now()
        connection.execute("ALTER TABLE relations RENAME TO legacy_relations_04")
        self._create_schema_05(connection)
        rows = connection.execute(
            "SELECT * FROM legacy_relations_04 ORDER BY relation_ref"
        ).fetchall()
        for row in rows:
            if row["status"] not in {"active", "recheck_due"}:
                continue
            basis = (
                "explicit_user_request"
                if row["confirmation_basis"]
                == "explicit_user_correction_or_confirmation"
                else "legacy_confirmed"
            )
            is_recheck = row["status"] == "recheck_due"
            connection.execute(
                "INSERT INTO relations(relation_ref, relation_id, scope_json, kind, "
                "statement, explanation_pattern, status, retention_basis, recheck_basis, "
                "recheck_marked_at, review_after, revision, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row["relation_ref"],
                    row["relation_id"],
                    row["scope_json"],
                    row["kind"],
                    row["statement"],
                    row["explanation_pattern"],
                    row["status"],
                    basis,
                    "legacy_conflict_or_review" if is_recheck else None,
                    migrated_at if is_recheck else None,
                    row["review_after"],
                    row["revision"],
                    row["created_at"],
                    row["updated_at"],
                ),
            )
        connection.execute("DROP TABLE legacy_relations_04")
        connection.execute("DROP TABLE IF EXISTS candidates")
        connection.execute(
            "DELETE FROM operation_replays WHERE operation = 'observe'"
        )

    @staticmethod
    def begin_write(connection: sqlite3.Connection) -> None:
        connection.execute("BEGIN IMMEDIATE")

    @staticmethod
    def revision(connection: sqlite3.Connection) -> int:
        row = connection.execute(
            "SELECT value FROM metadata WHERE key = 'revision'"
        ).fetchone()
        return int(row["value"])

    @staticmethod
    def require_revision(connection: sqlite3.Connection, expected: int) -> int:
        current = HypesStore.revision(connection)
        if current != expected:
            raise RevisionConflict(expected, current)
        return current

    @staticmethod
    def next_revision(connection: sqlite3.Connection) -> int:
        current = HypesStore.revision(connection)
        updated = current + 1
        connection.execute(
            "UPDATE metadata SET value = ? WHERE key = 'revision'",
            (str(updated),),
        )
        return updated

    @staticmethod
    def replay(
        connection: sqlite3.Connection,
        operation: str,
        idempotency_key: str,
        request: object,
    ) -> dict[str, Any] | None:
        row = connection.execute(
            "SELECT request_digest, response_json FROM operation_replays "
            "WHERE operation = ? AND idempotency_key = ?",
            (operation, idempotency_key),
        ).fetchone()
        if row is None:
            return None
        if row["request_digest"] != _digest(request):
            raise ReplayConflict()
        value = json.loads(row["response_json"])
        if not isinstance(value, dict):
            raise TypeError("stored replay response must be an object")
        return value

    @staticmethod
    def record_replay(
        connection: sqlite3.Connection,
        operation: str,
        idempotency_key: str,
        request: object,
        response: dict[str, Any],
    ) -> None:
        connection.execute(
            "INSERT INTO operation_replays(operation, idempotency_key, request_digest, "
            "response_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (
                operation,
                idempotency_key,
                _digest(request),
                _canonical(response),
                _now(),
            ),
        )

    @staticmethod
    def sign_ticket(connection: sqlite3.Connection, payload: dict[str, Any]) -> str:
        raw = _canonical(payload).encode("utf-8")
        encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
        row = connection.execute(
            "SELECT value FROM metadata WHERE key = 'ticket_secret'"
        ).fetchone()
        signature = hmac.new(
            bytes.fromhex(row["value"]), encoded.encode("ascii"), hashlib.sha256
        ).hexdigest()
        return f"{encoded}.{signature}"

    @staticmethod
    def verify_ticket(
        connection: sqlite3.Connection, ticket: str
    ) -> dict[str, Any] | None:
        try:
            encoded, supplied = ticket.split(".", 1)
            row = connection.execute(
                "SELECT value FROM metadata WHERE key = 'ticket_secret'"
            ).fetchone()
            expected = hmac.new(
                bytes.fromhex(row["value"]), encoded.encode("ascii"), hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(supplied, expected):
                return None
            raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            payload = json.loads(raw)
            return payload if isinstance(payload, dict) else None
        except (ValueError, TypeError, json.JSONDecodeError):
            return None


__all__ = [
    "HypesStore",
    "_canonical",
    "_digest",
    "_now",
    "default_data_root",
    "relation_ref",
]
