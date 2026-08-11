"""Private SQLite store for the shared Sense work profile."""

from __future__ import annotations

import base64
import fcntl
import hashlib
import hmac
import json
import os
import sqlite3
import stat
import time
import unicodedata
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .errors import (
    ConfirmationMismatchError,
    ConfirmationRequiredError,
    IdempotencyConflictError,
    InvalidDeleteTicketError,
    MigrationTargetNotEmptyError,
    PreviewReadOnlyError,
    ProfileBusyError,
    ProfileExistsError,
    ProfileNotFoundError,
    RevisionConflictError,
    SectionNotFoundError,
    UnsafeStorageError,
)
from .migration import (
    MIGRATION_BUNDLE_SCHEMA_VERSION,
    MIGRATION_FORMAT,
    validate_idempotency_key,
)
from .model import (
    Lifecycle,
    ProfileDocument,
    ProfileSection,
    canonical_json_bytes,
    content_sha256,
    section_sha256,
)

PRIVATE_DIRECTORY_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
RETAINED_PREVIOUS_REVISIONS = 12
DATA_ROOT_ENV = "SENSE_DATA_DIR"
DATABASE_NAME = "sense.sqlite3"
LOCK_NAME = "runtime.lock"
REMOVABLE_NAMES = {
    DATABASE_NAME,
    f"{DATABASE_NAME}-wal",
    f"{DATABASE_NAME}-shm",
}
LOCK_TIMEOUT_SECONDS = 2.0
SQLITE_BUSY_TIMEOUT_MS = 2000
REMOTE_DELETE_TICKET_TTL = timedelta(minutes=10)

SCHEMA = """
CREATE TABLE IF NOT EXISTS current_profile (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('preview', 'active')),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    profile_json TEXT NOT NULL,
    profile_sha256 TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profile_revisions (
    revision INTEGER PRIMARY KEY,
    profile_json TEXT NOT NULL,
    profile_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runtime_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS remote_operation_replays (
    operation TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_sha256 TEXT NOT NULL,
    response_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (operation, idempotency_key)
);
"""


@dataclass(frozen=True)
class StoredProfile:
    lifecycle: Lifecycle
    profile: ProfileDocument
    digest: str
    updated_at: str


@dataclass(frozen=True)
class StoredRevision:
    profile: ProfileDocument
    digest: str
    created_at: str
    current: bool


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def default_data_root() -> Path:
    configured = os.environ.get(DATA_ROOT_ENV)
    raw = Path(configured) if configured else Path.home() / "Library/Application Support/Sense"
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
            raise UnsafeStorageError("Sense data root must be owned by the current user")
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
            raise UnsafeStorageError(
                "Sense will not take over a non-empty directory"
            )
        _ensure_private_directory(self.data_root)
        _ensure_private_file(self.lock_path)
        if self.database_path.exists():
            _reject_symlink(self.database_path)

    def _acquire_lock(self, *, exclusive: bool, create: bool) -> int:
        if create:
            self._prepare_write_paths(create_database=True)
        else:
            if not self.database_path.is_file():
                raise ProfileNotFoundError("Sense work profile has not been created")
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
            raise ProfileNotFoundError("Sense work profile has not been created")
        lock_descriptor = self._acquire_lock(exclusive=True, create=create)
        try:
            if not create and not self.database_path.is_file():
                raise ProfileNotFoundError("Sense work profile has not been created")
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
            connection.executescript(SCHEMA)
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
            raise ProfileNotFoundError("Sense work profile has not been created")
        _reject_symlink(self.database_path)
        lock_descriptor = self._acquire_lock(exclusive=False, create=False)
        try:
            if not self.database_path.is_file():
                raise ProfileNotFoundError("Sense work profile has not been created")
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
            self.data_root / f"{DATABASE_NAME}-wal",
            self.data_root / f"{DATABASE_NAME}-shm",
            self.lock_path,
        ):
            if path.exists():
                _reject_symlink(path)
                path.chmod(PRIVATE_FILE_MODE)

    @staticmethod
    def _profile_json(profile: ProfileDocument) -> str:
        return canonical_json_bytes(profile).decode("utf-8")

    @staticmethod
    def _stored_from_row(row: sqlite3.Row | None) -> StoredProfile:
        if row is None:
            raise ProfileNotFoundError("Sense work profile has not been created")
        profile = ProfileDocument.model_validate(json.loads(row["profile_json"]))
        if profile.revision != row["revision"]:
            raise UnsafeStorageError("stored Sense revision is inconsistent")
        digest = content_sha256(profile)
        if digest != row["profile_sha256"]:
            raise UnsafeStorageError("stored Sense profile digest is inconsistent")
        return StoredProfile(
            lifecycle=row["lifecycle"],
            profile=profile,
            digest=digest,
            updated_at=row["updated_at"],
        )

    @classmethod
    def _load_current(cls, connection: sqlite3.Connection) -> StoredProfile:
        row = connection.execute(
            """
            SELECT lifecycle, revision, profile_json, profile_sha256, updated_at
            FROM current_profile
            WHERE singleton = 1
            """
        ).fetchone()
        return cls._stored_from_row(row)

    def initialize(
        self,
        profile: ProfileDocument,
        *,
        lifecycle: Lifecycle = "preview",
        replace_preview: bool = False,
        expected_preview_revision: int | None = None,
        expected_preview_digest: str | None = None,
    ) -> StoredProfile:
        if profile.revision != 1:
            raise ValueError("a new Sense profile must begin at revision 1")
        payload = self._profile_json(profile)
        digest = content_sha256(profile)
        now = utc_now()
        replaced_existing = False
        with closing(self._connect_write(create=True)) as connection:
            self._begin_exclusive(connection)
            existing = connection.execute(
                "SELECT lifecycle FROM current_profile WHERE singleton = 1"
            ).fetchone()
            if existing is not None:
                if not replace_preview or existing["lifecycle"] != "preview":
                    connection.execute("ROLLBACK")
                    raise ProfileExistsError(
                        "Sense already has a work profile",
                        details={"lifecycle": existing["lifecycle"]},
                    )
                current = self._load_current(connection)
                if (
                    expected_preview_revision is None
                    or expected_preview_digest is None
                    or current.profile.revision != expected_preview_revision
                    or current.digest != expected_preview_digest
                ):
                    connection.execute("ROLLBACK")
                    raise RevisionConflictError(
                        "Sense preview changed after it was reviewed",
                        details={"current_revision": current.profile.revision},
                    )
                connection.execute("DELETE FROM profile_revisions")
                connection.execute("DELETE FROM current_profile")
                replaced_existing = True
            connection.execute(
                """
                INSERT INTO current_profile (
                    singleton, lifecycle, revision, profile_json, profile_sha256, updated_at
                ) VALUES (1, ?, ?, ?, ?, ?)
                """,
                (lifecycle, profile.revision, payload, digest, now),
            )
            connection.execute("COMMIT")
            if replaced_existing:
                self._purge_deleted_bytes(connection)
            else:
                self._checkpoint(connection)
        self._secure_runtime_files()
        return self.read()

    def read(self) -> StoredProfile:
        with closing(self._connect_read()) as connection:
            return self._load_current(connection)

    @staticmethod
    def _archive_current(
        connection: sqlite3.Connection,
        current: StoredProfile,
    ) -> None:
        connection.execute(
            """
            INSERT OR REPLACE INTO profile_revisions (
                revision, profile_json, profile_sha256, created_at
            ) VALUES (?, ?, ?, ?)
            """,
            (
                current.profile.revision,
                SenseStore._profile_json(current.profile),
                current.digest,
                current.updated_at,
            ),
        )

    @staticmethod
    def _prune_history(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            DELETE FROM profile_revisions
            WHERE revision NOT IN (
                SELECT revision
                FROM profile_revisions
                ORDER BY revision DESC
                LIMIT ?
            )
            """,
            (RETAINED_PREVIOUS_REVISIONS,),
        )

    @staticmethod
    def _replace_current(
        connection: sqlite3.Connection,
        *,
        lifecycle: Lifecycle,
        profile: ProfileDocument,
    ) -> None:
        connection.execute(
            """
            UPDATE current_profile
            SET lifecycle = ?, revision = ?, profile_json = ?,
                profile_sha256 = ?, updated_at = ?
            WHERE singleton = 1
            """,
            (
                lifecycle,
                profile.revision,
                SenseStore._profile_json(profile),
                content_sha256(profile),
                utc_now(),
            ),
        )

    @staticmethod
    def _find_section(profile: ProfileDocument, section_id: str) -> ProfileSection:
        for section in profile.sections:
            if section.id == section_id:
                return section
        raise SectionNotFoundError(
            "Sense profile section was not found",
            details={"section_id": section_id},
        )

    @staticmethod
    def _require_revision(current: StoredProfile, expected_revision: int) -> None:
        if current.profile.revision != expected_revision:
            raise RevisionConflictError(
                "Sense profile changed after it was read",
                details={"current_revision": current.profile.revision},
            )

    @staticmethod
    def _require_active(current: StoredProfile) -> None:
        if current.lifecycle != "active":
            raise PreviewReadOnlyError(
                "Sense preview is read-only until the user activates it",
                details={"lifecycle": current.lifecycle},
            )

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
    def _require_sensitive_confirmation(
        previous_section: ProfileSection,
        new_section: ProfileSection,
        *,
        user_confirmed: bool,
    ) -> None:
        previous_use = set(previous_section.use_for)
        new_use = set(new_section.use_for)
        scope_expanded = not new_use.issubset(previous_use)
        retains_sensitive_content = new_section.sensitivity == "sensitive"
        declassifies_sensitive_content = (
            previous_section.sensitivity == "sensitive"
            and new_section.sensitivity != "sensitive"
        )
        if (
            not scope_expanded
            and not retains_sensitive_content
            and not declassifies_sensitive_content
        ):
            return
        has_user_source = any(
            source.origin == "user_set" for source in new_section.source_refs
        )
        if not user_confirmed or not has_user_source:
            raise ConfirmationRequiredError(
                "Sensitive content, sensitivity declassification, or broader profile "
                "use needs explicit user confirmation and a user-set source reference"
            )

    @staticmethod
    def _replay_operation(
        connection: sqlite3.Connection,
        *,
        operation: str,
        idempotency_key: str,
        request: dict[str, Any],
    ) -> dict[str, Any] | None:
        row = connection.execute(
            "SELECT request_sha256, response_json FROM remote_operation_replays "
            "WHERE operation = ? AND idempotency_key = ?",
            (operation, idempotency_key),
        ).fetchone()
        if row is None:
            return None
        if row["request_sha256"] != content_sha256(request):
            raise IdempotencyConflictError(
                "the idempotency key was already used with different input"
            )
        replay = json.loads(row["response_json"])
        if not isinstance(replay, dict):
            raise UnsafeStorageError("stored Sense replay response is inconsistent")
        replay["replayed"] = True
        return replay

    @staticmethod
    def _record_operation_replay(
        connection: sqlite3.Connection,
        *,
        operation: str,
        idempotency_key: str,
        request: dict[str, Any],
        response: dict[str, Any],
    ) -> None:
        connection.execute(
            "INSERT INTO remote_operation_replays ("
            "operation, idempotency_key, request_sha256, response_json, created_at"
            ") VALUES (?, ?, ?, ?, ?)",
            (
                operation,
                idempotency_key,
                content_sha256(request),
                canonical_json_bytes(response).decode("utf-8"),
                utc_now(),
            ),
        )

    @staticmethod
    def _ticket_secret(connection: sqlite3.Connection) -> bytes:
        row = connection.execute(
            "SELECT value FROM runtime_metadata WHERE key = 'remote_delete_ticket_secret'"
        ).fetchone()
        if row is None:
            value = os.urandom(32).hex()
            connection.execute(
                "INSERT INTO runtime_metadata(key, value) VALUES "
                "('remote_delete_ticket_secret', ?)",
                (value,),
            )
            return bytes.fromhex(value)
        try:
            secret = bytes.fromhex(row["value"])
        except ValueError as exc:
            raise UnsafeStorageError(
                "stored Sense delete-ticket key is inconsistent"
            ) from exc
        if len(secret) != 32:
            raise UnsafeStorageError("stored Sense delete-ticket key is inconsistent")
        return secret

    @classmethod
    def _sign_remote_delete_ticket(
        cls,
        connection: sqlite3.Connection,
        payload: dict[str, Any],
    ) -> str:
        encoded = base64.urlsafe_b64encode(canonical_json_bytes(payload)).decode(
            "ascii"
        ).rstrip("=")
        signature = hmac.new(
            cls._ticket_secret(connection),
            encoded.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()
        return f"{encoded}.{signature}"

    @classmethod
    def _verify_remote_delete_ticket(
        cls,
        connection: sqlite3.Connection,
        ticket: str,
    ) -> dict[str, Any]:
        try:
            encoded, supplied_signature = ticket.split(".", 1)
            expected_signature = hmac.new(
                cls._ticket_secret(connection),
                encoded.encode("ascii"),
                hashlib.sha256,
            ).hexdigest()
            if not hmac.compare_digest(supplied_signature, expected_signature):
                raise InvalidDeleteTicketError(
                    "the Sense delete ticket is invalid or expired"
                )
            decoded = base64.urlsafe_b64decode(
                encoded + "=" * (-len(encoded) % 4)
            )
            payload = json.loads(decoded)
        except InvalidDeleteTicketError:
            raise
        except (UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise InvalidDeleteTicketError(
                "the Sense delete ticket is invalid or expired"
            ) from exc
        if not isinstance(payload, dict):
            raise InvalidDeleteTicketError(
                "the Sense delete ticket is invalid or expired"
            )
        return payload

    def import_migration_profile(
        self,
        *,
        profile: ProfileDocument,
        lifecycle: Lifecycle,
        profile_sha256: str,
        bundle_sha256: str,
        expected_empty: bool,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Initialize an empty namespace from one validated canonical bundle."""

        if expected_empty is not True:
            raise ValueError("Sense migration import requires expected_empty=true")
        if lifecycle != "active":
            raise ValueError("Sense migration accepts only an active profile")
        if content_sha256(profile) != profile_sha256:
            raise ValueError("Sense migration profile digest does not match its payload")
        expected_bundle_sha256 = content_sha256(
            {
                "format": MIGRATION_FORMAT,
                "bundle_schema_version": MIGRATION_BUNDLE_SCHEMA_VERSION,
                "lifecycle": lifecycle,
                "profile": profile.model_dump(mode="json"),
                "profile_sha256": profile_sha256,
            }
        )
        if bundle_sha256 != expected_bundle_sha256:
            raise ValueError("Sense migration bundle digest does not match its payload")
        validate_idempotency_key(idempotency_key)
        request = {
            "expected_empty": True,
            "bundle_sha256": bundle_sha256,
            "profile_sha256": profile_sha256,
        }
        with closing(self._connect_write(create=True)) as connection:
            self._begin_exclusive(connection)
            replay = self._replay_operation(
                connection,
                operation="migration_import_v1",
                idempotency_key=idempotency_key,
                request=request,
            )
            if replay is not None:
                connection.execute("COMMIT")
                return replay

            current_exists = connection.execute(
                "SELECT 1 FROM current_profile WHERE singleton = 1"
            ).fetchone()
            history_exists = connection.execute(
                "SELECT 1 FROM profile_revisions LIMIT 1"
            ).fetchone()
            if current_exists is not None or history_exists is not None:
                raise MigrationTargetNotEmptyError(
                    "Sense migration target is not empty"
                )

            connection.execute(
                "INSERT INTO current_profile ("
                "singleton, lifecycle, revision, profile_json, profile_sha256, updated_at"
                ") VALUES (1, ?, ?, ?, ?, ?)",
                (
                    lifecycle,
                    profile.revision,
                    self._profile_json(profile),
                    profile_sha256,
                    utc_now(),
                ),
            )
            result = {
                "revision": profile.revision,
                "lifecycle": lifecycle,
                "profile_sha256": profile_sha256,
                "bundle_sha256": bundle_sha256,
                "effect": "profile_imported",
                "replayed": False,
            }
            self._record_operation_replay(
                connection,
                operation="migration_import_v1",
                idempotency_key=idempotency_key,
                request=request,
                response=result,
            )
            connection.execute("COMMIT")
            self._checkpoint(connection)
        self._secure_runtime_files()
        return result

    def revise(
        self,
        *,
        expected_revision: int,
        section_id: str,
        previous_section_sha256: str,
        new_section: ProfileSection,
        user_confirmed: bool,
    ) -> StoredProfile:
        if new_section.id != section_id:
            raise ValueError("replacement section id must match section_id")
        with closing(self._connect_write()) as connection:
            self._begin_exclusive(connection)
            current = self._load_current(connection)
            self._require_active(current)
            self._require_revision(current, expected_revision)
            previous = self._find_section(current.profile, section_id)
            if section_sha256(previous) != previous_section_sha256:
                connection.execute("ROLLBACK")
                raise RevisionConflictError(
                    "Sense section changed after it was read",
                    details={
                        "current_revision": current.profile.revision,
                        "section_id": section_id,
                    },
                )
            self._require_sensitive_confirmation(
                previous,
                new_section,
                user_confirmed=user_confirmed,
            )
            self._archive_current(connection, current)
            sections = [
                new_section if section.id == section_id else section
                for section in current.profile.sections
            ]
            revised = current.profile.model_copy(
                update={
                    "revision": current.profile.revision + 1,
                    "sections": sections,
                }
            )
            revised = ProfileDocument.model_validate(revised.model_dump(mode="json"))
            self._replace_current(
                connection,
                lifecycle=current.lifecycle,
                profile=revised,
            )
            self._prune_history(connection)
            connection.execute("COMMIT")
            self._checkpoint(connection)
        self._secure_runtime_files()
        return self.read()

    def remote_revise_public(
        self,
        *,
        expected_revision: int,
        idempotency_key: str,
        principal_binding: str,
        section_id: str,
        previous_understanding: str,
        changed_future_judgment: str,
        public_fields: dict[str, Any],
    ) -> dict[str, Any]:
        """Replace only caller-visible fields of one existing ordinary section."""

        allowed_public_fields = {
            "purpose",
            "text",
            "origins",
            "use_for",
            "review_when",
        }
        if set(public_fields) != allowed_public_fields:
            raise ValueError("remote update must contain exactly the public section fields")
        if not previous_understanding.strip():
            raise ValueError("previous_understanding must explain the replaced view")
        if not changed_future_judgment.strip():
            raise ValueError(
                "changed_future_judgment must state what will differ next time"
            )
        request = {
            "expected_revision": expected_revision,
            "principal_binding": principal_binding,
            "section_id": section_id,
            "previous_understanding": previous_understanding,
            "changed_future_judgment": changed_future_judgment,
            "public_fields": public_fields,
        }
        with closing(self._connect_write()) as connection:
            self._begin_exclusive(connection)
            replay = self._replay_operation(
                connection,
                operation="remote_revise_public_v1",
                idempotency_key=idempotency_key,
                request=request,
            )
            if replay is not None:
                connection.execute("COMMIT")
                return replay

            current = self._load_current(connection)
            self._require_active(current)
            self._require_revision(current, expected_revision)
            previous = self._find_section(current.profile, section_id)
            if previous.sensitivity != "ordinary":
                raise SectionNotFoundError(
                    "Sense profile section was not found",
                    details={"section_id": section_id},
                )
            replacement = ProfileSection.model_validate(
                {
                    "id": previous.id,
                    **public_fields,
                    "sensitivity": previous.sensitivity,
                    "source_refs": [
                        source.model_dump(mode="json")
                        for source in previous.source_refs
                    ],
                }
            )
            self._require_sensitive_confirmation(
                previous,
                replacement,
                user_confirmed=False,
            )
            self._archive_current(connection, current)
            sections = [
                replacement if section.id == section_id else section
                for section in current.profile.sections
            ]
            revised = ProfileDocument.model_validate(
                current.profile.model_copy(
                    update={
                        "revision": current.profile.revision + 1,
                        "sections": sections,
                    }
                ).model_dump(mode="json")
            )
            self._replace_current(
                connection,
                lifecycle=current.lifecycle,
                profile=revised,
            )
            self._prune_history(connection)
            result = {
                "revision": revised.revision,
                "effect": "section_updated",
                "replayed": False,
            }
            self._record_operation_replay(
                connection,
                operation="remote_revise_public_v1",
                idempotency_key=idempotency_key,
                request=request,
                response=result,
            )
            connection.execute("COMMIT")
            self._checkpoint(connection)
        self._secure_runtime_files()
        return result

    def remote_delete_preview(
        self,
        *,
        section_id: str,
        principal_binding: str,
    ) -> dict[str, Any]:
        """Mint a short-lived, exact-state ticket for one ordinary section."""

        with closing(self._connect_write()) as connection:
            self._begin_exclusive(connection)
            current = self._load_current(connection)
            self._require_active(current)
            section = self._find_section(current.profile, section_id)
            if section.sensitivity != "ordinary":
                raise SectionNotFoundError(
                    "Sense profile section was not found",
                    details={"section_id": section_id},
                )
            if len(current.profile.sections) == 1:
                raise ValueError("Sense profile must keep at least one section")
            expires_at = datetime.now(UTC) + REMOTE_DELETE_TICKET_TTL
            payload = {
                "action": "remote_delete_section_v1",
                "principal_binding": principal_binding,
                "expected_revision": current.profile.revision,
                "profile_sha256": current.digest,
                "section_id": section_id,
                "section_sha256": section_sha256(section),
                "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
            }
            ticket = self._sign_remote_delete_ticket(connection, payload)
            connection.execute("COMMIT")
        self._secure_runtime_files()
        return {
            "revision": current.profile.revision,
            "section": section,
            "delete_ticket": ticket,
            "expires_at": payload["expires_at"],
            "effect": "remove_section_from_current_and_retained_revisions",
        }

    def remote_delete(
        self,
        *,
        expected_revision: int,
        idempotency_key: str,
        principal_binding: str,
        delete_ticket: str,
    ) -> dict[str, Any]:
        """Forget one previewed ordinary section and every retained copy of it."""

        request = {
            "expected_revision": expected_revision,
            "principal_binding": principal_binding,
            "delete_ticket": delete_ticket,
        }
        with closing(self._connect_write()) as connection:
            self._begin_exclusive(connection)
            replay = self._replay_operation(
                connection,
                operation="remote_delete_section_v1",
                idempotency_key=idempotency_key,
                request=request,
            )
            if replay is not None:
                connection.execute("COMMIT")
                return replay

            current = self._load_current(connection)
            self._require_active(current)
            self._require_revision(current, expected_revision)
            payload = self._verify_remote_delete_ticket(connection, delete_ticket)
            try:
                expires_at = datetime.fromisoformat(str(payload["expires_at"]))
            except (KeyError, TypeError, ValueError) as exc:
                raise InvalidDeleteTicketError(
                    "the Sense delete ticket is invalid or expired"
                ) from exc
            if expires_at.tzinfo is None or expires_at <= datetime.now(UTC):
                raise InvalidDeleteTicketError(
                    "the Sense delete ticket is invalid or expired"
                )
            if (
                payload.get("action") != "remote_delete_section_v1"
                or payload.get("principal_binding") != principal_binding
                or payload.get("expected_revision") != expected_revision
                or payload.get("profile_sha256") != current.digest
                or not isinstance(payload.get("section_id"), str)
                or not isinstance(payload.get("section_sha256"), str)
            ):
                raise InvalidDeleteTicketError(
                    "the Sense delete ticket does not match this account or profile state"
                )
            section_id = payload["section_id"]
            section = self._find_section(current.profile, section_id)
            if (
                section.sensitivity != "ordinary"
                or section_sha256(section) != payload["section_sha256"]
            ):
                raise InvalidDeleteTicketError(
                    "the previewed Sense section changed; preview the deletion again"
                )
            if len(current.profile.sections) == 1:
                raise ValueError("Sense profile must keep at least one section")

            self._archive_current(connection, current)
            revision_rows = connection.execute(
                "SELECT revision, profile_json FROM profile_revisions"
            ).fetchall()
            for row in revision_rows:
                historical = ProfileDocument.model_validate(
                    json.loads(row["profile_json"])
                )
                historical_sections = [
                    candidate
                    for candidate in historical.sections
                    if candidate.id != section_id
                ]
                if not historical_sections:
                    connection.execute(
                        "DELETE FROM profile_revisions WHERE revision = ?",
                        (row["revision"],),
                    )
                    continue
                scrubbed = ProfileDocument.model_validate(
                    historical.model_copy(
                        update={"sections": historical_sections}
                    ).model_dump(mode="json")
                )
                connection.execute(
                    "UPDATE profile_revisions SET profile_json = ?, profile_sha256 = ? "
                    "WHERE revision = ?",
                    (
                        self._profile_json(scrubbed),
                        content_sha256(scrubbed),
                        row["revision"],
                    ),
                )

            revised = ProfileDocument.model_validate(
                current.profile.model_copy(
                    update={
                        "revision": current.profile.revision + 1,
                        "sections": [
                            candidate
                            for candidate in current.profile.sections
                            if candidate.id != section_id
                        ],
                    }
                ).model_dump(mode="json")
            )
            self._replace_current(
                connection,
                lifecycle=current.lifecycle,
                profile=revised,
            )
            self._prune_history(connection)
            result = {
                "revision": revised.revision,
                "removed_section_count": 1,
                "effect": "section_deleted",
                "replayed": False,
            }
            self._record_operation_replay(
                connection,
                operation="remote_delete_section_v1",
                idempotency_key=idempotency_key,
                request=request,
                response=result,
            )
            connection.execute("COMMIT")
            self._purge_deleted_bytes(connection)
        self._secure_runtime_files()
        return result

    @staticmethod
    def _forget_payload(
        *,
        revision: int,
        profile_digest: str,
        section_id: str,
        replacement_section: ProfileSection | None,
    ) -> dict[str, Any]:
        return {
            "action": "forget",
            "revision": revision,
            "profile_digest": profile_digest,
            "section_id": section_id,
            "replacement_section": (
                replacement_section.model_dump(mode="json")
                if replacement_section is not None
                else None
            ),
        }

    def preview_forget(
        self,
        *,
        section_id: str,
        replacement_section: ProfileSection | None = None,
    ) -> dict[str, Any]:
        current = self.read()
        section = self._find_section(current.profile, section_id)
        if replacement_section is not None and replacement_section.id != section_id:
            raise ValueError("replacement section id must match section_id")
        payload = self._forget_payload(
            revision=current.profile.revision,
            profile_digest=current.digest,
            section_id=section_id,
            replacement_section=replacement_section,
        )
        return {
            "revision": current.profile.revision,
            "before": section.model_dump(mode="json"),
            "after": (
                replacement_section.model_dump(mode="json")
                if replacement_section is not None
                else None
            ),
            "result": "replace" if replacement_section is not None else "remove",
            "confirmation_digest": content_sha256(payload),
        }

    def forget(
        self,
        *,
        expected_revision: int,
        section_id: str,
        confirmation_digest: str,
        replacement_section: ProfileSection | None = None,
        user_confirmed: bool,
    ) -> StoredProfile:
        with closing(self._connect_write()) as connection:
            self._begin_exclusive(connection)
            current = self._load_current(connection)
            self._require_active(current)
            self._require_revision(current, expected_revision)
            previous_section = self._find_section(current.profile, section_id)
            if replacement_section is not None and replacement_section.id != section_id:
                connection.execute("ROLLBACK")
                raise ValueError("replacement section id must match section_id")
            if replacement_section is not None:
                previous_refs = {
                    canonical_json_bytes(source)
                    for source in previous_section.source_refs
                }
                replacement_refs = {
                    canonical_json_bytes(source)
                    for source in replacement_section.source_refs
                }
                if not set(replacement_section.use_for).issubset(
                    set(previous_section.use_for)
                ):
                    connection.execute("ROLLBACK")
                    raise ValueError(
                        "forget replacement cannot broaden related work; use sense_revise"
                    )
                if not set(replacement_section.origins).issubset(
                    set(previous_section.origins)
                ):
                    connection.execute("ROLLBACK")
                    raise ValueError(
                        "forget replacement cannot add a new origin; use sense_revise"
                    )
                if not replacement_refs.issubset(previous_refs):
                    connection.execute("ROLLBACK")
                    raise ValueError(
                        "forget replacement cannot add source references; use sense_revise"
                    )
                try:
                    self._require_sensitive_confirmation(
                        previous_section,
                        replacement_section,
                        user_confirmed=user_confirmed,
                    )
                except ConfirmationRequiredError:
                    connection.execute("ROLLBACK")
                    raise
            elif (
                previous_section.sensitivity == "sensitive"
                and not user_confirmed
            ):
                connection.execute("ROLLBACK")
                raise ConfirmationRequiredError(
                    "forgetting a sensitive section needs explicit user confirmation"
                )
            payload = self._forget_payload(
                revision=current.profile.revision,
                profile_digest=current.digest,
                section_id=section_id,
                replacement_section=replacement_section,
            )
            expected_digest = content_sha256(payload)
            if confirmation_digest != expected_digest:
                connection.execute("ROLLBACK")
                raise ConfirmationMismatchError(
                    "forget confirmation no longer matches the current profile"
                )

            self._archive_current(connection, current)
            revision_rows = connection.execute(
                "SELECT revision, profile_json FROM profile_revisions"
            ).fetchall()
            for row in revision_rows:
                historical = ProfileDocument.model_validate(json.loads(row["profile_json"]))
                historical_sections = [
                    replacement_section if section.id == section_id else section
                    for section in historical.sections
                    if section.id != section_id or replacement_section is not None
                ]
                if not historical_sections:
                    connection.execute(
                        "DELETE FROM profile_revisions WHERE revision = ?",
                        (row["revision"],),
                    )
                    continue
                scrubbed = historical.model_copy(update={"sections": historical_sections})
                scrubbed = ProfileDocument.model_validate(
                    scrubbed.model_dump(mode="json")
                )
                connection.execute(
                    """
                    UPDATE profile_revisions
                    SET profile_json = ?, profile_sha256 = ?
                    WHERE revision = ?
                    """,
                    (
                        self._profile_json(scrubbed),
                        content_sha256(scrubbed),
                        row["revision"],
                    ),
                )

            current_sections = [
                replacement_section if section.id == section_id else section
                for section in current.profile.sections
                if section.id != section_id or replacement_section is not None
            ]
            if not current_sections:
                connection.execute("ROLLBACK")
                raise ValueError("Sense profile must keep at least one section")
            revised = current.profile.model_copy(
                update={
                    "revision": current.profile.revision + 1,
                    "sections": current_sections,
                }
            )
            revised = ProfileDocument.model_validate(revised.model_dump(mode="json"))
            self._replace_current(
                connection,
                lifecycle=current.lifecycle,
                profile=revised,
            )
            self._prune_history(connection)
            connection.execute("COMMIT")
            self._purge_deleted_bytes(connection)
        self._secure_runtime_files()
        return self.read()

    def activate(
        self,
        *,
        expected_revision: int,
        confirm_profile_digest: str,
    ) -> StoredProfile:
        with closing(self._connect_write()) as connection:
            self._begin_exclusive(connection)
            current = self._load_current(connection)
            self._require_revision(current, expected_revision)
            if current.digest != confirm_profile_digest:
                connection.execute("ROLLBACK")
                raise ConfirmationMismatchError(
                    "activation confirmation no longer matches the preview"
                )
            if current.lifecycle == "active":
                connection.execute("COMMIT")
            else:
                connection.execute(
                    """
                    UPDATE current_profile
                    SET lifecycle = 'active', updated_at = ?
                    WHERE singleton = 1
                    """,
                    (utc_now(),),
                )
                connection.execute("COMMIT")
            self._checkpoint(connection)
        self._secure_runtime_files()
        return self.read()

    def history_count(self) -> int:
        with closing(self._connect_read()) as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM profile_revisions"
            ).fetchone()
            return int(row["count"])

    def history(self) -> list[ProfileDocument]:
        return [
            revision.profile
            for revision in self.revision_history()
            if not revision.current
        ]

    def revision_history(self) -> list[StoredRevision]:
        with closing(self._connect_read()) as connection:
            current = self._load_current(connection)
            rows = connection.execute(
                """
                SELECT profile_json, profile_sha256, created_at
                FROM profile_revisions
                ORDER BY revision DESC
                """
            ).fetchall()
        return [
            StoredRevision(
                profile=current.profile,
                digest=current.digest,
                created_at=current.updated_at,
                current=True,
            ),
            *[
                StoredRevision(
                    profile=ProfileDocument.model_validate(
                        json.loads(row["profile_json"])
                    ),
                    digest=row["profile_sha256"],
                    created_at=row["created_at"],
                    current=False,
                )
                for row in rows
            ],
        ]

    def _removal_preview(self, current: StoredProfile) -> dict[str, Any]:
        targets = [str(self.data_root / name) for name in sorted(REMOVABLE_NAMES)]
        payload = {
            "action": "remove_database",
            "revision": current.profile.revision,
            "profile_digest": current.digest,
            "targets": targets,
        }
        return {
            "revision": current.profile.revision,
            "targets": targets,
            "confirmation_digest": content_sha256(payload),
            "does_not_remove": [
                "provider conversations and memory",
                "Git history and backups",
                "previously exported copies",
            ],
        }

    def removal_preview(self) -> dict[str, Any]:
        current = self.read()
        return self._removal_preview(current)

    def remove_database(self, *, confirmation_digest: str) -> dict[str, Any]:
        with closing(self._connect_write()) as connection:
            current = self._load_current(connection)
            preview = self._removal_preview(current)
            if confirmation_digest != preview["confirmation_digest"]:
                raise ConfirmationMismatchError(
                    "database removal confirmation no longer matches the current profile"
                )
            self._checkpoint(connection)
            removed: list[str] = []
            for name in sorted(REMOVABLE_NAMES):
                target = self.data_root / name
                if target.parent != self.data_root or target.name not in REMOVABLE_NAMES:
                    raise UnsafeStorageError("refusing to remove an unexpected path")
                if target.exists():
                    _reject_symlink(target)
                    target.unlink()
                    removed.append(str(target))
        return {
            "removed": removed,
            "retained_runtime_lock": str(self.lock_path),
            "does_not_remove": preview["does_not_remove"],
        }

    @staticmethod
    def _checkpoint(connection: sqlite3.Connection) -> None:
        row = connection.execute("PRAGMA journal_mode").fetchone()
        if row is None or str(row[0]).casefold() != "delete":
            raise ProfileBusyError(
                "Sense could not finish removing retired profile bytes"
            )

    @staticmethod
    def _purge_deleted_bytes(connection: sqlite3.Connection) -> None:
        SenseStore._checkpoint(connection)

    def security_status(self) -> dict[str, Any]:
        def mode(path: Path) -> str | None:
            if not path.exists():
                return None
            return f"{stat.S_IMODE(path.stat().st_mode):04o}"

        return {
            "data_root": str(self.data_root),
            "directory_mode": mode(self.data_root),
            "database_mode": mode(self.database_path),
            "wal_mode": mode(self.data_root / f"{DATABASE_NAME}-wal"),
            "lock_mode": mode(self.lock_path),
        }
