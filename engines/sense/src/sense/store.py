"""Private single-state SQLite storage for Sense."""

from __future__ import annotations

import fcntl
import json
import os
import sqlite3
import stat
import time
import unicodedata
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .errors import (
    ConfirmationRequiredError,
    ProfileBusyError,
    ProfileExistsError,
    ProfileNotFoundError,
    SectionConflictError,
    SectionNotFoundError,
    UnsafeStorageError,
)
from .model import (
    MAX_CHANGES,
    ProfileDocument,
    ProfileSection,
    SectionChange,
    canonical_json_bytes,
    content_sha256,
    section_sha256,
)

PRIVATE_DIRECTORY_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
DATA_ROOT_ENV = "SENSE_DATA_DIR"
DATABASE_NAME = "sense.sqlite3"
LOCK_NAME = "runtime.lock"
REMOVABLE_NAMES = {
    DATABASE_NAME,
    f"{DATABASE_NAME}-journal",
    f"{DATABASE_NAME}-wal",
    f"{DATABASE_NAME}-shm",
}
LOCK_TIMEOUT_SECONDS = 2.0
SQLITE_BUSY_TIMEOUT_MS = 2000
DATABASE_SCHEMA_VERSION = 2

CURRENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS current_profile (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    profile_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

LEGACY_SECTION_IDS = {
    "working-together": "questions-and-choices",
    "work-process": "scope-and-checking",
    "learning-across-work": "what-to-keep",
}


@dataclass(frozen=True)
class StoredProfile:
    profile: ProfileDocument
    digest: str
    updated_at: str


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def default_data_root() -> Path:
    configured = os.environ.get(DATA_ROOT_ENV)
    raw = (
        Path(configured)
        if configured
        else Path.home() / "Library/Application Support/Sense"
    )
    expanded = raw.expanduser()
    if not expanded.is_absolute():
        raise UnsafeStorageError(
            f"{DATA_ROOT_ENV} must be an absolute path",
            details={"environment_variable": DATA_ROOT_ENV},
        )
    return Path(unicodedata.normalize("NFC", os.path.normpath(str(expanded))))


def _reject_symlink(path: Path) -> None:
    if path.is_symlink():
        raise UnsafeStorageError(
            "Sense private storage must not be a symbolic link",
            details={"name": path.name},
        )


def _ensure_private_directory(path: Path) -> None:
    if path.exists():
        _reject_symlink(path)
        metadata = path.stat()
        if not stat.S_ISDIR(metadata.st_mode):
            raise UnsafeStorageError("Sense data root must be a directory")
        if metadata.st_uid != os.getuid():
            raise UnsafeStorageError(
                "Sense data root must be owned by the current user"
            )
    else:
        path.mkdir(parents=True, mode=PRIVATE_DIRECTORY_MODE)
    path.chmod(PRIVATE_DIRECTORY_MODE)


def _ensure_private_file(path: Path) -> None:
    _reject_symlink(path)
    descriptor = os.open(
        path,
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        PRIVATE_FILE_MODE,
    )
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise UnsafeStorageError("Sense private storage must use regular files")
        if metadata.st_uid != os.getuid():
            raise UnsafeStorageError(
                "Sense private files must be owned by the current user"
            )
        os.fchmod(descriptor, PRIVATE_FILE_MODE)
    finally:
        os.close(descriptor)


class _LockedConnection(sqlite3.Connection):
    """SQLite connection that holds the Sense process lock until close."""

    _sense_lock_descriptor: int | None = None

    def attach_sense_lock(self, descriptor: int) -> None:
        self._sense_lock_descriptor = descriptor

    def close(self) -> None:
        descriptor = self._sense_lock_descriptor
        self._sense_lock_descriptor = None
        try:
            super().close()
        finally:
            if descriptor is not None:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)


class SenseStore:
    def __init__(self, data_root: Path | None = None) -> None:
        self.data_root = data_root or default_data_root()
        if not self.data_root.is_absolute():
            raise UnsafeStorageError("Sense data root must be absolute")
        if self.data_root.name.casefold() not in {"sense", ".sense"}:
            raise UnsafeStorageError(
                "Sense storage must use a dedicated Sense directory",
                details={"required_leaf_name": "Sense"},
            )
        self.database_path = self.data_root / DATABASE_NAME
        self.lock_path = self.data_root / LOCK_NAME

    def _prepare_write_paths(self, *, create_database: bool) -> None:
        if (
            create_database
            and self.data_root.exists()
            and not self.lock_path.exists()
            and not self.database_path.exists()
            and any(self.data_root.iterdir())
        ):
            raise UnsafeStorageError("Sense will not take over a non-empty directory")
        _ensure_private_directory(self.data_root)
        _ensure_private_file(self.lock_path)
        if create_database:
            _ensure_private_file(self.database_path)
        elif self.database_path.exists():
            _reject_symlink(self.database_path)

    def _acquire_lock(self, *, exclusive: bool, create: bool) -> int:
        if create:
            self._prepare_write_paths(create_database=True)
        else:
            if not self.database_path.is_file():
                raise ProfileNotFoundError("Sense data has not been created")
            if not self.lock_path.is_file():
                raise UnsafeStorageError("Sense runtime lock is missing")
            _reject_symlink(self.lock_path)
        descriptor = os.open(
            self.lock_path,
            os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
        )
        operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        while True:
            try:
                fcntl.flock(descriptor, operation | fcntl.LOCK_NB)
                return descriptor
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    os.close(descriptor)
                    raise ProfileBusyError(
                        "Sense is busy in another process; retry after that operation finishes"
                    ) from None
                time.sleep(0.02)

    def _connect_write(self, *, create: bool = False) -> sqlite3.Connection:
        if not create and not self.database_path.is_file():
            raise ProfileNotFoundError("Sense data has not been created")
        lock_descriptor = self._acquire_lock(exclusive=True, create=create)
        try:
            connection = sqlite3.connect(
                self.database_path,
                timeout=10,
                isolation_level=None,
                factory=_LockedConnection,
            )
        except Exception:
            fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
            os.close(lock_descriptor)
            raise
        connection.attach_sense_lock(lock_descriptor)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA secure_delete = ON")
            mode = connection.execute("PRAGMA journal_mode = DELETE").fetchone()
            if mode is None or str(mode[0]).casefold() != "delete":
                raise ProfileBusyError(
                    "Sense could not enter its exclusive update mode"
                )
            connection.execute("PRAGMA synchronous = FULL")
            self._secure_runtime_files()
        except sqlite3.OperationalError as exc:
            connection.close()
            if "locked" in str(exc).casefold() or "busy" in str(exc).casefold():
                raise ProfileBusyError(
                    "Sense is busy in another process; retry after that operation finishes"
                ) from None
            raise
        except Exception:
            connection.close()
            raise
        return connection

    def _connect_read(self) -> sqlite3.Connection:
        if not self.database_path.is_file():
            raise ProfileNotFoundError("Sense data has not been created")
        _reject_symlink(self.database_path)
        lock_descriptor = self._acquire_lock(exclusive=False, create=False)
        try:
            connection = sqlite3.connect(
                f"file:{self.database_path}?mode=ro",
                uri=True,
                timeout=10,
                isolation_level=None,
                factory=_LockedConnection,
            )
        except Exception:
            fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
            os.close(lock_descriptor)
            raise
        connection.attach_sense_lock(lock_descriptor)
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
        connection.execute("PRAGMA query_only = ON")
        return connection

    def _secure_runtime_files(self) -> None:
        for path in (
            self.database_path,
            self.data_root / f"{DATABASE_NAME}-journal",
            self.data_root / f"{DATABASE_NAME}-wal",
            self.data_root / f"{DATABASE_NAME}-shm",
            self.lock_path,
        ):
            if path.exists():
                _reject_symlink(path)
                path.chmod(PRIVATE_FILE_MODE)

    @staticmethod
    def _begin_exclusive(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("BEGIN EXCLUSIVE")
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).casefold() or "busy" in str(exc).casefold():
                raise ProfileBusyError(
                    "Sense is busy in another process; retry after that operation finishes"
                ) from None
            raise

    @staticmethod
    def _profile_json(profile: ProfileDocument) -> str:
        return canonical_json_bytes(profile).decode("utf-8")

    @staticmethod
    def _migrate_profile(raw_profile: str) -> ProfileDocument:
        try:
            raw = json.loads(raw_profile)
            raw_sections = raw["sections"]
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise UnsafeStorageError("legacy Sense profile is not readable") from exc
        if not isinstance(raw_sections, list):
            raise UnsafeStorageError("legacy Sense profile sections are invalid")

        sections: list[dict[str, Any]] = []
        for item in raw_sections:
            if not isinstance(item, dict):
                raise UnsafeStorageError("legacy Sense profile sections are invalid")
            section_id = LEGACY_SECTION_IDS.get(str(item.get("id", "")), item.get("id"))
            origins = [
                "learned_from_results" if value == "learned_from_work" else value
                for value in item.get("origins", [])
            ]
            sections.append(
                {
                    "id": section_id,
                    "purpose": item.get("purpose"),
                    "text": item.get("text"),
                    "origins": origins,
                    "sensitivity": item.get("sensitivity", "ordinary"),
                }
            )
        try:
            return ProfileDocument(sections=sections)
        except (TypeError, ValueError) as exc:
            raise UnsafeStorageError("legacy Sense profile cannot be migrated") from exc

    def ensure_ready(self) -> None:
        """Create the current schema or migrate the one supported legacy layout once."""

        migrated = False
        with closing(self._connect_write(create=True)) as connection:
            self._begin_exclusive(connection)
            tables = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            if "current_profile" not in tables:
                connection.execute(CURRENT_SCHEMA)
                connection.execute(f"PRAGMA user_version = {DATABASE_SCHEMA_VERSION}")
                connection.execute("COMMIT")
                self._secure_runtime_files()
                return

            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(current_profile)")
            }
            version_row = connection.execute("PRAGMA user_version").fetchone()
            version = int(version_row[0]) if version_row is not None else 0
            if columns == {"singleton", "profile_json", "updated_at"}:
                if version not in {0, DATABASE_SCHEMA_VERSION}:
                    connection.execute("ROLLBACK")
                    raise UnsafeStorageError(
                        "Sense database uses an unsupported schema version"
                    )
                if version == 0:
                    connection.execute(
                        f"PRAGMA user_version = {DATABASE_SCHEMA_VERSION}"
                    )
                connection.execute("COMMIT")
                return

            legacy_columns = {
                "singleton",
                "lifecycle",
                "revision",
                "profile_json",
                "profile_sha256",
                "updated_at",
            }
            if not legacy_columns.issubset(columns):
                connection.execute("ROLLBACK")
                raise UnsafeStorageError("Sense database layout is unsupported")

            row = connection.execute(
                "SELECT lifecycle, profile_json, updated_at FROM current_profile "
                "WHERE singleton = 1"
            ).fetchone()
            if row is not None and row["lifecycle"] != "active":
                connection.execute("ROLLBACK")
                raise UnsafeStorageError(
                    "a legacy Sense preview must be reviewed and imported again locally"
                )
            profile = self._migrate_profile(row["profile_json"]) if row else None
            updated_at = row["updated_at"] if row else utc_now()

            connection.execute("DROP TABLE IF EXISTS current_profile_v2")
            connection.execute(
                "CREATE TABLE current_profile_v2 ("
                "singleton INTEGER PRIMARY KEY CHECK (singleton = 1), "
                "profile_json TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )
            if profile is not None:
                connection.execute(
                    "INSERT INTO current_profile_v2(singleton, profile_json, updated_at) "
                    "VALUES (1, ?, ?)",
                    (self._profile_json(profile), updated_at),
                )
            for table in (
                "profile_revisions",
                "remote_operation_replays",
                "runtime_metadata",
                "current_profile",
            ):
                connection.execute(f"DROP TABLE IF EXISTS {table}")
            connection.execute(
                "ALTER TABLE current_profile_v2 RENAME TO current_profile"
            )
            connection.execute(f"PRAGMA user_version = {DATABASE_SCHEMA_VERSION}")
            connection.execute("COMMIT")
            connection.execute("VACUUM")
            migrated = True
        if migrated:
            self._secure_runtime_files()

    @staticmethod
    def _stored_from_row(row: sqlite3.Row | None) -> StoredProfile:
        if row is None:
            raise ProfileNotFoundError("Sense profile has not been imported")
        try:
            profile = ProfileDocument.model_validate(json.loads(row["profile_json"]))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise UnsafeStorageError("stored Sense profile is invalid") from exc
        return StoredProfile(
            profile=profile,
            digest=content_sha256(profile),
            updated_at=row["updated_at"],
        )

    @classmethod
    def _load_current(cls, connection: sqlite3.Connection) -> StoredProfile:
        row = connection.execute(
            "SELECT profile_json, updated_at FROM current_profile WHERE singleton = 1"
        ).fetchone()
        return cls._stored_from_row(row)

    def initialize(
        self, profile: ProfileDocument, *, replace: bool = False
    ) -> StoredProfile:
        self.ensure_ready()
        with closing(self._connect_write()) as connection:
            self._begin_exclusive(connection)
            existing = connection.execute(
                "SELECT 1 FROM current_profile WHERE singleton = 1"
            ).fetchone()
            if existing is not None and not replace:
                connection.execute("ROLLBACK")
                raise ProfileExistsError("Sense profile already exists")
            payload = self._profile_json(profile)
            now = utc_now()
            if existing is None:
                connection.execute(
                    "INSERT INTO current_profile(singleton, profile_json, updated_at) "
                    "VALUES (1, ?, ?)",
                    (payload, now),
                )
            else:
                connection.execute(
                    "UPDATE current_profile SET profile_json = ?, updated_at = ? "
                    "WHERE singleton = 1",
                    (payload, now),
                )
            connection.execute("COMMIT")
        self._secure_runtime_files()
        return self.read()

    def read(self) -> StoredProfile:
        with closing(self._connect_read()) as connection:
            return self._load_current(connection)

    @staticmethod
    def _find_section(profile: ProfileDocument, section_id: str) -> ProfileSection:
        for section in profile.sections:
            if section.id == section_id:
                return section
        raise SectionNotFoundError(
            "Sense section was not found",
            details={"section_id": section_id},
        )

    @staticmethod
    def _require_sensitive_confirmation(
        previous_section: ProfileSection,
        new_section: ProfileSection,
        *,
        user_confirmed: bool,
    ) -> None:
        if (
            previous_section.sensitivity == "sensitive"
            or new_section.sensitivity == "sensitive"
        ) and not user_confirmed:
            raise ConfirmationRequiredError(
                "changing sensitive Sense guidance requires explicit local confirmation"
            )

    def revise(
        self,
        *,
        changes: list[SectionChange],
        user_confirmed: bool = False,
    ) -> dict[str, Any]:
        if not changes:
            raise ValueError("at least one Sense section change is required")
        if len(changes) > MAX_CHANGES:
            raise ValueError(f"no more than {MAX_CHANGES} Sense changes are allowed")
        section_ids = [change.section_id for change in changes]
        if len(section_ids) != len(set(section_ids)):
            raise ValueError("each Sense section may be changed only once per request")

        with closing(self._connect_write()) as connection:
            self._begin_exclusive(connection)
            current = self._load_current(connection)
            replacements: dict[str, ProfileSection] = {}
            unchanged: list[str] = []
            conflicts: list[str] = []
            for change in changes:
                previous = self._find_section(current.profile, change.section_id)
                if previous == change.new_section:
                    unchanged.append(change.section_id)
                    continue
                if section_sha256(previous) != change.previous_section_sha256:
                    conflicts.append(change.section_id)
                    continue
                self._require_sensitive_confirmation(
                    previous,
                    change.new_section,
                    user_confirmed=user_confirmed,
                )
                replacements[change.section_id] = change.new_section

            if conflicts:
                connection.execute("ROLLBACK")
                raise SectionConflictError(
                    "one or more Sense sections changed after they were read",
                    details={"section_ids": sorted(conflicts)},
                )

            if replacements:
                revised = ProfileDocument(
                    sections=[
                        replacements.get(section.id, section)
                        for section in current.profile.sections
                    ]
                )
                connection.execute(
                    "UPDATE current_profile SET profile_json = ?, updated_at = ? "
                    "WHERE singleton = 1",
                    (self._profile_json(revised), utc_now()),
                )
                effect = "sections_updated"
            else:
                effect = "no_change"
            connection.execute("COMMIT")

        self._secure_runtime_files()
        return {
            "effect": effect,
            "changed_section_ids": sorted(replacements),
            "unchanged_section_ids": sorted(unchanged),
        }

    def remove_section(
        self,
        *,
        section_id: str,
        previous_section_sha256: str,
        user_confirmed: bool,
    ) -> dict[str, Any]:
        if not user_confirmed:
            raise ConfirmationRequiredError(
                "removing Sense guidance requires explicit local confirmation"
            )
        with closing(self._connect_write()) as connection:
            self._begin_exclusive(connection)
            current = self._load_current(connection)
            previous = self._find_section(current.profile, section_id)
            if section_sha256(previous) != previous_section_sha256:
                connection.execute("ROLLBACK")
                raise SectionConflictError(
                    "the Sense section changed after it was read",
                    details={"section_ids": [section_id]},
                )
            remaining = [
                section
                for section in current.profile.sections
                if section.id != section_id
            ]
            if not remaining:
                connection.execute("ROLLBACK")
                raise ValueError("Sense must keep at least one section")
            revised = ProfileDocument(sections=remaining)
            connection.execute(
                "UPDATE current_profile SET profile_json = ?, updated_at = ? "
                "WHERE singleton = 1",
                (self._profile_json(revised), utc_now()),
            )
            connection.execute("COMMIT")
        self._secure_runtime_files()
        return {"removed_section_id": section_id}

    def remove_database(self, *, user_confirmed: bool) -> dict[str, Any]:
        if not user_confirmed:
            raise ConfirmationRequiredError(
                "removing all Sense data requires explicit local confirmation"
            )
        descriptor = self._acquire_lock(exclusive=True, create=False)
        removed: list[str] = []
        try:
            for name in sorted(REMOVABLE_NAMES):
                target = self.data_root / name
                if (
                    target.parent != self.data_root
                    or target.name not in REMOVABLE_NAMES
                ):
                    raise UnsafeStorageError("refusing to remove an unexpected path")
                if target.exists():
                    _reject_symlink(target)
                    target.unlink()
                    removed.append(str(target))
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
        return {
            "removed": removed,
            "retained_runtime_lock": str(self.lock_path),
        }

    def security_status(self) -> dict[str, Any]:
        def mode(path: Path) -> str | None:
            if not path.exists():
                return None
            return f"{stat.S_IMODE(path.stat().st_mode):04o}"

        return {
            "data_root": str(self.data_root),
            "directory_mode": mode(self.data_root),
            "database_mode": mode(self.database_path),
            "lock_mode": mode(self.lock_path),
        }
