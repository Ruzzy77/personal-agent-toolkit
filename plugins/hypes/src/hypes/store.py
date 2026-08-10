"""SQLite persistence for explicit Hypes application state.

Connections are opened per operation. Nothing here depends on an MCP connection,
server process, or request ordering.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .errors import ReplayConflict, RevisionConflict


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


class HypesStore:
    def __init__(self, data_root: Path | None = None) -> None:
        self.data_root = (data_root or default_data_root()).resolve()
        self.database_path = self.data_root / "hypes.sqlite3"

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.data_root.mkdir(parents=True, exist_ok=True)
        try:
            self.data_root.chmod(0o700)
        except OSError:
            pass
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        self._initialize(connection)
        connection.commit()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        try:
            self.database_path.chmod(0o600)
        except OSError:
            pass

    def _initialize(self, connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS relations (
                relation_id TEXT PRIMARY KEY,
                scope_json TEXT NOT NULL,
                kind TEXT NOT NULL,
                statement TEXT NOT NULL,
                explanation_pattern TEXT,
                status TEXT NOT NULL,
                evidence_kinds_json TEXT NOT NULL,
                episode_digests_json TEXT NOT NULL,
                revision INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS observations (
                observation_id TEXT PRIMARY KEY,
                relation_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_digest TEXT NOT NULL,
                episode_digest TEXT NOT NULL,
                evidence_kind TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS operation_replays (
                operation TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                request_digest TEXT NOT NULL,
                response_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (operation, idempotency_key)
            );
            """
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
    def episode_digest(connection: sqlite3.Connection, episode_key: str) -> str:
        row = connection.execute(
            "SELECT value FROM metadata WHERE key = 'ticket_secret'"
        ).fetchone()
        return hmac.new(
            bytes.fromhex(row["value"]),
            episode_key.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

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
    def verify_ticket(connection: sqlite3.Connection, ticket: str) -> dict[str, Any] | None:
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


__all__ = ["HypesStore", "_now", "default_data_root"]
