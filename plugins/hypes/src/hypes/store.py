"""Minimal SQLite storage for the Hypes relationship model of the user."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

from .errors import HypesError

CURRENT_SCHEMA_VERSION = 2
DATA_ROOT_MODE = 0o700
DATABASE_MODE = 0o600
DATABASE_COMPANION_SUFFIXES = ("-journal", "-shm", "-wal")
_REQUIRED_TABLES = {"nodes", "predicates", "edges", "nodes_fts", "predicates_fts"}


class UnsafeStorageError(HypesError):
    """The configured Hypes store does not meet its private-storage boundary."""

    def __init__(self, message: str) -> None:
        super().__init__("unsafe_storage", message)


def default_data_root() -> Path:
    configured = os.environ.get("HYPES_DATA_ROOT")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / "Library" / "Application Support" / "Hypes"


def _canonical(value: object) -> str:
    """Serialize a JSON value deterministically for SQLite text columns."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _current_uid() -> int | None:
    getter = getattr(os, "geteuid", None)
    return getter() if getter is not None else None


def _lstat(path: Path, *, description: str) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise UnsafeStorageError(
            f"Hypes {description} metadata is unavailable"
        ) from exc


def _require_private_path(
    metadata: os.stat_result,
    *,
    description: str,
    expected_mode: int,
    directory: bool,
) -> None:
    if stat.S_ISLNK(metadata.st_mode):
        raise UnsafeStorageError(f"Hypes {description} must not be a symbolic link")
    expected_kind = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected_kind(metadata.st_mode):
        kind = "directory" if directory else "regular file"
        raise UnsafeStorageError(f"Hypes {description} must be a {kind}")
    uid = _current_uid()
    if uid is not None and metadata.st_uid != uid:
        raise UnsafeStorageError(
            f"Hypes {description} must be owned by the current user"
        )
    if stat.S_IMODE(metadata.st_mode) != expected_mode:
        raise UnsafeStorageError(
            f"Hypes {description} must use mode {expected_mode:04o}"
        )


def _created_path_flags(*, directory: bool) -> int:
    flags = os.O_RDONLY if directory else os.O_RDWR | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    if directory:
        flags |= getattr(os, "O_DIRECTORY", 0)
    return flags


def _secure_created_path(
    path: Path,
    *,
    description: str,
    expected_mode: int,
    directory: bool,
) -> None:
    try:
        descriptor = os.open(
            path,
            _created_path_flags(directory=directory),
            expected_mode,
        )
    except FileExistsError:
        raise
    except OSError as exc:
        raise UnsafeStorageError(f"Hypes {description} could not be secured") from exc
    created: os.stat_result | None = None
    try:
        try:
            created = os.fstat(descriptor)
        except OSError as exc:
            raise UnsafeStorageError(
                f"Hypes {description} metadata could not be verified"
            ) from exc
        try:
            os.fchmod(descriptor, expected_mode)
        except OSError as exc:
            raise UnsafeStorageError(
                f"Hypes {description} permissions could not be secured"
            ) from exc
        _require_private_path(
            os.fstat(descriptor),
            description=description,
            expected_mode=expected_mode,
            directory=directory,
        )
    except Exception:
        with suppress(OSError):
            os.close(descriptor)
        descriptor = -1
        try:
            current = path.lstat()
            same_created_path = created is not None and (
                current.st_dev,
                current.st_ino,
            ) == (created.st_dev, created.st_ino)
            uid = _current_uid()
            current_is_owned = uid is None or current.st_uid == uid
            unidentified_empty_path = (
                created is None
                and current_is_owned
                and (
                    stat.S_ISDIR(current.st_mode)
                    if directory
                    else stat.S_ISREG(current.st_mode) and current.st_size == 0
                )
            )
            if same_created_path or unidentified_empty_path:
                path.rmdir() if directory else path.unlink()
        except OSError:
            pass
        raise
    finally:
        if descriptor >= 0:
            with suppress(OSError):
                os.close(descriptor)


class HypesStore:
    """Own one initialized graph database and separate read/write connections."""

    def __init__(self, data_root: Path | None = None) -> None:
        requested_root = (data_root or default_data_root()).expanduser()
        self.data_root = Path(os.path.abspath(requested_root))
        self.database_path = self.data_root / "hypes-ontology.sqlite3"

    def _prepare_data_root(self) -> None:
        metadata = _lstat(self.data_root, description="data directory")
        if metadata is not None:
            _require_private_path(
                metadata,
                description="data directory",
                expected_mode=DATA_ROOT_MODE,
                directory=True,
            )
            return
        try:
            self.data_root.mkdir(mode=DATA_ROOT_MODE, parents=True, exist_ok=False)
        except FileExistsError as exc:
            metadata = _lstat(self.data_root, description="data directory")
            if metadata is None:
                raise UnsafeStorageError(
                    "Hypes data directory changed while it was being prepared"
                ) from exc
            _require_private_path(
                metadata,
                description="data directory",
                expected_mode=DATA_ROOT_MODE,
                directory=True,
            )
            return
        except OSError as exc:
            raise UnsafeStorageError(
                "Hypes data directory could not be created"
            ) from exc
        _secure_created_path(
            self.data_root,
            description="data directory",
            expected_mode=DATA_ROOT_MODE,
            directory=True,
        )

    def _prepare_database(self) -> None:
        metadata = _lstat(self.database_path, description="database")
        if metadata is not None:
            _require_private_path(
                metadata,
                description="database",
                expected_mode=DATABASE_MODE,
                directory=False,
            )
            return
        try:
            _secure_created_path(
                self.database_path,
                description="database",
                expected_mode=DATABASE_MODE,
                directory=False,
            )
        except FileExistsError as exc:
            metadata = _lstat(self.database_path, description="database")
            if metadata is None:
                raise UnsafeStorageError(
                    "Hypes database changed while it was being prepared"
                ) from exc
            _require_private_path(
                metadata,
                description="database",
                expected_mode=DATABASE_MODE,
                directory=False,
            )

    def _require_storage_boundary(self) -> tuple[tuple[int, int], ...]:
        identities: list[tuple[int, int]] = []
        data_root = _lstat(self.data_root, description="data directory")
        if data_root is None:
            raise UnsafeStorageError("Hypes data directory is unavailable")
        _require_private_path(
            data_root,
            description="data directory",
            expected_mode=DATA_ROOT_MODE,
            directory=True,
        )
        identities.append((data_root.st_dev, data_root.st_ino))
        database = _lstat(self.database_path, description="database")
        if database is None:
            raise UnsafeStorageError("Hypes database is unavailable")
        _require_private_path(
            database,
            description="database",
            expected_mode=DATABASE_MODE,
            directory=False,
        )
        identities.append((database.st_dev, database.st_ino))
        for suffix in DATABASE_COMPANION_SUFFIXES:
            companion = _lstat(
                Path(f"{self.database_path}{suffix}"),
                description="database companion",
            )
            if companion is None:
                continue
            _require_private_path(
                companion,
                description="database companion",
                expected_mode=DATABASE_MODE,
                directory=False,
            )
        return tuple(identities)

    @staticmethod
    def _configure(connection: sqlite3.Connection, *, writable: bool) -> None:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA foreign_keys = ON")
        if writable:
            secure_delete = connection.execute("PRAGMA secure_delete = ON").fetchone()
            if secure_delete is None or secure_delete[0] != 1:
                raise RuntimeError("SQLite secure deletion is unavailable")
        else:
            connection.execute("PRAGMA query_only = ON")

    def initialize(self) -> None:
        """Create or migrate the graph schema once before serving operations."""

        self._prepare_data_root()
        self._prepare_database()
        storage_identity = self._require_storage_boundary()
        connection = sqlite3.connect(self.database_path, timeout=30.0)
        try:
            if self._require_storage_boundary() != storage_identity:
                raise UnsafeStorageError(
                    "Hypes storage changed while the database was being opened"
                )
            self._configure(connection, writable=True)
            version_row = connection.execute("PRAGMA user_version").fetchone()
            version = int(version_row[0]) if version_row is not None else 0
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if version == CURRENT_SCHEMA_VERSION and _REQUIRED_TABLES.issubset(tables):
                return
            if version not in {0, 1}:
                raise RuntimeError(
                    "the Hypes ontology database uses an unsupported schema version"
                )
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("BEGIN IMMEDIATE")
            try:
                if version == 0:
                    self._create_schema(connection)
                else:
                    self._migrate_v1(connection)
                connection.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION}")
            except Exception:
                connection.rollback()
                raise
            else:
                connection.commit()
        finally:
            connection.close()
        self._require_storage_boundary()

    @contextmanager
    def connect_read(self) -> Iterator[sqlite3.Connection]:
        """Open a true read-only connection without schema or journal writes."""

        storage_identity = self._require_storage_boundary()
        connection = sqlite3.connect(
            f"file:{self.database_path}?mode=ro",
            uri=True,
            timeout=30.0,
            isolation_level=None,
        )
        try:
            if self._require_storage_boundary() != storage_identity:
                raise UnsafeStorageError(
                    "Hypes storage changed while the database was being opened"
                )
            self._configure(connection, writable=False)
            yield connection
        finally:
            connection.close()

    @contextmanager
    def connect_write(self) -> Iterator[sqlite3.Connection]:
        """Open one write connection after initialization has completed."""

        storage_identity = self._require_storage_boundary()
        connection = sqlite3.connect(
            self.database_path,
            timeout=30.0,
            isolation_level=None,
        )
        try:
            if self._require_storage_boundary() != storage_identity:
                raise UnsafeStorageError(
                    "Hypes storage changed while the database was being opened"
                )
            self._configure(connection, writable=True)
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        self._require_storage_boundary()

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        statements = (
            """CREATE TABLE nodes (
                node_id TEXT PRIMARY KEY,
                labels_json TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                aliases_json TEXT NOT NULL,
                attributes_json TEXT NOT NULL
            )""",
            """CREATE TABLE predicates (
                predicate_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                aliases_json TEXT NOT NULL
            )""",
            """CREATE TABLE edges (
                edge_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL
                    REFERENCES nodes(node_id) ON UPDATE RESTRICT ON DELETE CASCADE,
                predicate_id TEXT NOT NULL
                    REFERENCES predicates(predicate_id) ON UPDATE RESTRICT ON DELETE CASCADE,
                target_id TEXT NOT NULL
                    REFERENCES nodes(node_id) ON UPDATE RESTRICT ON DELETE CASCADE,
                qualifiers_json TEXT NOT NULL
            )""",
            "CREATE INDEX edges_source ON edges(source_id, edge_id)",
            "CREATE INDEX edges_target ON edges(target_id, edge_id)",
            "CREATE INDEX edges_predicate ON edges(predicate_id, edge_id)",
            """CREATE VIRTUAL TABLE nodes_fts USING fts5(
                ref UNINDEXED, name, aliases, description, tokenize = 'unicode61'
            )""",
            """CREATE VIRTUAL TABLE predicates_fts USING fts5(
                ref UNINDEXED, name, aliases, description, tokenize = 'unicode61'
            )""",
            """CREATE TRIGGER nodes_fts_insert AFTER INSERT ON nodes BEGIN
                INSERT INTO nodes_fts(ref, name, aliases, description)
                VALUES (new.node_id, new.name, new.aliases_json, new.description);
            END""",
            """CREATE TRIGGER nodes_fts_update
                AFTER UPDATE OF node_id, name, aliases_json, description ON nodes BEGIN
                DELETE FROM nodes_fts WHERE ref = old.node_id;
                INSERT INTO nodes_fts(ref, name, aliases, description)
                VALUES (new.node_id, new.name, new.aliases_json, new.description);
            END""",
            """CREATE TRIGGER nodes_fts_delete AFTER DELETE ON nodes BEGIN
                DELETE FROM nodes_fts WHERE ref = old.node_id;
            END""",
            """CREATE TRIGGER predicates_fts_insert AFTER INSERT ON predicates BEGIN
                INSERT INTO predicates_fts(ref, name, aliases, description)
                VALUES (new.predicate_id, new.name, new.aliases_json, new.description);
            END""",
            """CREATE TRIGGER predicates_fts_update
                AFTER UPDATE OF predicate_id, name, aliases_json, description
                ON predicates BEGIN
                DELETE FROM predicates_fts WHERE ref = old.predicate_id;
                INSERT INTO predicates_fts(ref, name, aliases, description)
                VALUES (new.predicate_id, new.name, new.aliases_json, new.description);
            END""",
            """CREATE TRIGGER predicates_fts_delete AFTER DELETE ON predicates BEGIN
                DELETE FROM predicates_fts WHERE ref = old.predicate_id;
            END""",
        )
        for statement in statements:
            connection.execute(statement)

    @staticmethod
    def _migrate_v1(connection: sqlite3.Connection) -> None:
        connection.execute(
            """CREATE TABLE edges_v2 (
                edge_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL
                    REFERENCES nodes(node_id) ON UPDATE RESTRICT ON DELETE CASCADE,
                predicate_id TEXT NOT NULL
                    REFERENCES predicates(predicate_id) ON UPDATE RESTRICT ON DELETE CASCADE,
                target_id TEXT NOT NULL
                    REFERENCES nodes(node_id) ON UPDATE RESTRICT ON DELETE CASCADE,
                qualifiers_json TEXT NOT NULL
            )"""
        )
        connection.execute(
            "INSERT INTO edges_v2(edge_id, source_id, predicate_id, target_id, qualifiers_json) "
            "SELECT edge_id, source_id, predicate_id, target_id, qualifiers_json FROM edges"
        )
        connection.execute("DROP TABLE edges")
        connection.execute("ALTER TABLE edges_v2 RENAME TO edges")
        connection.execute("CREATE INDEX edges_source ON edges(source_id, edge_id)")
        connection.execute("CREATE INDEX edges_target ON edges(target_id, edge_id)")
        connection.execute(
            "CREATE INDEX edges_predicate ON edges(predicate_id, edge_id)"
        )

    @staticmethod
    def begin_write(connection: sqlite3.Connection) -> None:
        connection.execute("BEGIN IMMEDIATE")


__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "DATABASE_COMPANION_SUFFIXES",
    "DATABASE_MODE",
    "DATA_ROOT_MODE",
    "HypesStore",
    "UnsafeStorageError",
    "_canonical",
    "default_data_root",
]
