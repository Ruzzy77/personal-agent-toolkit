"""Local-first read/write work folders, including explicitly promoted sources."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import sqlite3
import stat
import unicodedata
import uuid
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .config import (
    EXECUTION_POLICIES,
    WorkspaceRuntimePaths,
    is_within,
    normalize_workspace_id,
)
from .database import (
    encode_json,
    list_corpora,
    utc_now,
    workspace_connection,
    workspace_read_connection,
)
from .errors import (
    ContextNotFoundError,
    PolicyDeniedError,
    SourceBoundaryError,
    WorkspaceBoundaryError,
    WorkspaceConflictError,
    WorkspaceNotFoundError,
    WorkspaceUnavailableError,
    WorkspaceValidationError,
)
from .filesystem_ops import (
    FileMetadataPreservationError,
    FileMetadataSnapshot,
    atomic_exchange_at,
    copy_file_metadata,
    ensure_parent_directory_allows_replacement,
    link_if_absent_at,
    snapshot_file_metadata,
    write_all,
)
from .locking import source_workspace_registry_lock, workspace_writer_lock
from .source_access import (
    opened_source_root,
    resolve_source_root_identity_path,
    source_root_identity,
)

WORKSPACE_DEFAULT_FILE_LIMIT = 100
WORKSPACE_MAX_FILE_LIMIT = 200
WORKSPACE_MAX_FILE_OFFSET = 10_000
WORKSPACE_MAX_PATH_FILTER_CHARS = 1_000
WORKSPACE_MAX_FILE_BYTES = 2 * 1024 * 1024
WORKSPACE_MAX_DISPLAY_NAME_CHARS = 160
WORKSPACE_RECOVERY_RETENTION_DAYS = 14
WORKSPACE_RECOVERY_MAINTENANCE_LIMIT = 100
WORKSPACE_EXPECTED_ABSENT = "absent"
WORKSPACE_VERSION_PREFIX = "v1:"
WORKSPACE_MAX_ENCODED_CONTENT_CHARS = 3 * WORKSPACE_MAX_FILE_BYTES
WORKSPACE_RECOVERY_ID_RE = re.compile(r"^wrec_[0-9a-f]{32}$")
WORKSPACE_INDEX_CHANGE_QUEUE = "index-changes.json"
WORKSPACE_INDEX_CHANGE_QUEUE_VERSION = 1
WORKSPACE_INDEX_CHANGE_MAX_ENTRIES = 2_048
WORKSPACE_INDEX_CHANGE_MAX_BYTES = 1024 * 1024


def _workspace_access() -> Any:
    # Imported lazily so read-only Corpus behavior remains usable when an older
    # optional package projection does not yet contain the workspace module.
    from . import workspace_access

    return workspace_access


def _validate_audience(audience: str) -> None:
    if audience not in {"local_cli", "external_mcp"}:
        raise WorkspaceValidationError(
            "unsupported workspace audience",
            details={"audience": audience},
        )


def _normalize_context_id(value: str) -> str:
    # Keep the launcher bootstrap path independent of document-extraction
    # dependencies. The full context module is needed only when a work folder is
    # actually connected.
    from .contexts import normalize_context_id

    return normalize_context_id(value)


def _normalize_display_name(value: object) -> str:
    if not isinstance(value, str):
        raise WorkspaceValidationError("workspace display name must be a string")
    normalized = unicodedata.normalize("NFC", value.strip())
    if (
        not normalized
        or len(normalized) > WORKSPACE_MAX_DISPLAY_NAME_CHARS
        or any(
            unicodedata.category(character).startswith("C") for character in normalized
        )
        or any(character in {"/", "\\"} for character in normalized)
    ):
        raise WorkspaceValidationError(
            "workspace display name is invalid",
            details={"maximum_chars": WORKSPACE_MAX_DISPLAY_NAME_CHARS},
        )
    return normalized


def _paths_overlap(first: Path, second: Path) -> bool:
    return is_within(first, second) or is_within(second, first)


def _matching_source_corpus_id(
    root: Path,
    *,
    corpora: list[dict[str, Any]],
) -> str | None:
    try:
        root_device, root_inode = _root_identity(root)
    except (WorkspaceBoundaryError, WorkspaceUnavailableError):
        root_device = root_inode = None
    for corpus in corpora:
        source = Path(corpus["source_root"]).expanduser().resolve(strict=False)
        stable_identity_matches = (
            root_device is not None
            and root_inode is not None
            and corpus.get("root_device") is not None
            and corpus.get("root_inode") is not None
            and (root_device, root_inode)
            == (int(corpus["root_device"]), int(corpus["root_inode"]))
        )
        if root == source or stable_identity_matches:
            return str(corpus["corpus_id"])
    return None


def _normalize_root(root: Path) -> Path:
    expanded = root.expanduser()
    if not expanded.is_absolute():
        raise WorkspaceValidationError(
            "workspace root must be an absolute path",
            details={"reason": "not_absolute"},
        )
    try:
        resolved = expanded.resolve(strict=True)
    except OSError as exc:
        raise WorkspaceUnavailableError(
            "workspace root is unavailable",
            details={"reason": f"resolve_failed:{exc.errno}"},
        ) from exc
    if resolved == Path(resolved.anchor):
        raise WorkspaceBoundaryError(
            "filesystem root cannot be a workspace",
            details={"reason": "root_not_allowed"},
        )
    try:
        with opened_source_root(resolved) as descriptor:
            metadata = os.fstat(descriptor)
            if not stat.S_ISDIR(metadata.st_mode):
                raise WorkspaceValidationError("workspace root is not a directory")
            return resolved
    except SourceBoundaryError as exc:
        raise WorkspaceBoundaryError(
            "workspace root could not be opened without following symbolic links",
            details={"reason": exc.details.get("reason", "unsafe_root")},
        ) from exc


def _root_identity(root: Path) -> tuple[int, int]:
    try:
        access = _workspace_access()
        if hasattr(access, "workspace_root_identity"):
            identity = access.workspace_root_identity(root)
            if isinstance(identity, tuple):
                return (int(identity[0]), int(identity[1]))
            return (int(identity.device), int(identity.inode))
    except (AttributeError, TypeError):
        pass
    except Exception as exc:
        if isinstance(exc, (WorkspaceBoundaryError, WorkspaceUnavailableError)):
            raise
        raise WorkspaceUnavailableError(
            "workspace root is unavailable",
            details={"reason": "root_open_failed"},
        ) from exc
    try:
        with opened_source_root(root) as descriptor:
            return source_root_identity(descriptor)
    except SourceBoundaryError as exc:
        raise WorkspaceBoundaryError(
            "workspace root is unsafe",
            details={"reason": exc.details.get("reason", "unsafe_root")},
        ) from exc


def _workspace_row(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    return dict(row)


def _workspace_database_exists(data_root: Path) -> bool:
    return (data_root / "workspaces.sqlite3").exists()


def list_workspace_roots(data_root: Path) -> list[Path]:
    """Return registered roots without creating any private state."""

    if not _workspace_database_exists(data_root):
        return []
    try:
        with workspace_read_connection(data_root) as connection:
            rows = connection.execute(
                "SELECT root_path FROM workspaces ORDER BY workspace_id"
            ).fetchall()
    except WorkspaceNotFoundError:
        return []
    return [Path(row["root_path"]) for row in rows]


class WorkspaceService:
    """Manage one explicitly connected local work folder per Corpus context."""

    def __init__(self, data_root: Path, *, contexts: Any) -> None:
        self.data_root = data_root
        self.contexts = contexts

    def _load_row(
        self,
        workspace_id: str,
        *,
        audience: str,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any]:
        _validate_audience(audience)
        normalized_id = normalize_workspace_id(workspace_id)
        if connection is None:
            if not _workspace_database_exists(self.data_root):
                raise WorkspaceNotFoundError("work folder is not connected")
            with workspace_read_connection(self.data_root) as read_connection:
                row = read_connection.execute(
                    "SELECT * FROM workspaces WHERE workspace_id = ?",
                    (normalized_id,),
                ).fetchone()
        else:
            row = connection.execute(
                "SELECT * FROM workspaces WHERE workspace_id = ?",
                (normalized_id,),
            ).fetchone()
        if row is None:
            raise WorkspaceNotFoundError("work folder is not connected")
        result = _workspace_row(row)
        if (
            audience == "external_mcp"
            and result["execution_policy"] != "external_host_allowed"
        ):
            raise PolicyDeniedError(
                "work folder policy does not permit Chat access; use the local CLI",
                details={
                    "workspace_id": normalized_id,
                    "execution_policy": result["execution_policy"],
                },
            )
        if (
            audience == "external_mcp"
            and self._context_lifecycle_state(result) != "active"
        ):
            raise WorkspaceUnavailableError(
                "work folder context is not active",
                details={"reason": "context_archived"},
            )
        return result

    def _context_lifecycle_state(self, row: dict[str, Any]) -> str:
        try:
            return str(self.contexts.lifecycle_state(row["context_id"]))
        except ContextNotFoundError:
            return "missing"

    def _resolve_workspace_location(self, workspace_id: str) -> dict[str, Any] | None:
        """Recover a moved Finder folder by its stable filesystem identity."""

        workspace_id = normalize_workspace_id(workspace_id)
        if not _workspace_database_exists(self.data_root):
            return None
        with (
            source_workspace_registry_lock(self.data_root),
            workspace_writer_lock(self.data_root),
            workspace_connection(self.data_root) as connection,
        ):
            row = connection.execute(
                "SELECT * FROM workspaces WHERE workspace_id = ?",
                (workspace_id,),
            ).fetchone()
            if row is None:
                return None
            result = _workspace_row(row)
            expected_identity = (
                int(result["root_device"]),
                int(result["root_inode"]),
            )
            try:
                observed_identity = _root_identity(Path(result["root_path"]))
            except (WorkspaceBoundaryError, WorkspaceUnavailableError):
                observed_identity = None
            if observed_identity == expected_identity:
                return result
            if (
                observed_identity is not None
                and observed_identity[1] == expected_identity[1]
            ):
                updated_at = utc_now()
                connection.execute(
                    """
                    UPDATE workspaces
                    SET root_device = ?, root_inode = ?, updated_at = ?
                    WHERE workspace_id = ?
                      AND root_device = ? AND root_inode = ?
                    """,
                    (
                        *observed_identity,
                        updated_at,
                        workspace_id,
                        *expected_identity,
                    ),
                )
                return _workspace_row(
                    connection.execute(
                        "SELECT * FROM workspaces WHERE workspace_id = ?",
                        (workspace_id,),
                    ).fetchone()
                )

            recovered = resolve_source_root_identity_path(*expected_identity)
            if recovered is None:
                return result
            try:
                recovered = _normalize_root(recovered)
            except (
                WorkspaceBoundaryError,
                WorkspaceUnavailableError,
                WorkspaceValidationError,
            ):
                return result
            recovered_nfc = unicodedata.normalize("NFC", str(recovered))
            data_root = self.data_root.expanduser().resolve(strict=False)
            if _paths_overlap(recovered, data_root):
                return result

            context_state = self._context_lifecycle_state(result)
            if context_state not in {"active", "archived"}:
                return result
            context = self.contexts.read(
                context_id=result["context_id"],
                state=context_state,
                limit=1,
                offset=0,
                audience="local_cli",
            )
            try:
                self._require_safe_source_overlap(
                    recovered,
                    corpora=list_corpora(self.data_root),
                    context_corpus_ids=set(context["context"]["corpus_ids"]),
                )
            except WorkspaceBoundaryError:
                return result

            conflict = connection.execute(
                """
                SELECT workspace_id FROM workspaces
                WHERE workspace_id != ?
                  AND (
                      root_path_nfc = ?
                      OR (root_device = ? AND root_inode = ?)
                  )
                LIMIT 1
                """,
                (workspace_id, recovered_nfc, *expected_identity),
            ).fetchone()
            if conflict is not None:
                return result
            for existing in connection.execute(
                """
                SELECT workspace_id, root_path FROM workspaces
                WHERE workspace_id != ?
                """,
                (workspace_id,),
            ).fetchall():
                existing_root = Path(existing["root_path"]).resolve(strict=False)
                if _paths_overlap(recovered, existing_root):
                    return result

            updated_at = utc_now()
            connection.execute(
                """
                UPDATE workspaces
                SET root_path = ?, root_path_nfc = ?, updated_at = ?
                WHERE workspace_id = ?
                  AND root_device = ? AND root_inode = ?
                """,
                (
                    str(recovered),
                    recovered_nfc,
                    updated_at,
                    workspace_id,
                    *expected_identity,
                ),
            )
            return _workspace_row(
                connection.execute(
                    "SELECT * FROM workspaces WHERE workspace_id = ?",
                    (workspace_id,),
                ).fetchone()
            )

    def _resolve_all_workspace_locations(self) -> None:
        if not _workspace_database_exists(self.data_root):
            return
        with workspace_read_connection(self.data_root) as connection:
            workspace_ids = [
                str(row["workspace_id"])
                for row in connection.execute(
                    "SELECT workspace_id FROM workspaces ORDER BY workspace_id"
                ).fetchall()
            ]
        for workspace_id in workspace_ids:
            self._resolve_workspace_location(workspace_id)

    @staticmethod
    def _read_index_change_queue(
        paths: WorkspaceRuntimePaths,
    ) -> dict[str, dict[str, Any]]:
        workspace_root = paths.open_workspace_root()
        root_descriptor = workspace_root.__enter__()
        try:
            try:
                before = os.stat(
                    WORKSPACE_INDEX_CHANGE_QUEUE,
                    dir_fd=root_descriptor,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                return {}
            except OSError as exc:
                raise WorkspaceUnavailableError(
                    "work folder index change queue is unavailable",
                    details={"reason": f"index_change_queue_stat_failed:{exc.errno}"},
                ) from exc
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or stat.S_IMODE(before.st_mode) != 0o600
                or before.st_size > WORKSPACE_INDEX_CHANGE_MAX_BYTES
            ):
                raise WorkspaceBoundaryError(
                    "work folder index change queue is unsafe",
                    details={"reason": "unsafe_index_queue"},
                )
            try:
                descriptor = os.open(
                    WORKSPACE_INDEX_CHANGE_QUEUE,
                    os.O_RDONLY
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=root_descriptor,
                )
            except OSError as exc:
                raise WorkspaceUnavailableError(
                    "work folder index change queue could not be opened",
                    details={"reason": f"index_change_queue_open_failed:{exc.errno}"},
                ) from exc
            try:
                opened = os.fstat(descriptor)
                if (
                    opened.st_dev != before.st_dev
                    or opened.st_ino != before.st_ino
                    or opened.st_size != before.st_size
                ):
                    raise WorkspaceConflictError(
                        "work folder index change queue changed while it was opened",
                        details={"reason": "index_change_queue_changed"},
                    )
                chunks: list[bytes] = []
                observed = 0
                while observed <= WORKSPACE_INDEX_CHANGE_MAX_BYTES:
                    try:
                        chunk = os.read(
                            descriptor,
                            min(
                                64 * 1024,
                                WORKSPACE_INDEX_CHANGE_MAX_BYTES + 1 - observed,
                            ),
                        )
                    except InterruptedError:
                        continue
                    if not chunk:
                        break
                    chunks.append(chunk)
                    observed += len(chunk)
                if observed > WORKSPACE_INDEX_CHANGE_MAX_BYTES:
                    raise WorkspaceBoundaryError(
                        "work folder index change queue is too large",
                        details={"reason": "index_change_queue_too_large"},
                    )
                after = os.fstat(descriptor)
                if (
                    after.st_dev != opened.st_dev
                    or after.st_ino != opened.st_ino
                    or after.st_size != opened.st_size
                    or after.st_mtime_ns != opened.st_mtime_ns
                    or after.st_ctime_ns != opened.st_ctime_ns
                ):
                    raise WorkspaceConflictError(
                        "work folder index change queue changed while it was read",
                        details={"reason": "index_change_queue_changed"},
                    )
            finally:
                os.close(descriptor)
        finally:
            workspace_root.__exit__(None, None, None)

        try:
            value = json.loads(b"".join(chunks))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WorkspaceBoundaryError(
                "work folder index change queue is invalid",
                details={"reason": "invalid_index_queue"},
            ) from exc
        if (
            not isinstance(value, dict)
            or value.get("version") != WORKSPACE_INDEX_CHANGE_QUEUE_VERSION
            or not isinstance(value.get("entries"), dict)
            or len(value["entries"]) > WORKSPACE_INDEX_CHANGE_MAX_ENTRIES
        ):
            raise WorkspaceBoundaryError(
                "work folder index change queue is invalid",
                details={"reason": "invalid_index_queue"},
            )
        access = _workspace_access()
        entries: dict[str, dict[str, Any]] = {}
        for relative_path, entry in value["entries"].items():
            if (
                not isinstance(relative_path, str)
                or access.normalize_workspace_relative_path(relative_path)
                != relative_path
                or not isinstance(entry, dict)
                or entry.get("state") not in {"prepared", "dirty"}
                or not isinstance(entry.get("source_corpus_id"), str)
            ):
                raise WorkspaceBoundaryError(
                    "work folder index change queue is invalid",
                    details={"reason": "invalid_index_change_queue_entry"},
                )
            entries[relative_path] = dict(entry)
        return entries

    @staticmethod
    def _write_index_change_queue(
        paths: WorkspaceRuntimePaths,
        entries: dict[str, dict[str, Any]],
    ) -> None:
        value = {
            "version": WORKSPACE_INDEX_CHANGE_QUEUE_VERSION,
            "entries": entries,
        }
        payload = encode_json(value).encode()
        if (
            len(entries) > WORKSPACE_INDEX_CHANGE_MAX_ENTRIES
            or len(payload) > WORKSPACE_INDEX_CHANGE_MAX_BYTES
        ):
            raise WorkspaceUnavailableError(
                "work folder index change queue is full",
                details={"reason": "index_change_queue_full"},
            )
        temporary_name = f".{WORKSPACE_INDEX_CHANGE_QUEUE}.{uuid.uuid4().hex}.tmp"
        with paths.open_workspace_root() as root_descriptor:
            descriptor: int | None = None
            try:
                descriptor = os.open(
                    temporary_name,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=root_descriptor,
                )
                os.fchmod(descriptor, 0o600)
                write_all(descriptor, payload)
                os.fsync(descriptor)
                os.close(descriptor)
                descriptor = None
                os.rename(
                    temporary_name,
                    WORKSPACE_INDEX_CHANGE_QUEUE,
                    src_dir_fd=root_descriptor,
                    dst_dir_fd=root_descriptor,
                )
                os.fsync(root_descriptor)
            except OSError as exc:
                raise WorkspaceUnavailableError(
                    "work folder index change queue could not be updated",
                    details={"reason": f"index_change_queue_write_failed:{exc.errno}"},
                ) from exc
            finally:
                if descriptor is not None:
                    os.close(descriptor)
                with suppress(FileNotFoundError):
                    os.unlink(temporary_name, dir_fd=root_descriptor)

    def _source_corpus_id_for_row(self, row: dict[str, Any]) -> str | None:
        return _matching_source_corpus_id(
            Path(row["root_path"]).expanduser().resolve(strict=False),
            corpora=list_corpora(self.data_root),
        )

    def _prepare_index_change(
        self,
        *,
        row: dict[str, Any],
        paths: WorkspaceRuntimePaths,
        source_corpus_id: str,
        relative_path: str,
        operation: str,
        expected_version: str,
        intended_sha256: str,
    ) -> None:
        entries = self._read_index_change_queue(paths)
        existing = entries.get(relative_path)
        if existing is not None and existing.get("state") == "dirty":
            return
        if existing is None and len(entries) >= WORKSPACE_INDEX_CHANGE_MAX_ENTRIES:
            raise WorkspaceUnavailableError(
                "work folder index change queue is full",
                details={"reason": "index_change_queue_full"},
            )
        entries[relative_path] = {
            "source_corpus_id": source_corpus_id,
            "workspace_id": row["workspace_id"],
            "state": "prepared",
            "operation": operation,
            "expected_version": expected_version,
            "intended_sha256": intended_sha256,
            "updated_at": utc_now(),
        }
        self._write_index_change_queue(paths, entries)

    def _mark_index_change_dirty(
        self,
        *,
        paths: WorkspaceRuntimePaths,
        source_corpus_id: str,
        relative_path: str,
        result_version: str,
    ) -> None:
        entries = self._read_index_change_queue(paths)
        entry = dict(entries.get(relative_path, {}))
        entry.update(
            {
                "source_corpus_id": source_corpus_id,
                "state": "dirty",
                "result_version": result_version,
                "updated_at": utc_now(),
            }
        )
        entries[relative_path] = entry
        self._write_index_change_queue(paths, entries)

    def _clear_prepared_index_change(
        self,
        *,
        paths: WorkspaceRuntimePaths,
        relative_path: str,
    ) -> None:
        entries = self._read_index_change_queue(paths)
        entry = entries.get(relative_path)
        if entry is None or entry.get("state") != "prepared":
            return
        del entries[relative_path]
        self._write_index_change_queue(paths, entries)

    def _promoted_source_guard(self, corpus_id: str) -> dict[str, Any] | None:
        corpus = next(
            (
                item
                for item in list_corpora(self.data_root)
                if item["corpus_id"] == corpus_id
            ),
            None,
        )
        if corpus is None or not _workspace_database_exists(self.data_root):
            return None
        source_root = Path(corpus["source_root"]).expanduser().resolve(strict=False)
        with workspace_read_connection(self.data_root) as connection:
            rows = connection.execute(
                "SELECT * FROM workspaces ORDER BY workspace_id"
            ).fetchall()
        for candidate in rows:
            row = _workspace_row(candidate)
            root = Path(row["root_path"]).expanduser().resolve(strict=False)
            if root != source_root:
                continue
            paths = WorkspaceRuntimePaths(self.data_root, row["workspace_id"])
            return {
                "workspace_id": row["workspace_id"],
                "root": root,
                "identity": self._identity(row),
                "changes": self._read_index_change_queue(paths),
            }
        return None

    def promoted_source_guard(self, corpus_id: str) -> dict[str, Any] | None:
        """Return private live-observation state for an exact promoted source."""

        self._resolve_all_workspace_locations()
        return self._promoted_source_guard(corpus_id)

    def clear_index_changes(
        self,
        *,
        corpus_id: str,
        relative_paths: set[str],
    ) -> int:
        """Clear paths only after the caller proved their projections current."""

        if not relative_paths:
            return 0
        self._resolve_all_workspace_locations()
        with workspace_writer_lock(self.data_root):
            guard = self._promoted_source_guard(corpus_id)
            if guard is None:
                return 0
            paths = WorkspaceRuntimePaths(self.data_root, guard["workspace_id"])
            entries = self._read_index_change_queue(paths)
            removed = 0
            for relative_path in relative_paths:
                if entries.pop(relative_path, None) is not None:
                    removed += 1
            if removed:
                self._write_index_change_queue(paths, entries)
            return removed

    def _connected_state(
        self,
        row: dict[str, Any],
    ) -> tuple[str, str | None, Any | None]:
        context_state = self._context_lifecycle_state(row)
        if context_state != "active":
            return (
                "suspended",
                "context_archived"
                if context_state == "archived"
                else "context_missing",
                None,
            )
        try:
            identity = self._identity(row)
        except (WorkspaceBoundaryError, WorkspaceUnavailableError) as exc:
            return ("unavailable", str(exc.details.get("reason", "unavailable")), None)
        return ("connected", None, identity)

    def _project(self, row: dict[str, Any], *, audience: str) -> dict[str, Any]:
        state, reason, identity = self._connected_state(row)
        display_name = row["display_name"]
        if audience == "external_mcp" and any(
            character in {"/", "\\"} for character in display_name
        ):
            # Defend the external projection even if an older or manually edited
            # registry row predates the display-name validation above.
            display_name = row["workspace_id"]
        result = {
            "workspace_id": row["workspace_id"],
            "context_id": row["context_id"],
            "context_state": self._context_lifecycle_state(row),
            "display_name": display_name,
            "execution_policy": row["execution_policy"],
            "current_relative_path": row["current_relative_path"],
            "generation": row["generation"],
            "connection_state": state,
            "connection_reason": reason,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        current_relative_path = row["current_relative_path"]
        if current_relative_path is None:
            result["current_file"] = None
        elif state != "connected":
            result["current_file"] = {
                "relative_path": current_relative_path,
                "state": "unavailable",
                "reason": reason or "workspace_unavailable",
            }
        else:
            access = _workspace_access()
            try:
                current = access.inspect_workspace_file(
                    Path(row["root_path"]),
                    identity,
                    current_relative_path,
                )
                if current.state == "ready":
                    result["current_file"] = {
                        "relative_path": current.relative_path,
                        "state": "ready",
                        "reason": None,
                        "residency_state": "resident",
                        "size": current.size,
                        "modified_ns": current.modified_ns,
                    }
                elif current.state == "missing":
                    result["current_file"] = {
                        "relative_path": current.relative_path,
                        "state": "missing",
                        "reason": "missing",
                    }
                elif current.state == "remote_only":
                    result["current_file"] = {
                        "relative_path": current.relative_path,
                        "state": "unavailable",
                        "reason": "remote_only",
                        "residency_state": "remote_only",
                        "size": current.size,
                        "modified_ns": current.modified_ns,
                    }
                else:
                    result["current_file"] = {
                        "relative_path": current.relative_path,
                        "state": "excluded",
                        "reason": current.state,
                    }
            except WorkspaceBoundaryError as exc:
                result["current_file"] = {
                    "relative_path": current_relative_path,
                    "state": "excluded",
                    "reason": str(exc.details.get("reason", "excluded")),
                }
            except WorkspaceUnavailableError as exc:
                current_reason = str(exc.details.get("reason", "unavailable"))
                result["current_file"] = {
                    "relative_path": current_relative_path,
                    "state": "missing"
                    if current_reason == "missing"
                    else "unavailable",
                    "reason": current_reason,
                }
        source_corpus_id = _matching_source_corpus_id(
            Path(row["root_path"]).expanduser().resolve(strict=False),
            corpora=list_corpora(self.data_root),
        )
        if source_corpus_id is not None and audience == "local_cli":
            result["source_corpus_id"] = source_corpus_id
        if audience == "local_cli":
            result["root_path"] = row["root_path"]
        return result

    @staticmethod
    def _require_safe_source_overlap(
        root: Path,
        *,
        corpora: list[dict[str, Any]],
        context_corpus_ids: set[str],
    ) -> str | None:
        promoted_source_corpus_id: str | None = None
        try:
            root_identity = _root_identity(root)
        except (WorkspaceBoundaryError, WorkspaceUnavailableError):
            root_identity = None
        for corpus in corpora:
            source = Path(corpus["source_root"]).expanduser().resolve(strict=False)
            stable_identity_matches = (
                root_identity is not None
                and corpus.get("root_device") is not None
                and corpus.get("root_inode") is not None
                and root_identity
                == (int(corpus["root_device"]), int(corpus["root_inode"]))
            )
            if root == source or stable_identity_matches:
                corpus_id = str(corpus["corpus_id"])
                if corpus_id not in context_corpus_ids:
                    raise WorkspaceBoundaryError(
                        "a registered source can become editable only in its linked context",
                        details={
                            "reason": "source_context_mismatch",
                            "corpus_id": corpus_id,
                        },
                    )
                promoted_source_corpus_id = corpus_id
                continue
            if _paths_overlap(root, source):
                raise WorkspaceBoundaryError(
                    "work folder and a registered source must not partially overlap",
                    details={
                        "reason": "source_root_overlap",
                        "corpus_id": corpus["corpus_id"],
                    },
                )
        return promoted_source_corpus_id

    def connect(
        self,
        *,
        workspace_id: str | None,
        context_id: str | None,
        display_name: str | None,
        root: Path,
        execution_policy: str,
    ) -> dict[str, Any]:
        root = _normalize_root(root)
        default_identifier = root.name
        workspace_id = normalize_workspace_id(workspace_id or default_identifier)
        context_id = _normalize_context_id(context_id or default_identifier)
        display_name = _normalize_display_name(display_name or default_identifier)
        if execution_policy not in EXECUTION_POLICIES:
            raise WorkspaceValidationError(
                "unsupported workspace execution policy",
                details={
                    "execution_policy": execution_policy,
                    "allowed": sorted(EXECUTION_POLICIES),
                },
            )
        context = self.contexts.read(
            context_id=context_id,
            state="active",
            limit=1,
            offset=0,
            audience="local_cli",
        )
        context_corpus_ids = set(context["context"]["corpus_ids"])
        data_root = self.data_root.expanduser().resolve(strict=False)
        if _paths_overlap(root, data_root):
            raise WorkspaceBoundaryError(
                "work folder and Corpus private data must not overlap",
                details={"reason": "data_root_overlap"},
            )
        self._require_safe_source_overlap(
            root,
            corpora=list_corpora(self.data_root),
            context_corpus_ids=context_corpus_ids,
        )
        identity = _root_identity(root)
        now = utc_now()
        paths = WorkspaceRuntimePaths(self.data_root, workspace_id)
        with (
            source_workspace_registry_lock(self.data_root),
            workspace_writer_lock(self.data_root),
            workspace_connection(self.data_root) as connection,
        ):
            context = self.contexts.read(
                context_id=context_id,
                state="active",
                limit=1,
                offset=0,
                audience="local_cli",
            )
            context_corpus_ids = set(context["context"]["corpus_ids"])
            self._require_safe_source_overlap(
                root,
                corpora=list_corpora(self.data_root),
                context_corpus_ids=context_corpus_ids,
            )
            conflict = connection.execute(
                """
                SELECT workspace_id, context_id, root_path
                FROM workspaces
                WHERE workspace_id = ? OR context_id = ? OR root_path_nfc = ?
                LIMIT 1
                """,
                (
                    workspace_id,
                    context_id,
                    unicodedata.normalize("NFC", str(root)),
                ),
            ).fetchone()
            if conflict is not None:
                raise WorkspaceConflictError(
                    "work folder id, context, or root is already connected",
                    details={
                        "workspace_id": conflict["workspace_id"],
                        "context_id": conflict["context_id"],
                        "reason": "registration_conflict",
                    },
                )
            for existing in connection.execute(
                "SELECT workspace_id, root_path FROM workspaces"
            ).fetchall():
                existing_root = Path(existing["root_path"]).resolve(strict=False)
                if _paths_overlap(root, existing_root):
                    raise WorkspaceBoundaryError(
                        "connected work folders must not overlap",
                        details={
                            "reason": "workspace_overlap",
                            "workspace_id": existing["workspace_id"],
                        },
                    )
            connection.execute(
                """
                INSERT INTO workspaces(
                    workspace_id, context_id, display_name, root_path,
                    root_path_nfc, root_device, root_inode, execution_policy,
                    current_relative_path, generation, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, 1, ?, ?)
                """,
                (
                    workspace_id,
                    context_id,
                    display_name,
                    str(root),
                    unicodedata.normalize("NFC", str(root)),
                    identity[0],
                    identity[1],
                    execution_policy,
                    now,
                    now,
                ),
            )
            paths.ensure()
            row = self._load_row(
                workspace_id,
                audience="local_cli",
                connection=connection,
            )
        return {"work_folder": self._project(row, audience="local_cli")}

    def rebind_root(
        self,
        *,
        workspace_id: str,
        root: Path,
        expected_root: Path,
    ) -> dict[str, Any]:
        """Explicitly replace a copied or restored Work root.

        Finder moves are resolved automatically by filesystem identity. This
        operation is deliberately explicit because a copied directory has a
        new identity and cannot safely be inferred from a similar name alone.
        """

        workspace_id = normalize_workspace_id(workspace_id)
        root = _normalize_root(root)
        if not expected_root.expanduser().is_absolute():
            raise WorkspaceValidationError(
                "expected workspace root must be an absolute path",
                details={"reason": "expected_root_not_absolute"},
            )
        expected_root_nfc = unicodedata.normalize(
            "NFC", str(Path(os.path.abspath(expected_root.expanduser())))
        )
        root_nfc = unicodedata.normalize("NFC", str(root))
        data_root = self.data_root.expanduser().resolve(strict=False)
        if _paths_overlap(root, data_root):
            raise WorkspaceBoundaryError(
                "work folder and Corpus private data must not overlap",
                details={"reason": "data_root_overlap"},
            )
        identity = _root_identity(root)
        with (
            source_workspace_registry_lock(self.data_root),
            workspace_writer_lock(self.data_root),
            workspace_connection(self.data_root) as connection,
        ):
            row = connection.execute(
                "SELECT * FROM workspaces WHERE workspace_id = ?",
                (workspace_id,),
            ).fetchone()
            if row is None:
                raise WorkspaceNotFoundError("work folder is not connected")
            existing = _workspace_row(row)
            existing_root_nfc = unicodedata.normalize("NFC", str(existing["root_path"]))
            if existing_root_nfc != expected_root_nfc:
                raise WorkspaceConflictError(
                    "connected work folder does not match the expected root",
                    details={
                        "workspace_id": workspace_id,
                        "reason": "expected_root_mismatch",
                    },
                )
            context_state = self._context_lifecycle_state(existing)
            if context_state != "active":
                raise WorkspaceUnavailableError(
                    "work folder context is not active",
                    details={"reason": "context_archived"},
                )
            context = self.contexts.read(
                context_id=existing["context_id"],
                state="active",
                limit=1,
                offset=0,
                audience="local_cli",
            )
            self._require_safe_source_overlap(
                root,
                corpora=list_corpora(self.data_root),
                context_corpus_ids=set(context["context"]["corpus_ids"]),
            )
            conflict = connection.execute(
                """
                SELECT workspace_id FROM workspaces
                WHERE workspace_id != ?
                  AND (
                      root_path_nfc = ?
                      OR (root_device = ? AND root_inode = ?)
                  )
                LIMIT 1
                """,
                (workspace_id, root_nfc, *identity),
            ).fetchone()
            if conflict is not None:
                raise WorkspaceConflictError(
                    "replacement work folder is already connected",
                    details={
                        "workspace_id": workspace_id,
                        "conflicting_workspace_id": conflict["workspace_id"],
                        "reason": "registration_conflict",
                    },
                )
            for other in connection.execute(
                """
                SELECT workspace_id, root_path FROM workspaces
                WHERE workspace_id != ?
                """,
                (workspace_id,),
            ).fetchall():
                if _paths_overlap(root, Path(other["root_path"]).resolve(strict=False)):
                    raise WorkspaceBoundaryError(
                        "connected work folders must not overlap",
                        details={
                            "reason": "workspace_overlap",
                            "workspace_id": other["workspace_id"],
                        },
                    )
            updated_at = utc_now()
            connection.execute(
                """
                UPDATE workspaces
                SET root_path = ?, root_path_nfc = ?, root_device = ?,
                    root_inode = ?, updated_at = ?
                WHERE workspace_id = ? AND root_path_nfc = ?
                """,
                (
                    str(root),
                    root_nfc,
                    identity[0],
                    identity[1],
                    updated_at,
                    workspace_id,
                    existing_root_nfc,
                ),
            )
            refreshed = self._load_row(
                workspace_id,
                audience="local_cli",
                connection=connection,
            )
        return {
            "changed": existing_root_nfc != root_nfc,
            "previous_root": existing["root_path"],
            "work_folder": self._project(refreshed, audience="local_cli"),
        }

    def disconnect(
        self,
        *,
        workspace_id: str,
    ) -> dict[str, Any]:
        workspace_id = normalize_workspace_id(workspace_id)
        self._resolve_workspace_location(workspace_id)
        with workspace_writer_lock(self.data_root):
            with workspace_connection(self.data_root) as connection:
                row = self._load_row(
                    workspace_id,
                    audience="local_cli",
                    connection=connection,
                )
                source_corpus_id = self._source_corpus_id_for_row(row)
                if source_corpus_id is not None:
                    pending_changes = self._read_index_change_queue(
                        WorkspaceRuntimePaths(self.data_root, row["workspace_id"])
                    )
                    if pending_changes:
                        raise WorkspaceConflictError(
                            "work folder has source-index changes pending refresh",
                            details={
                                "reason": "index_refresh_pending",
                                "pending_change_count": len(pending_changes),
                            },
                        )
                recovery_rows = connection.execute(
                    """
                    SELECT recovery_relative_path
                    FROM workspace_recoveries
                    WHERE workspace_id = ? AND recovery_relative_path IS NOT NULL
                    """,
                    (workspace_id,),
                ).fetchall()
                recovery_names = {
                    recovery["recovery_relative_path"] for recovery in recovery_rows
                }
                for recovery_name in recovery_names:
                    if not isinstance(recovery_name, str) or not re.fullmatch(
                        r"wrec_[0-9a-f]{32}\.bin", recovery_name
                    ):
                        raise WorkspaceBoundaryError(
                            "work folder recovery record is unsafe",
                            details={"reason": "invalid_recovery_path"},
                        )
                connection.execute(
                    "DELETE FROM workspaces WHERE workspace_id = ?",
                    (workspace_id,),
                )

            recovery_cleanup_complete = True
            if recovery_names:
                paths = WorkspaceRuntimePaths(self.data_root, workspace_id)
                try:
                    with paths.open_workspace_directory(
                        "recovery"
                    ) as parent_descriptor:
                        for recovery_name in recovery_names:
                            with suppress(FileNotFoundError):
                                os.unlink(recovery_name, dir_fd=parent_descriptor)
                        os.fsync(parent_descriptor)
                except OSError:
                    recovery_cleanup_complete = False
        return {
            "workspace_id": workspace_id,
            "disconnected": True,
            "local_files_changed": False,
            "recovery_cleanup_complete": recovery_cleanup_complete,
        }

    def list(self, *, audience: str = "local_cli") -> dict[str, Any]:
        _validate_audience(audience)
        if not _workspace_database_exists(self.data_root):
            return {"work_folders": [], "returned_count": 0}
        self._resolve_all_workspace_locations()
        with workspace_read_connection(self.data_root) as connection:
            rows = connection.execute(
                "SELECT * FROM workspaces ORDER BY workspace_id"
            ).fetchall()
        visible = [
            _workspace_row(row)
            for row in rows
            if audience == "local_cli"
            or (
                row["execution_policy"] == "external_host_allowed"
                and self._context_lifecycle_state(_workspace_row(row)) == "active"
            )
        ]
        work_folders = [self._project(row, audience=audience) for row in visible]
        return {
            "work_folders": work_folders,
            "returned_count": len(work_folders),
        }

    def status(
        self,
        *,
        workspace_id: str,
        audience: str = "local_cli",
    ) -> dict[str, Any]:
        self._resolve_workspace_location(workspace_id)
        row = self._load_row(workspace_id, audience=audience)
        return {"work_folder": self._project(row, audience=audience)}

    def roots(self, *, resolve_locations: bool = True) -> list[Path]:
        if resolve_locations:
            self._resolve_all_workspace_locations()
        return list_workspace_roots(self.data_root)

    @staticmethod
    def _identity(row: dict[str, Any]) -> Any:
        access = _workspace_access()
        observed = _root_identity(Path(row["root_path"]))
        if observed[1] != int(row["root_inode"]):
            raise WorkspaceUnavailableError(
                "work folder root changed after it was connected",
                details={"reason": "root_identity_changed"},
            )
        return access.WorkspaceRootIdentity(
            device=observed[0],
            inode=observed[1],
        )

    def _require_connected(self, row: dict[str, Any]) -> Any:
        state, reason, identity = self._connected_state(row)
        if state != "connected":
            raise WorkspaceUnavailableError(
                "work folder is not connected",
                details={"reason": reason or "unavailable"},
            )
        return identity

    @staticmethod
    def _observation_dict(observation: Any) -> dict[str, Any]:
        return {
            "relative_path": observation.relative_path,
            "size": observation.size,
            "modified_ns": observation.modified_ns,
            "version_token": observation.version_token,
        }

    @staticmethod
    def _same_file_after_exchange(before: Any, after: Any) -> bool:
        """Compare everything an atomic rename does not itself change.

        Some filesystems update ctime when an inode is renamed or exchanged, so
        comparing the opaque token after the exchange would reject every valid
        save.  Device, inode, bytes, mtime, mode, and link count still detect a
        target replacement or content write between the precheck and exchange.
        """

        return (
            before.device == after.device
            and before.inode == after.inode
            and before.size == after.size
            and before.modified_ns == after.modified_ns
            and before.mode == after.mode
            and before.link_count == after.link_count
            and before.sha256 == after.sha256
        )

    @staticmethod
    def _sync_directory(parent_descriptor: int, *, action: str) -> None:
        """Make a completed namespace action durable, where supported."""

        try:
            os.fsync(parent_descriptor)
        except OSError as exc:
            raise WorkspaceUnavailableError(
                "work folder directory could not be synchronized",
                details={"reason": f"{action}_sync_failed:{exc.errno}"},
            ) from exc

    def files(
        self,
        *,
        workspace_id: str,
        relative_path: str | None = None,
        path_contains: str | None = None,
        limit: int = WORKSPACE_DEFAULT_FILE_LIMIT,
        offset: int = 0,
        audience: str = "local_cli",
        recursive: bool = True,
    ) -> dict[str, Any]:
        if not isinstance(recursive, bool):
            raise WorkspaceValidationError("recursive must be a boolean")
        if isinstance(limit, bool) or not 1 <= limit <= WORKSPACE_MAX_FILE_LIMIT:
            raise WorkspaceValidationError(
                "work folder file limit is outside the supported range",
                details={"maximum": WORKSPACE_MAX_FILE_LIMIT},
            )
        if isinstance(offset, bool) or offset < 0 or offset > WORKSPACE_MAX_FILE_OFFSET:
            raise WorkspaceValidationError(
                "work folder file offset is outside the supported range",
                details={"maximum": WORKSPACE_MAX_FILE_OFFSET},
            )
        normalized_filter: str | None = None
        if path_contains is not None:
            if not isinstance(path_contains, str):
                raise WorkspaceValidationError("path_contains must be a string")
            normalized_filter = unicodedata.normalize("NFC", path_contains.strip())
            if (
                not normalized_filter
                or len(normalized_filter) > WORKSPACE_MAX_PATH_FILTER_CHARS
            ):
                raise WorkspaceValidationError(
                    "path_contains is outside the supported range",
                    details={"maximum_chars": WORKSPACE_MAX_PATH_FILTER_CHARS},
                )
        self._resolve_workspace_location(workspace_id)
        row = self._load_row(workspace_id, audience=audience)
        identity = self._require_connected(row)
        access = _workspace_access()
        scan_limit = WORKSPACE_MAX_FILE_OFFSET + WORKSPACE_MAX_FILE_LIMIT
        listing = access.list_workspace(
            Path(row["root_path"]),
            identity,
            relative_path=relative_path,
            max_entries=scan_limit,
            max_depth=32 if recursive else 0,
        )
        entries = [
            {
                "relative_path": entry.relative_path,
                "kind": entry.kind,
                "size": entry.size,
                "modified_ns": entry.modified_ns,
                "residency_state": entry.residency_state,
            }
            for entry in listing.entries
            if normalized_filter is None
            or normalized_filter.casefold() in entry.relative_path.casefold()
        ]
        total_matching = len(entries)
        page = entries[offset : offset + limit]
        next_offset = offset + len(page)
        scanned_all_matching_entries = not listing.truncated
        has_next_known_page = next_offset < total_matching
        return {
            "work_folder": self._project(row, audience=audience),
            "relative_path": relative_path,
            "path_contains": normalized_filter,
            "offset": offset,
            "limit": limit,
            "returned_count": len(page),
            "total_matching": total_matching if scanned_all_matching_entries else None,
            "listing_truncated": listing.truncated,
            "has_more": has_next_known_page,
            "next_offset": next_offset if has_next_known_page else None,
            "entries": page,
            "skipped": {
                "symlinks": listing.skipped_symlinks,
                "special": listing.skipped_special,
                "excluded": listing.skipped_excluded,
            },
        }

    def read(
        self,
        *,
        workspace_id: str,
        relative_path: str | None = None,
        encoding: str = "utf8",
        max_bytes: int = WORKSPACE_MAX_FILE_BYTES,
        audience: str = "local_cli",
    ) -> dict[str, Any]:
        if (
            isinstance(max_bytes, bool)
            or not 1 <= max_bytes <= WORKSPACE_MAX_FILE_BYTES
        ):
            raise WorkspaceValidationError(
                "work folder read size is outside the supported range",
                details={"maximum_bytes": WORKSPACE_MAX_FILE_BYTES},
            )
        if encoding not in {"utf8", "base64"}:
            raise WorkspaceValidationError(
                "unsupported work folder encoding",
                details={"allowed": ["base64", "utf8"]},
            )
        self._resolve_workspace_location(workspace_id)
        row = self._load_row(workspace_id, audience=audience)
        identity = self._require_connected(row)
        selected = relative_path or row["current_relative_path"]
        if not selected:
            raise WorkspaceValidationError(
                "no work folder file was selected",
                details={"choose_relative_path": True},
            )
        access = _workspace_access()
        file_read = access.read_workspace_file(
            Path(row["root_path"]),
            identity,
            selected,
            max_bytes=max_bytes,
        )
        if encoding == "base64":
            content = base64.b64encode(file_read.data).decode("ascii")
        else:
            try:
                content = file_read.data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise WorkspaceValidationError(
                    "work folder file is not valid UTF-8; retry with base64",
                    details={
                        "relative_path": file_read.observation.relative_path,
                        "retry_encoding": "base64",
                    },
                ) from exc
        return {
            "work_folder": self._project(row, audience=audience),
            "file": self._observation_dict(file_read.observation),
            "encoding": encoding,
            "content": content,
            "content_sha256": file_read.observation.sha256,
            "content_is_untrusted": True,
        }

    def select_current(
        self,
        *,
        workspace_id: str,
        relative_path: str,
        audience: str = "local_cli",
    ) -> dict[str, Any]:
        access = _workspace_access()
        normalized_path = access.normalize_workspace_relative_path(relative_path)
        self._resolve_workspace_location(workspace_id)
        with (
            workspace_writer_lock(self.data_root),
            workspace_connection(self.data_root) as connection,
        ):
            row = self._load_row(
                workspace_id,
                audience=audience,
                connection=connection,
            )
            identity = self._require_connected(row)
            observation = access.observe_workspace_file(
                Path(row["root_path"]),
                identity,
                normalized_path,
                max_bytes=WORKSPACE_MAX_FILE_BYTES,
            )
            new_generation = int(row["generation"]) + 1
            selected_at = utc_now()
            updated = connection.execute(
                """
                UPDATE workspaces
                SET current_relative_path = ?, generation = ?, updated_at = ?
                WHERE workspace_id = ? AND generation = ?
                """,
                (
                    observation.relative_path,
                    new_generation,
                    selected_at,
                    row["workspace_id"],
                    row["generation"],
                ),
            ).rowcount
            if updated != 1:
                raise WorkspaceConflictError(
                    "work folder changed during current file selection",
                    details={"reason": "generation_changed"},
                )
            row["current_relative_path"] = observation.relative_path
            row["generation"] = new_generation
            row["updated_at"] = selected_at
        return {
            "work_folder": self._project(row, audience=audience),
            "file": self._observation_dict(observation),
        }

    @staticmethod
    def _decode_content(content: str, *, content_encoding: str) -> bytes:
        if not isinstance(content, str):
            raise WorkspaceValidationError(
                "work folder content must be a string carrier"
            )
        if content_encoding == "utf8":
            payload = content.encode("utf-8")
        elif content_encoding == "base64":
            try:
                payload = base64.b64decode(content, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise WorkspaceValidationError(
                    "work folder base64 content is invalid",
                    details={"content_encoding": "base64"},
                ) from exc
        else:
            raise WorkspaceValidationError(
                "unsupported work folder content encoding",
                details={"allowed": ["base64", "utf8"]},
            )
        if len(payload) > WORKSPACE_MAX_FILE_BYTES:
            raise WorkspaceValidationError(
                "work folder content exceeds the write limit",
                details={
                    "content_bytes": len(payload),
                    "maximum_bytes": WORKSPACE_MAX_FILE_BYTES,
                },
            )
        return payload

    @staticmethod
    def _validate_expected_version(expected_version: str) -> None:
        if expected_version == WORKSPACE_EXPECTED_ABSENT:
            return
        if (
            not isinstance(expected_version, str)
            or not expected_version.startswith(WORKSPACE_VERSION_PREFIX)
            or len(expected_version) > 1_000
        ):
            raise WorkspaceValidationError(
                "expected_version must be 'absent' or an observed v1 token"
            )

    @staticmethod
    def _write_temporary(
        parent_descriptor: int,
        *,
        payload: bytes,
        mode: int,
    ) -> str:
        name = f".corpus-write-{uuid.uuid4().hex}.tmp"
        descriptor: int | None = None
        try:
            descriptor = os.open(
                name,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                mode & 0o777,
                dir_fd=parent_descriptor,
            )
            os.fchmod(descriptor, mode & 0o777)
            write_all(descriptor, payload)
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            return name
        except OSError as exc:
            if descriptor is not None:
                with suppress(OSError):
                    os.close(descriptor)
                descriptor = None
            with suppress(OSError):
                os.unlink(name, dir_fd=parent_descriptor)
                os.fsync(parent_descriptor)
            raise WorkspaceUnavailableError(
                "work folder temporary file could not be written",
                details={"reason": f"temporary_write_failed:{exc.errno}"},
            ) from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)

    @staticmethod
    def _preserve_existing_metadata(
        parent_descriptor: int,
        *,
        existing_name: str,
        temporary_name: str,
        before: Any,
        relative_path: str,
    ) -> FileMetadataSnapshot:
        """Copy complete existing-file metadata to a private temporary inode."""

        source_descriptor: int | None = None
        temporary_descriptor: int | None = None
        try:
            source_descriptor = os.open(
                existing_name,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent_descriptor,
            )
            source = os.fstat(source_descriptor)
            if source.st_dev != before.device or source.st_ino != before.inode:
                raise WorkspaceConflictError(
                    "work folder file changed before metadata could be preserved",
                    details={
                        "relative_path": relative_path,
                        "reason": "metadata_source_changed",
                    },
                )
            temporary_descriptor = os.open(
                temporary_name,
                os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent_descriptor,
            )
            return copy_file_metadata(
                source_descriptor,
                temporary_descriptor,
                parent_descriptor=parent_descriptor,
            )
        except FileMetadataPreservationError as exc:
            if exc.reason == "metadata_changed":
                raise WorkspaceConflictError(
                    "work folder metadata changed during save",
                    details={
                        "relative_path": relative_path,
                        "reason": "concurrent_metadata_change",
                    },
                ) from exc
            if exc.reason == "metadata_copy_failed":
                raise WorkspaceUnavailableError(
                    "work folder metadata could not be copied",
                    details={
                        "relative_path": relative_path,
                        "reason": f"metadata_copy_failed:{exc.errno}",
                    },
                ) from exc
            raise WorkspaceBoundaryError(
                "work folder metadata cannot be preserved for atomic replacement",
                details={
                    "relative_path": relative_path,
                    "reason": "metadata_blocks_replacement",
                    "metadata_reason": exc.reason,
                },
            ) from exc
        except OSError as exc:
            raise WorkspaceUnavailableError(
                "work folder metadata could not be prepared",
                details={
                    "relative_path": relative_path,
                    "reason": f"metadata_prepare_failed:{exc.errno}",
                },
            ) from exc
        finally:
            if temporary_descriptor is not None:
                os.close(temporary_descriptor)
            if source_descriptor is not None:
                os.close(source_descriptor)

    @staticmethod
    def _require_metadata_safe_parent(
        parent_descriptor: int,
        *,
        relative_path: str,
    ) -> None:
        """Reject directory metadata that could strand a replacement temporary."""

        try:
            ensure_parent_directory_allows_replacement(parent_descriptor)
        except FileMetadataPreservationError as exc:
            raise WorkspaceBoundaryError(
                "work folder directory metadata blocks atomic replacement",
                details={
                    "relative_path": relative_path,
                    "reason": "metadata_blocks_replacement",
                    "metadata_reason": exc.reason,
                },
            ) from exc
        except OSError as exc:
            raise WorkspaceUnavailableError(
                "work folder directory metadata could not be checked",
                details={
                    "relative_path": relative_path,
                    "reason": f"metadata_preflight_failed:{exc.errno}",
                },
            ) from exc

    @staticmethod
    def _metadata_matches_at(
        parent_descriptor: int,
        *,
        name: str,
        expected: FileMetadataSnapshot,
    ) -> bool:
        descriptor = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_descriptor,
        )
        try:
            return snapshot_file_metadata(descriptor) == expected
        finally:
            os.close(descriptor)

    @staticmethod
    def _remove_temporary(parent_descriptor: int, name: str | None) -> None:
        if name is None:
            return
        with suppress(OSError):
            os.unlink(name, dir_fd=parent_descriptor)
            os.fsync(parent_descriptor)

    @staticmethod
    def _private_recovery_name(recovery_id: str) -> str:
        return f"{recovery_id}.bin"

    @staticmethod
    def _copy_descriptor_to_recovery(
        descriptor: int,
        *,
        paths: WorkspaceRuntimePaths,
        recovery_name: str,
        mode: int,
    ) -> None:
        with paths.open_workspace_directory("recovery") as recovery_descriptor:
            output: int | None = None
            try:
                output = os.open(
                    recovery_name,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=recovery_descriptor,
                )
                os.fchmod(output, 0o600)
                os.lseek(descriptor, 0, os.SEEK_SET)
                while True:
                    chunk = os.read(descriptor, 1024 * 1024)
                    if not chunk:
                        break
                    write_all(output, chunk)
                os.fsync(output)
                os.close(output)
                output = None
                os.fsync(recovery_descriptor)
            except OSError as exc:
                if output is not None:
                    os.close(output)
                with suppress(OSError):
                    os.unlink(recovery_name, dir_fd=recovery_descriptor)
                raise WorkspaceUnavailableError(
                    "work folder recovery copy could not be stored",
                    details={"reason": f"recovery_write_failed:{exc.errno}"},
                ) from exc

    @staticmethod
    def _discard_private_recovery(
        paths: WorkspaceRuntimePaths,
        recovery_name: str | None,
    ) -> None:
        if recovery_name is None:
            return
        with (
            paths.open_workspace_directory("recovery") as recovery_descriptor,
            suppress(OSError),
        ):
            os.unlink(recovery_name, dir_fd=recovery_descriptor)
            os.fsync(recovery_descriptor)

    def _discard_recovery_record(
        self,
        *,
        workspace_id: str,
        recovery_id: str,
        paths: WorkspaceRuntimePaths,
        recovery_name: str | None,
    ) -> None:
        self._discard_private_recovery(paths, recovery_name)
        with workspace_connection(self.data_root) as connection:
            connection.execute(
                """
                UPDATE workspace_recoveries
                SET state = 'discarded', recovery_relative_path = NULL,
                    updated_at = ?
                WHERE recovery_id = ? AND workspace_id = ?
                  AND state = 'prepared'
                """,
                (utc_now(), recovery_id, workspace_id),
            )

    @staticmethod
    def _recovery_is_expired(expires_at: object, *, now: datetime) -> bool:
        if not isinstance(expires_at, str) or not expires_at:
            return False
        try:
            observed = datetime.fromisoformat(expires_at)
        except ValueError:
            return False
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=UTC)
        return observed <= now

    def _discard_recovery_state(
        self,
        *,
        row: dict[str, Any],
        recovery: dict[str, Any],
        paths: WorkspaceRuntimePaths,
        reason: str,
    ) -> bool:
        recovery_name = recovery.get("recovery_relative_path")
        try:
            metadata = json.loads(recovery.get("metadata_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        metadata["discard_reason"] = reason
        metadata["discarded_at"] = utc_now()
        with workspace_connection(self.data_root) as connection:
            updated = connection.execute(
                """
                UPDATE workspace_recoveries
                SET state = 'discarded', recovery_relative_path = NULL,
                    metadata_json = ?, updated_at = ?
                WHERE recovery_id = ? AND workspace_id = ?
                  AND state IN ('prepared', 'available')
                """,
                (
                    encode_json(metadata),
                    metadata["discarded_at"],
                    recovery["recovery_id"],
                    row["workspace_id"],
                ),
            ).rowcount
        if updated != 1:
            return False
        if isinstance(recovery_name, str) and re.fullmatch(
            r"wrec_[0-9a-f]{32}\.bin",
            recovery_name,
        ):
            self._discard_private_recovery(paths, recovery_name)
        return True

    def _maintain_recoveries_locked(
        self,
        *,
        row: dict[str, Any],
        paths: WorkspaceRuntimePaths,
        limit: int = WORKSPACE_RECOVERY_MAINTENANCE_LIMIT,
    ) -> dict[str, int]:
        """Bound recovery reconciliation and private orphan cleanup."""

        now = datetime.now(UTC)
        with workspace_read_connection(self.data_root) as connection:
            recoveries = [
                dict(item)
                for item in connection.execute(
                    """
                    SELECT * FROM workspace_recoveries
                    WHERE workspace_id = ?
                      AND state IN ('prepared', 'available')
                    ORDER BY created_at, recovery_id
                    LIMIT ?
                    """,
                    (row["workspace_id"], limit),
                ).fetchall()
            ]
        summary = {
            "reconciled": 0,
            "discarded": 0,
            "compacted": 0,
            "orphan_files_removed": 0,
        }
        with workspace_read_connection(self.data_root) as connection:
            older_available = [
                dict(item)
                for item in connection.execute(
                    """
                    SELECT * FROM (
                        SELECT recoveries.*,
                               ROW_NUMBER() OVER (
                                   PARTITION BY relative_path
                                   ORDER BY created_at DESC, recovery_id DESC
                               ) AS recovery_rank
                        FROM workspace_recoveries recoveries
                        WHERE workspace_id = ? AND state = 'available'
                    )
                    WHERE recovery_rank > 1
                    ORDER BY created_at, recovery_id
                    LIMIT ?
                    """,
                    (row["workspace_id"], limit),
                ).fetchall()
            ]
        for recovery in older_available:
            if self._discard_recovery_state(
                row=row,
                recovery=recovery,
                paths=paths,
                reason="newer_recovery_available",
            ):
                summary["discarded"] += 1
                summary["compacted"] += 1

        access = _workspace_access()
        for recovery in recoveries:
            if self._recovery_is_expired(recovery.get("expires_at"), now=now):
                if self._discard_recovery_state(
                    row=row,
                    recovery=recovery,
                    paths=paths,
                    reason="expired",
                ):
                    summary["discarded"] += 1
                continue
            if recovery["state"] != "prepared" or recovery["operation"] != "replace":
                continue
            try:
                canonical = access.normalize_workspace_relative_path(
                    recovery["relative_path"]
                )
                current = access.observe_workspace_file(
                    Path(row["root_path"]),
                    self._identity(row),
                    canonical,
                    max_bytes=WORKSPACE_MAX_FILE_BYTES,
                )
            # Stale or malformed recovery entries must not stop reconciliation.
            except Exception:  # noqa: BLE001, S112
                continue
            if current.version_token == recovery["base_version_token"]:
                if self._discard_recovery_state(
                    row=row,
                    recovery=recovery,
                    paths=paths,
                    reason="prepared_without_mutation",
                ):
                    summary["discarded"] += 1
                continue
            try:
                metadata = json.loads(recovery["metadata_json"])
            except (TypeError, json.JSONDecodeError):
                metadata = {}
            intended_sha256 = (
                metadata.get("intended_sha256") if isinstance(metadata, dict) else None
            )
            if intended_sha256 != current.sha256:
                continue
            recovery_name = recovery.get("recovery_relative_path")
            if not isinstance(recovery_name, str):
                if self._discard_recovery_state(
                    row=row,
                    recovery=recovery,
                    paths=paths,
                    reason="missing_recovery_path",
                ):
                    summary["discarded"] += 1
                continue
            try:
                self._read_private_recovery(
                    paths=paths,
                    recovery_name=recovery_name,
                    relative_path=canonical,
                )
            # Any unreadable private copy is handled as a missing recovery copy.
            except Exception:  # noqa: BLE001
                if self._discard_recovery_state(
                    row=row,
                    recovery=recovery,
                    paths=paths,
                    reason="missing_recovery_copy",
                ):
                    summary["discarded"] += 1
                continue
            if not isinstance(metadata, dict):
                metadata = {}
            metadata["reconciled_from_prepared"] = True
            metadata["reconciled_at"] = utc_now()
            with workspace_connection(self.data_root) as connection:
                updated = connection.execute(
                    """
                    UPDATE workspace_recoveries
                    SET state = 'available', result_version_token = ?,
                        metadata_json = ?, updated_at = ?
                    WHERE recovery_id = ? AND workspace_id = ?
                      AND state = 'prepared'
                    """,
                    (
                        current.version_token,
                        encode_json(metadata),
                        metadata["reconciled_at"],
                        recovery["recovery_id"],
                        row["workspace_id"],
                    ),
                ).rowcount
            summary["reconciled"] += int(updated == 1)

        with workspace_read_connection(self.data_root) as connection:
            referenced = {
                item["recovery_relative_path"]
                for item in connection.execute(
                    """
                    SELECT recovery_relative_path FROM workspace_recoveries
                    WHERE workspace_id = ? AND recovery_relative_path IS NOT NULL
                    """,
                    (row["workspace_id"],),
                ).fetchall()
            }
        with paths.open_workspace_directory("recovery") as recovery_descriptor:
            removed = 0
            for name in sorted(os.listdir(recovery_descriptor)):
                if removed >= limit:
                    break
                if (
                    name in referenced
                    or re.fullmatch(r"wrec_[0-9a-f]{32}\.bin", name) is None
                ):
                    continue
                try:
                    metadata = os.stat(
                        name,
                        dir_fd=recovery_descriptor,
                        follow_symlinks=False,
                    )
                    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                        continue
                    os.unlink(name, dir_fd=recovery_descriptor)
                    removed += 1
                except OSError:
                    continue
            if removed:
                with suppress(OSError):
                    os.fsync(recovery_descriptor)
            summary["orphan_files_removed"] = removed
        return summary

    def maintain_recoveries(self, *, workspace_id: str) -> dict[str, int]:
        """Run bounded local maintenance without exposing a remote tool."""

        self._resolve_workspace_location(workspace_id)
        with workspace_writer_lock(self.data_root):
            row = self._load_row(workspace_id, audience="local_cli")
            paths = WorkspaceRuntimePaths(self.data_root, row["workspace_id"])
            paths.ensure()
            return self._maintain_recoveries_locked(row=row, paths=paths)

    def write(
        self,
        *,
        workspace_id: str,
        relative_path: str,
        content: str,
        content_encoding: str,
        expected_version: str,
        make_current: bool = True,
        audience: str = "local_cli",
    ) -> dict[str, Any]:
        """Create or replace one file without silently overwriting outside edits."""

        self._validate_expected_version(expected_version)
        payload = self._decode_content(content, content_encoding=content_encoding)
        access = _workspace_access()
        canonical = access.normalize_workspace_relative_path(relative_path)
        recovery_id = f"wrec_{uuid.uuid4().hex}"
        now = utc_now()
        expires_at = (
            datetime.now(UTC) + timedelta(days=WORKSPACE_RECOVERY_RETENTION_DAYS)
        ).isoformat()
        operation = (
            "create" if expected_version == WORKSPACE_EXPECTED_ABSENT else "replace"
        )

        self._resolve_workspace_location(workspace_id)
        with workspace_writer_lock(self.data_root):
            row = self._load_row(workspace_id, audience=audience)
            identity = self._require_connected(row)
            paths = WorkspaceRuntimePaths(self.data_root, row["workspace_id"])
            paths.ensure()
            with suppress(Exception):
                self._maintain_recoveries_locked(row=row, paths=paths)
            root = Path(row["root_path"])
            recovery_name = (
                None
                if operation == "create"
                else self._private_recovery_name(recovery_id)
            )
            result_observation: Any | None = None
            source_corpus_id = self._source_corpus_id_for_row(row)
            intended_sha256 = hashlib.sha256(payload).hexdigest()

            if source_corpus_id is not None:
                self._prepare_index_change(
                    row=row,
                    paths=paths,
                    source_corpus_id=source_corpus_id,
                    relative_path=canonical,
                    operation=operation,
                    expected_version=expected_version,
                    intended_sha256=intended_sha256,
                )

            if operation == "replace":
                # Persist intent before changing the user file. A crash may
                # leave a prepared record, but never an unrecorded replacement.
                with workspace_connection(self.data_root) as connection:
                    connection.execute(
                        """
                        INSERT INTO workspace_recoveries(
                            recovery_id, workspace_id, operation, relative_path,
                            recovery_relative_path, base_version_token,
                            result_version_token, metadata_json, state,
                            created_at, updated_at, expires_at
                        ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, 'prepared', ?, ?, ?)
                        """,
                        (
                            recovery_id,
                            row["workspace_id"],
                            operation,
                            canonical,
                            recovery_name,
                            expected_version,
                            encode_json(
                                {
                                    "intended_sha256": intended_sha256,
                                    "content_encoding": content_encoding,
                                }
                            ),
                            now,
                            now,
                            expires_at,
                        ),
                    )

            mutation_completed = False
            try:
                with (
                    access.opened_workspace_root(root, identity) as root_descriptor,
                    access.opened_workspace_parent(
                        root_descriptor,
                        canonical,
                        create_parents=True,
                    ) as (parent_descriptor, canonical_name, existing_raw_name),
                ):
                    if expected_version == WORKSPACE_EXPECTED_ABSENT:
                        if existing_raw_name is not None:
                            raise WorkspaceConflictError(
                                "work folder file already exists",
                                details={
                                    "relative_path": canonical,
                                    "reason": "expected_absent",
                                },
                            )
                        temporary_name = self._write_temporary(
                            parent_descriptor,
                            payload=payload,
                            mode=0o600,
                        )
                        try:
                            try:
                                link_if_absent_at(
                                    parent_descriptor,
                                    temporary_name,
                                    canonical_name,
                                )
                            except FileExistsError as exc:
                                raise WorkspaceConflictError(
                                    "work folder file appeared before create",
                                    details={
                                        "relative_path": canonical,
                                        "reason": "create_conflict",
                                    },
                                ) from exc
                            try:
                                self._sync_directory(parent_descriptor, action="create")
                                os.unlink(temporary_name, dir_fd=parent_descriptor)
                                self._sync_directory(
                                    parent_descriptor, action="create_cleanup"
                                )
                            except WorkspaceUnavailableError as exc:
                                raise WorkspaceUnavailableError(
                                    "work folder create durability could not be confirmed",
                                    details={
                                        "relative_path": canonical,
                                        "reason": exc.details.get(
                                            "reason", "directory_sync_failed"
                                        ),
                                        "file_created": True,
                                    },
                                ) from exc
                            temporary_name = None
                            result_observation = access.observe_workspace_file(
                                root,
                                identity,
                                canonical,
                                max_bytes=WORKSPACE_MAX_FILE_BYTES,
                            )
                        finally:
                            self._remove_temporary(parent_descriptor, temporary_name)
                    else:
                        if existing_raw_name is None:
                            raise WorkspaceConflictError(
                                "work folder file no longer exists",
                                details={
                                    "relative_path": canonical,
                                    "reason": "missing_before_replace",
                                },
                            )
                        before = access.observe_workspace_file(
                            root,
                            identity,
                            canonical,
                            max_bytes=WORKSPACE_MAX_FILE_BYTES,
                        )
                        if before.version_token != expected_version:
                            raise WorkspaceConflictError(
                                "work folder file changed before save",
                                details={
                                    "relative_path": canonical,
                                    "reason": "stale_version",
                                    "current_version": before.version_token,
                                },
                            )
                        if before.hardlinked:
                            raise WorkspaceBoundaryError(
                                "hard-linked work folder files cannot be replaced",
                                details={
                                    "relative_path": canonical,
                                    "reason": "unexpected_link_count",
                                },
                            )
                        self._require_metadata_safe_parent(
                            parent_descriptor,
                            relative_path=canonical,
                        )
                        temporary_name = self._write_temporary(
                            parent_descriptor,
                            payload=payload,
                            mode=0o600,
                        )
                        preserved_metadata: FileMetadataSnapshot | None = None
                        old_descriptor: int | None = None
                        exchanged = False
                        cleanup_temporary = True
                        try:
                            preserved_metadata = self._preserve_existing_metadata(
                                parent_descriptor,
                                existing_name=existing_raw_name,
                                temporary_name=temporary_name,
                                before=before,
                                relative_path=canonical,
                            )
                            try:
                                atomic_exchange_at(
                                    parent_descriptor,
                                    temporary_name,
                                    existing_raw_name,
                                )
                            except OSError as exc:
                                raise WorkspaceUnavailableError(
                                    "work folder does not support an atomic replacement",
                                    details={
                                        "relative_path": canonical,
                                        "reason": f"atomic_exchange_failed:{exc.errno}",
                                    },
                                ) from exc
                            exchanged = True
                            self._sync_directory(
                                parent_descriptor, action="replacement"
                            )
                            old_descriptor = os.open(
                                temporary_name,
                                os.O_RDONLY
                                | getattr(os, "O_CLOEXEC", 0)
                                | getattr(os, "O_NOFOLLOW", 0),
                                dir_fd=parent_descriptor,
                            )
                            observed_old = (
                                access.workspace_file_observation_from_descriptor(
                                    old_descriptor,
                                    relative_path=canonical,
                                    max_bytes=WORKSPACE_MAX_FILE_BYTES,
                                )
                            )
                            if not self._same_file_after_exchange(before, observed_old):
                                atomic_exchange_at(
                                    parent_descriptor,
                                    temporary_name,
                                    existing_raw_name,
                                )
                                exchanged = False
                                self._sync_directory(
                                    parent_descriptor, action="replacement_rollback"
                                )
                                raise WorkspaceConflictError(
                                    "work folder file changed during save",
                                    details={
                                        "relative_path": canonical,
                                        "reason": "concurrent_replace",
                                    },
                                )
                            try:
                                metadata_unchanged = (
                                    snapshot_file_metadata(old_descriptor)
                                    == preserved_metadata
                                )
                                replacement_metadata_matches = (
                                    self._metadata_matches_at(
                                        parent_descriptor,
                                        name=existing_raw_name,
                                        expected=preserved_metadata,
                                    )
                                )
                            except OSError:
                                metadata_unchanged = False
                                replacement_metadata_matches = False
                            if not metadata_unchanged:
                                atomic_exchange_at(
                                    parent_descriptor,
                                    temporary_name,
                                    existing_raw_name,
                                )
                                exchanged = False
                                self._sync_directory(
                                    parent_descriptor, action="replacement_rollback"
                                )
                                raise WorkspaceConflictError(
                                    "work folder metadata changed during save",
                                    details={
                                        "relative_path": canonical,
                                        "reason": "concurrent_metadata_change",
                                    },
                                )
                            if not replacement_metadata_matches:
                                atomic_exchange_at(
                                    parent_descriptor,
                                    temporary_name,
                                    existing_raw_name,
                                )
                                exchanged = False
                                self._sync_directory(
                                    parent_descriptor, action="replacement_rollback"
                                )
                                raise WorkspaceConflictError(
                                    "replacement metadata changed during save",
                                    details={
                                        "relative_path": canonical,
                                        "reason": "replacement_metadata_changed",
                                    },
                                )
                            assert recovery_name is not None
                            self._copy_descriptor_to_recovery(
                                old_descriptor,
                                paths=paths,
                                recovery_name=recovery_name,
                                mode=before.mode,
                            )
                            result_observation = access.observe_workspace_file(
                                root,
                                identity,
                                canonical,
                                max_bytes=WORKSPACE_MAX_FILE_BYTES,
                            )
                        except Exception as exc:
                            rollback_confirmed = not exchanged
                            if exchanged:
                                try:
                                    atomic_exchange_at(
                                        parent_descriptor,
                                        temporary_name,
                                        existing_raw_name,
                                    )
                                    exchanged = False
                                    self._sync_directory(
                                        parent_descriptor,
                                        action="replacement_rollback",
                                    )
                                    rollback_confirmed = True
                                except Exception:  # noqa: BLE001
                                    # If the rollback itself fails, the temporary
                                    # name may be the only remaining link to the
                                    # previous inode. Never delete it blindly.
                                    cleanup_temporary = False
                                    rollback_confirmed = False
                            self._discard_private_recovery(paths, recovery_name)
                            if isinstance(exc, OSError):
                                raise WorkspaceUnavailableError(
                                    "work folder replacement could not be completed",
                                    details={
                                        "relative_path": canonical,
                                        "reason": f"replacement_failed:{exc.errno}",
                                        "rollback_confirmed": rollback_confirmed,
                                    },
                                ) from exc
                            raise
                        finally:
                            if old_descriptor is not None:
                                os.close(old_descriptor)
                            if cleanup_temporary:
                                self._remove_temporary(
                                    parent_descriptor, temporary_name
                                )

                mutation_completed = True
            except Exception:
                if operation == "replace" and not mutation_completed:
                    with suppress(Exception):
                        self._discard_recovery_record(
                            workspace_id=row["workspace_id"],
                            recovery_id=recovery_id,
                            paths=paths,
                            recovery_name=recovery_name,
                        )
                if source_corpus_id is not None and not mutation_completed:
                    unchanged = False
                    try:
                        if expected_version == WORKSPACE_EXPECTED_ABSENT:
                            state = access.inspect_workspace_file(
                                root,
                                identity,
                                canonical,
                            )
                            unchanged = state.state == "missing"
                        else:
                            unchanged = (
                                access.observe_workspace_file(
                                    root,
                                    identity,
                                    canonical,
                                    max_bytes=WORKSPACE_MAX_FILE_BYTES,
                                ).version_token
                                == expected_version
                            )
                    # Cleanup only needs to know whether the original state survived.
                    except Exception:  # noqa: BLE001
                        unchanged = False
                    if unchanged:
                        with suppress(Exception):
                            self._clear_prepared_index_change(
                                paths=paths,
                                relative_path=canonical,
                            )
                raise

            assert result_observation is not None
            if source_corpus_id is not None:
                with suppress(Exception):
                    self._mark_index_change_dirty(
                        paths=paths,
                        source_corpus_id=source_corpus_id,
                        relative_path=canonical,
                        result_version=result_observation.version_token,
                    )
            try:
                with workspace_connection(self.data_root) as connection:
                    current = self._load_row(
                        row["workspace_id"],
                        audience=audience,
                        connection=connection,
                    )
                    if int(current["generation"]) != int(row["generation"]):
                        raise WorkspaceConflictError(
                            "work folder selection changed during save",
                            details={"reason": "generation_changed"},
                        )
                    if operation == "replace":
                        updated = connection.execute(
                            """
                            UPDATE workspace_recoveries
                            SET result_version_token = ?, metadata_json = ?,
                                state = 'available', updated_at = ?
                            WHERE recovery_id = ?
                              AND workspace_id = ? AND state = 'prepared'
                            """,
                            (
                                result_observation.version_token,
                                encode_json(
                                    {
                                        "mode": stat.S_IMODE(result_observation.mode),
                                        "content_encoding": content_encoding,
                                    }
                                ),
                                now,
                                recovery_id,
                                row["workspace_id"],
                            ),
                        ).rowcount
                        if updated != 1:
                            raise WorkspaceConflictError(
                                "work folder recovery changed during save",
                                details={"reason": "recovery_state_changed"},
                            )
                    if make_current:
                        new_generation = int(row["generation"]) + 1
                        updated = connection.execute(
                            """
                            UPDATE workspaces
                            SET current_relative_path = ?, generation = ?, updated_at = ?
                            WHERE workspace_id = ? AND generation = ?
                            """,
                            (
                                canonical,
                                new_generation,
                                now,
                                row["workspace_id"],
                                row["generation"],
                            ),
                        ).rowcount
                        if updated != 1:
                            raise WorkspaceConflictError(
                                "work folder selection changed during save",
                                details={"reason": "generation_changed"},
                            )
                        row["current_relative_path"] = canonical
                        row["generation"] = new_generation
                        row["updated_at"] = now
            except Exception as exc:
                if operation == "create":
                    raise WorkspaceUnavailableError(
                        "work folder file was created, but current-file selection "
                        "could not be finalized",
                        details={
                            "reason": "metadata_finalize_failed",
                            "file_saved": True,
                            "relative_path": canonical,
                            "current_version": result_observation.version_token,
                            "recovery_id": None,
                        },
                    ) from exc
                # Replacement recovery was finalized in the same transaction as
                # current-file selection. Reconcile it to an available undo
                # point so a harmless selection failure does not hide recovery.
                with (
                    suppress(Exception),
                    workspace_connection(self.data_root) as connection,
                ):
                    connection.execute(
                        """
                        UPDATE workspace_recoveries
                        SET result_version_token = ?, metadata_json = ?,
                            state = 'available', updated_at = ?
                        WHERE recovery_id = ? AND workspace_id = ?
                          AND state = 'prepared'
                        """,
                        (
                            result_observation.version_token,
                            encode_json(
                                {
                                    "mode": stat.S_IMODE(result_observation.mode),
                                    "content_encoding": content_encoding,
                                }
                            ),
                            utc_now(),
                            recovery_id,
                            row["workspace_id"],
                        ),
                    )
                raise WorkspaceUnavailableError(
                    "work folder file was saved but its Corpus state could not be finalized",
                    details={
                        "reason": "metadata_finalize_failed",
                        "file_saved": True,
                        "relative_path": canonical,
                        "current_version": result_observation.version_token,
                        "recovery_id": recovery_id if operation == "replace" else None,
                    },
                ) from exc
            if operation == "replace":
                with suppress(Exception):
                    self._maintain_recoveries_locked(row=row, paths=paths)
            return {
                "work_folder": self._project(row, audience=audience),
                "file": self._observation_dict(result_observation),
                "created": expected_version == WORKSPACE_EXPECTED_ABSENT,
                "recovery_id": recovery_id if operation == "replace" else None,
                "undo_available": operation == "replace",
                "index_state": (
                    "pending_refresh"
                    if source_corpus_id is not None
                    else "not_applicable"
                ),
            }

    def delete(
        self,
        *,
        workspace_id: str,
        relative_path: str,
        expected_version: str,
        confirm_delete: bool,
        audience: str = "local_cli",
    ) -> dict[str, Any]:
        """Delete one observed file if its path and version are unchanged."""

        self._validate_expected_version(expected_version)
        if expected_version == WORKSPACE_EXPECTED_ABSENT:
            raise WorkspaceValidationError("delete requires an observed v1 version")
        if confirm_delete is not True:
            raise WorkspaceValidationError(
                "file deletion requires explicit confirmation"
            )

        access = _workspace_access()
        canonical = access.normalize_workspace_relative_path(relative_path)
        self._resolve_workspace_location(workspace_id)
        with workspace_writer_lock(self.data_root):
            row = self._load_row(workspace_id, audience=audience)
            identity = self._require_connected(row)
            paths = WorkspaceRuntimePaths(self.data_root, row["workspace_id"])
            paths.ensure()
            root = Path(row["root_path"])
            source_corpus_id = self._source_corpus_id_for_row(row)
            deleted = False
            try:
                with (
                    access.opened_workspace_root(root, identity) as root_descriptor,
                    access.opened_workspace_parent(
                        root_descriptor,
                        canonical,
                    ) as (parent_descriptor, _canonical_name, existing_raw_name),
                ):
                    if existing_raw_name is None:
                        raise WorkspaceConflictError(
                            "work folder file no longer exists",
                            details={"relative_path": canonical, "reason": "missing"},
                        )
                    descriptor = os.open(
                        existing_raw_name,
                        os.O_RDONLY
                        | getattr(os, "O_CLOEXEC", 0)
                        | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=parent_descriptor,
                    )
                    try:
                        before = access.workspace_file_observation_from_descriptor(
                            descriptor,
                            relative_path=canonical,
                            max_bytes=WORKSPACE_MAX_FILE_BYTES,
                        )
                        current_path = os.stat(
                            existing_raw_name,
                            dir_fd=parent_descriptor,
                            follow_symlinks=False,
                        )
                        if (
                            current_path.st_dev != before.device
                            or current_path.st_ino != before.inode
                        ):
                            raise WorkspaceConflictError(
                                "work folder file changed before deletion",
                                details={
                                    "relative_path": canonical,
                                    "reason": "delete_source_changed",
                                },
                            )
                        if before.version_token != expected_version:
                            raise WorkspaceConflictError(
                                "work folder file changed before deletion",
                                details={
                                    "relative_path": canonical,
                                    "reason": "stale_version",
                                    "current_version": before.version_token,
                                },
                            )
                        if source_corpus_id is not None:
                            self._prepare_index_change(
                                row=row,
                                paths=paths,
                                source_corpus_id=source_corpus_id,
                                relative_path=canonical,
                                operation="delete",
                                expected_version=expected_version,
                                intended_sha256=before.sha256,
                            )
                        try:
                            os.unlink(existing_raw_name, dir_fd=parent_descriptor)
                        except OSError as exc:
                            raise WorkspaceUnavailableError(
                                "work folder file could not be deleted",
                                details={
                                    "relative_path": canonical,
                                    "reason": f"delete_failed:{exc.errno}",
                                },
                            ) from exc
                        deleted = True
                        self._sync_directory(parent_descriptor, action="delete")
                    finally:
                        os.close(descriptor)
            except Exception:
                if not deleted and source_corpus_id is not None:
                    with suppress(Exception):
                        self._clear_prepared_index_change(
                            paths=paths,
                            relative_path=canonical,
                        )
                raise

            if source_corpus_id is not None:
                with suppress(Exception):
                    self._mark_index_change_dirty(
                        paths=paths,
                        source_corpus_id=source_corpus_id,
                        relative_path=canonical,
                        result_version=WORKSPACE_EXPECTED_ABSENT,
                    )
            now = utc_now()
            try:
                with workspace_connection(self.data_root) as connection:
                    current = self._load_row(
                        row["workspace_id"],
                        audience=audience,
                        connection=connection,
                    )
                    if current["current_relative_path"] == canonical:
                        new_generation = int(current["generation"]) + 1
                        connection.execute(
                            """
                            UPDATE workspaces
                            SET current_relative_path = NULL, generation = ?, updated_at = ?
                            WHERE workspace_id = ? AND generation = ?
                            """,
                            (
                                new_generation,
                                now,
                                row["workspace_id"],
                                current["generation"],
                            ),
                        )
                        row["current_relative_path"] = None
                        row["generation"] = new_generation
                        row["updated_at"] = now
            except Exception as exc:
                raise WorkspaceUnavailableError(
                    "work folder file was deleted but Current File state could not be updated",
                    details={
                        "relative_path": canonical,
                        "reason": "metadata_finalize_failed",
                        "file_deleted": True,
                    },
                ) from exc
            return {
                "work_folder": self._project(row, audience=audience),
                "relative_path": canonical,
                "deleted": True,
                "index_state": (
                    "pending_refresh"
                    if source_corpus_id is not None
                    else "not_applicable"
                ),
            }

    @staticmethod
    def _read_private_recovery(
        *,
        paths: WorkspaceRuntimePaths,
        recovery_name: str,
        relative_path: str,
    ) -> Any:
        if not re.fullmatch(r"wrec_[0-9a-f]{32}\.bin", recovery_name):
            raise WorkspaceBoundaryError(
                "work folder recovery record is unsafe",
                details={"reason": "invalid_recovery_path"},
            )
        access = _workspace_access()
        with paths.open_workspace_directory("recovery") as parent_descriptor:
            try:
                before = os.stat(
                    recovery_name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
                descriptor = os.open(
                    recovery_name,
                    os.O_RDONLY
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=parent_descriptor,
                )
            except OSError as exc:
                raise WorkspaceUnavailableError(
                    "work folder recovery copy is unavailable",
                    details={"reason": f"recovery_open_failed:{exc.errno}"},
                ) from exc
            try:
                opened = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(before.st_mode)
                    or before.st_dev != opened.st_dev
                    or before.st_ino != opened.st_ino
                    or stat.S_IMODE(opened.st_mode) != 0o600
                    or opened.st_nlink != 1
                ):
                    raise WorkspaceBoundaryError(
                        "work folder recovery copy is unsafe",
                        details={"reason": "unsafe_recovery_file"},
                    )
                return access.workspace_file_observation_from_descriptor(
                    descriptor,
                    relative_path=relative_path,
                    max_bytes=WORKSPACE_MAX_FILE_BYTES,
                    return_data=True,
                )
            finally:
                os.close(descriptor)

    def restore(
        self,
        *,
        workspace_id: str,
        recovery_id: str,
        expected_version: str,
        audience: str = "local_cli",
    ) -> dict[str, Any]:
        """Restore one replacement only while its resulting file is unchanged."""

        self._validate_expected_version(expected_version)
        if expected_version == WORKSPACE_EXPECTED_ABSENT:
            raise WorkspaceValidationError("restore requires an observed v1 version")
        if not isinstance(recovery_id, str) or not WORKSPACE_RECOVERY_ID_RE.fullmatch(
            recovery_id
        ):
            raise WorkspaceValidationError("recovery_id is invalid")
        access = _workspace_access()
        self._resolve_workspace_location(workspace_id)
        with workspace_writer_lock(self.data_root):
            row = self._load_row(workspace_id, audience=audience)
            identity = self._require_connected(row)
            paths = WorkspaceRuntimePaths(self.data_root, row["workspace_id"])
            paths.ensure()
            with suppress(Exception):
                self._maintain_recoveries_locked(row=row, paths=paths)
            with workspace_read_connection(self.data_root) as connection:
                recovery = connection.execute(
                    """
                    SELECT * FROM workspace_recoveries
                    WHERE recovery_id = ? AND workspace_id = ?
                    """,
                    (recovery_id, row["workspace_id"]),
                ).fetchone()
            if recovery is None:
                raise WorkspaceNotFoundError("work folder recovery does not exist")
            recovery = dict(recovery)
            if (
                recovery["state"] not in {"available", "prepared"}
                or recovery["operation"] != "replace"
            ):
                raise WorkspaceConflictError(
                    "work folder recovery is no longer available",
                    details={"reason": "recovery_not_available"},
                )
            if (
                recovery["state"] == "available"
                and recovery["result_version_token"] != expected_version
            ):
                raise WorkspaceConflictError(
                    "recovery does not match the expected work folder version",
                    details={"reason": "recovery_version_mismatch"},
                )
            recovery_name = recovery["recovery_relative_path"]
            if not isinstance(recovery_name, str):
                raise WorkspaceUnavailableError(
                    "work folder recovery copy is unavailable",
                    details={"reason": "missing_recovery_path"},
                )
            canonical = access.normalize_workspace_relative_path(
                recovery["relative_path"]
            )
            recovery_read = self._read_private_recovery(
                paths=paths,
                recovery_name=recovery_name,
                relative_path=canonical,
            )
            root = Path(row["root_path"])
            current = access.observe_workspace_file(
                root,
                identity,
                canonical,
                max_bytes=WORKSPACE_MAX_FILE_BYTES,
            )
            if current.version_token != expected_version:
                raise WorkspaceConflictError(
                    "work folder file changed after the recovery was created",
                    details={
                        "relative_path": canonical,
                        "reason": "stale_recovery",
                        "current_version": current.version_token,
                    },
                )
            recovery_metadata = json.loads(recovery["metadata_json"])
            if recovery["state"] == "prepared" and (
                recovery_metadata.get("intended_sha256") != current.sha256
            ):
                raise WorkspaceConflictError(
                    "interrupted recovery does not match the current work folder file",
                    details={"reason": "prepared_recovery_mismatch"},
                )
            if current.hardlinked:
                raise WorkspaceBoundaryError(
                    "hard-linked work folder files cannot be restored",
                    details={
                        "relative_path": canonical,
                        "reason": "unexpected_link_count",
                    },
                )
            source_corpus_id = self._source_corpus_id_for_row(row)
            if source_corpus_id is not None:
                self._prepare_index_change(
                    row=row,
                    paths=paths,
                    source_corpus_id=source_corpus_id,
                    relative_path=canonical,
                    operation="restore",
                    expected_version=expected_version,
                    intended_sha256=recovery_read.observation.sha256,
                )

            result_observation: Any | None = None
            with (
                access.opened_workspace_root(root, identity) as root_descriptor,
                access.opened_workspace_parent(
                    root_descriptor,
                    canonical,
                ) as (parent_descriptor, _canonical_name, existing_raw_name),
            ):
                if existing_raw_name is None:
                    raise WorkspaceConflictError(
                        "work folder file no longer exists",
                        details={"relative_path": canonical, "reason": "missing"},
                    )
                self._require_metadata_safe_parent(
                    parent_descriptor,
                    relative_path=canonical,
                )
                temporary_name = self._write_temporary(
                    parent_descriptor,
                    payload=recovery_read.data,
                    mode=0o600,
                )
                preserved_metadata: FileMetadataSnapshot | None = None
                old_descriptor: int | None = None
                exchanged = False
                cleanup_temporary = True
                try:
                    preserved_metadata = self._preserve_existing_metadata(
                        parent_descriptor,
                        existing_name=existing_raw_name,
                        temporary_name=temporary_name,
                        before=current,
                        relative_path=canonical,
                    )
                    try:
                        atomic_exchange_at(
                            parent_descriptor,
                            temporary_name,
                            existing_raw_name,
                        )
                    except OSError as exc:
                        raise WorkspaceUnavailableError(
                            "work folder does not support an atomic restore",
                            details={
                                "relative_path": canonical,
                                "reason": f"atomic_exchange_failed:{exc.errno}",
                            },
                        ) from exc
                    exchanged = True
                    self._sync_directory(parent_descriptor, action="restore")
                    old_descriptor = os.open(
                        temporary_name,
                        os.O_RDONLY
                        | getattr(os, "O_CLOEXEC", 0)
                        | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=parent_descriptor,
                    )
                    observed_old = access.workspace_file_observation_from_descriptor(
                        old_descriptor,
                        relative_path=canonical,
                        max_bytes=WORKSPACE_MAX_FILE_BYTES,
                    )
                    if not self._same_file_after_exchange(current, observed_old):
                        atomic_exchange_at(
                            parent_descriptor,
                            temporary_name,
                            existing_raw_name,
                        )
                        exchanged = False
                        self._sync_directory(
                            parent_descriptor, action="restore_rollback"
                        )
                        raise WorkspaceConflictError(
                            "work folder file changed during restore",
                            details={
                                "relative_path": canonical,
                                "reason": "concurrent_restore",
                            },
                        )
                    try:
                        metadata_unchanged = (
                            snapshot_file_metadata(old_descriptor) == preserved_metadata
                        )
                        replacement_metadata_matches = self._metadata_matches_at(
                            parent_descriptor,
                            name=existing_raw_name,
                            expected=preserved_metadata,
                        )
                    except OSError:
                        metadata_unchanged = False
                        replacement_metadata_matches = False
                    if not metadata_unchanged:
                        atomic_exchange_at(
                            parent_descriptor,
                            temporary_name,
                            existing_raw_name,
                        )
                        exchanged = False
                        self._sync_directory(
                            parent_descriptor, action="restore_rollback"
                        )
                        raise WorkspaceConflictError(
                            "work folder metadata changed during restore",
                            details={
                                "relative_path": canonical,
                                "reason": "concurrent_metadata_change",
                            },
                        )
                    if not replacement_metadata_matches:
                        atomic_exchange_at(
                            parent_descriptor,
                            temporary_name,
                            existing_raw_name,
                        )
                        exchanged = False
                        self._sync_directory(
                            parent_descriptor, action="restore_rollback"
                        )
                        raise WorkspaceConflictError(
                            "replacement metadata changed during restore",
                            details={
                                "relative_path": canonical,
                                "reason": "replacement_metadata_changed",
                            },
                        )
                    result_observation = access.observe_workspace_file(
                        root,
                        identity,
                        canonical,
                        max_bytes=WORKSPACE_MAX_FILE_BYTES,
                    )
                except Exception as exc:
                    rollback_confirmed = not exchanged
                    if exchanged:
                        try:
                            atomic_exchange_at(
                                parent_descriptor,
                                temporary_name,
                                existing_raw_name,
                            )
                            exchanged = False
                            self._sync_directory(
                                parent_descriptor, action="restore_rollback"
                            )
                            rollback_confirmed = True
                        # Preserve the temporary inode when rollback cannot be confirmed.
                        except Exception:  # noqa: BLE001
                            cleanup_temporary = False
                            rollback_confirmed = False
                    if isinstance(exc, OSError):
                        raise WorkspaceUnavailableError(
                            "work folder restore could not be completed",
                            details={
                                "relative_path": canonical,
                                "reason": f"restore_failed:{exc.errno}",
                                "rollback_confirmed": rollback_confirmed,
                            },
                        ) from exc
                    raise
                finally:
                    if old_descriptor is not None:
                        os.close(old_descriptor)
                    if cleanup_temporary:
                        self._remove_temporary(parent_descriptor, temporary_name)

            assert result_observation is not None
            if source_corpus_id is not None:
                with suppress(Exception):
                    self._mark_index_change_dirty(
                        paths=paths,
                        source_corpus_id=source_corpus_id,
                        relative_path=canonical,
                        result_version=result_observation.version_token,
                    )
            restored_at = utc_now()
            metadata = recovery_metadata
            metadata["restored_version_token"] = result_observation.version_token
            metadata_recorded = False
            try:
                with workspace_connection(self.data_root) as connection:
                    updated = connection.execute(
                        """
                        UPDATE workspace_recoveries
                        SET state = 'restored', metadata_json = ?, updated_at = ?
                        WHERE recovery_id = ? AND workspace_id = ?
                          AND state IN ('available', 'prepared')
                        """,
                        (
                            encode_json(metadata),
                            restored_at,
                            recovery_id,
                            row["workspace_id"],
                        ),
                    ).rowcount
                    if updated != 1:
                        raise WorkspaceConflictError(
                            "work folder recovery changed during restore",
                            details={"reason": "recovery_state_changed"},
                        )
                metadata_recorded = True
            except Exception:  # noqa: BLE001
                # The user file has already been restored. Preserve the private
                # recovery artifact and report the incomplete bookkeeping rather
                # than claiming that the file operation itself failed.
                metadata_recorded = False
            if metadata_recorded:
                self._discard_private_recovery(paths, recovery_name)
            return {
                "work_folder": self._project(row, audience=audience),
                "file": self._observation_dict(result_observation),
                "recovery_id": recovery_id,
                "restored": True,
                "recovery_metadata_recorded": metadata_recorded,
                "index_state": (
                    "pending_refresh"
                    if source_corpus_id is not None
                    else "not_applicable"
                ),
            }
