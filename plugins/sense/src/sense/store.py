"""Private SQLite storage for Sense."""

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
    ConfirmationMismatchError,
    ConfirmationRequiredError,
    IdempotencyConflictError,
    PreviewReadOnlyError,
    ProfileBusyError,
    ProfileExistsError,
    ProfileNotFoundError,
    RevisionConflictError,
    SectionNotFoundError,
    UnsafeStorageError,
)
from .model import (
    MAX_REVISION_CHANGES,
    Lifecycle,
    ProfileDocument,
    ProfileSection,
    SectionRevision,
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


@dataclass(frozen=True)
class RevisionPreview:
    current: StoredProfile
    proposed_profile: ProfileDocument
    target_section_ids: tuple[str, ...]
    changed_section_ids: tuple[str, ...]
    already_current_section_ids: tuple[str, ...]
    superseded_change_count: int


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
            if not create and not self.database_path.is_file():
                raise ProfileNotFoundError("Sense data has not been created")
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
            raise ProfileNotFoundError("Sense data has not been created")
        _reject_symlink(self.database_path)
        lock_descriptor = self._acquire_lock(exclusive=False, create=False)
        try:
            if not self.database_path.is_file():
                raise ProfileNotFoundError("Sense data has not been created")
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
            raise ProfileNotFoundError("Sense data has not been created")
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
                        "Sense data already exists",
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
            "Sense section was not found",
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
    def _normalize_section_revisions(
        changes: list[SectionRevision],
    ) -> tuple[list[SectionRevision], int]:
        if not changes:
            raise ValueError("at least one section revision is required")
        if len(changes) > MAX_REVISION_CHANGES:
            raise ValueError(
                f"no more than {MAX_REVISION_CHANGES} section revisions are allowed"
            )
        latest_by_section: dict[str, SectionRevision] = {}
        for change in changes:
            latest_by_section[change.section_id] = change
        normalized = [
            latest_by_section[section_id] for section_id in sorted(latest_by_section)
        ]
        return normalized, len(changes) - len(normalized)

    @classmethod
    def _prepare_revision_preview(
        cls,
        current: StoredProfile,
        *,
        expected_revision: int,
        changes: list[SectionRevision],
        superseded_change_count: int,
        user_confirmed: bool,
    ) -> RevisionPreview:
        if expected_revision < 1:
            raise ValueError("expected_revision must be at least 1")
        if expected_revision > current.profile.revision:
            raise RevisionConflictError(
                "Sense profile is older than the requested revision",
                details={
                    "expected_revision": expected_revision,
                    "current_revision": current.profile.revision,
                },
            )

        replacements: dict[str, ProfileSection] = {}
        already_current: list[str] = []
        conflicts: list[str] = []
        for change in changes:
            previous = cls._find_section(current.profile, change.section_id)
            if previous == change.new_section:
                already_current.append(change.section_id)
                continue
            if section_sha256(previous) != change.previous_section_sha256:
                conflicts.append(change.section_id)
                continue
            cls._require_sensitive_confirmation(
                previous,
                change.new_section,
                user_confirmed=user_confirmed,
            )
            replacements[change.section_id] = change.new_section

        if conflicts:
            raise RevisionConflictError(
                "One or more Sense sections changed after they were read",
                details={
                    "expected_revision": expected_revision,
                    "current_revision": current.profile.revision,
                    "section_ids": sorted(conflicts),
                },
            )

        changed_section_ids = tuple(sorted(replacements))
        if changed_section_ids:
            sections = [
                replacements.get(section.id, section)
                for section in current.profile.sections
            ]
            proposed = ProfileDocument.model_validate(
                current.profile.model_copy(
                    update={
                        "revision": current.profile.revision + 1,
                        "sections": sections,
                    }
                ).model_dump(mode="json")
            )
        else:
            proposed = current.profile

        return RevisionPreview(
            current=current,
            proposed_profile=proposed,
            target_section_ids=tuple(change.section_id for change in changes),
            changed_section_ids=changed_section_ids,
            already_current_section_ids=tuple(sorted(already_current)),
            superseded_change_count=superseded_change_count,
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

    def preview_revise_batch(
        self,
        *,
        expected_revision: int,
        changes: list[SectionRevision],
        user_confirmed: bool,
    ) -> RevisionPreview:
        normalized, superseded_change_count = self._normalize_section_revisions(changes)
        with closing(self._connect_read()) as connection:
            current = self._load_current(connection)
            self._require_active(current)
            return self._prepare_revision_preview(
                current,
                expected_revision=expected_revision,
                changes=normalized,
                superseded_change_count=superseded_change_count,
                user_confirmed=user_confirmed,
            )

    def revise_batch(
        self,
        *,
        expected_revision: int,
        idempotency_key: str,
        changes: list[SectionRevision],
        user_confirmed: bool,
    ) -> dict[str, Any]:
        normalized, superseded_change_count = self._normalize_section_revisions(changes)
        request = {
            "expected_revision": expected_revision,
            "changes": [change.model_dump(mode="json") for change in changes],
        }
        with closing(self._connect_write()) as connection:
            self._begin_exclusive(connection)
            replay = self._replay_operation(
                connection,
                operation="revise_batch_v1",
                idempotency_key=idempotency_key,
                request=request,
            )
            if replay is not None:
                connection.execute("COMMIT")
                return replay

            current = self._load_current(connection)
            self._require_active(current)
            preview = self._prepare_revision_preview(
                current,
                expected_revision=expected_revision,
                changes=normalized,
                superseded_change_count=superseded_change_count,
                user_confirmed=user_confirmed,
            )
            if preview.changed_section_ids:
                self._archive_current(connection, current)
                self._replace_current(
                    connection,
                    lifecycle=current.lifecycle,
                    profile=preview.proposed_profile,
                )
                self._prune_history(connection)
                effect = "sections_updated"
            else:
                effect = "no_change"

            result = {
                "revision": preview.proposed_profile.revision,
                "effect": effect,
                "target_section_ids": list(preview.target_section_ids),
                "changed_section_ids": list(preview.changed_section_ids),
                "already_current_section_ids": list(
                    preview.already_current_section_ids
                ),
                "superseded_change_count": preview.superseded_change_count,
                "replayed": False,
            }
            self._record_operation_replay(
                connection,
                operation="revise_batch_v1",
                idempotency_key=idempotency_key,
                request=request,
                response=result,
            )
            connection.execute("COMMIT")
            self._checkpoint(connection)
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
                    "forget replacement cannot broaden related situations; revise the section"
                    )
                if not set(replacement_section.origins).issubset(
                    set(previous_section.origins)
                ):
                    connection.execute("ROLLBACK")
                    raise ValueError(
                        "forget replacement cannot add a new origin; revise the section"
                    )
                if not replacement_refs.issubset(previous_refs):
                    connection.execute("ROLLBACK")
                    raise ValueError(
                        "forget replacement cannot add source references; revise the section"
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
