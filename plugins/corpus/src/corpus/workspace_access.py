"""Descriptor-pinned, read-only access to explicitly connected work folders.

This module is deliberately independent of workspace registration and policy
storage.  Callers persist the returned root identity and present it on every
operation.  All filesystem traversal is relative to a verified directory
descriptor and symbolic links are never followed.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .errors import (
    WorkspaceBoundaryError,
    WorkspaceConflictError,
    WorkspaceUnavailableError,
    WorkspaceValidationError,
)

WORKSPACE_MAX_COMPONENT_BYTES = 255
WORKSPACE_MAX_PATH_BYTES = 4096
WORKSPACE_MAX_PATH_COMPONENTS = 64
WORKSPACE_DEFAULT_MAX_READ_BYTES = 2 * 1024 * 1024
WORKSPACE_MAX_READ_BYTES = 250 * 1024 * 1024
WORKSPACE_DEFAULT_MAX_ENTRIES = 1_000
WORKSPACE_MAX_ENTRIES = 20_000
WORKSPACE_MAX_LIST_DEPTH = 64
WORKSPACE_VERSION_PREFIX = "v1:"
_READ_CHUNK_BYTES = 1024 * 1024
SF_DATALESS = getattr(stat, "SF_DATALESS", 0x40000000)

_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
_FILE_FLAGS = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)


@dataclass(frozen=True)
class WorkspaceExclusionPolicy:
    """Names that are unavailable through every work-folder entry point."""

    sensitive_names: frozenset[str]
    sensitive_suffixes: tuple[str, ...]
    temporary_prefixes: tuple[str, ...]
    temporary_suffixes: tuple[str, ...]
    excluded_directory_names: frozenset[str]
    exclude_hidden: bool = True


DEFAULT_WORKSPACE_EXCLUSION_POLICY = WorkspaceExclusionPolicy(
    sensitive_names=frozenset(
        {
            ".env",
            ".netrc",
            ".npmrc",
            ".pypirc",
            "credentials",
            "credentials.json",
            "id_dsa",
            "id_ed25519",
            "id_rsa",
        }
    ),
    sensitive_suffixes=(
        ".cer",
        ".crt",
        ".der",
        ".jks",
        ".key",
        ".keystore",
        ".p12",
        ".pem",
        ".pfx",
    ),
    temporary_prefixes=("~$", ".~lock."),
    temporary_suffixes=(
        ".bak",
        ".crdownload",
        ".lock",
        ".part",
        ".swp",
        ".temp",
        ".tmp",
    ),
    excluded_directory_names=frozenset(
        {
            "__pycache__",
            "node_modules",
        }
    ),
)


@dataclass(frozen=True)
class WorkspaceRootIdentity:
    device: int
    inode: int

    def as_tuple(self) -> tuple[int, int]:
        return (self.device, self.inode)


@dataclass(frozen=True)
class WorkspaceRootRegistration:
    root: Path
    identity: WorkspaceRootIdentity


@dataclass(frozen=True)
class WorkspaceEntry:
    relative_path: str
    kind: Literal["file", "directory"]
    size: int
    modified_ns: int
    residency_state: Literal["resident", "remote_only"] = "resident"


@dataclass(frozen=True)
class WorkspaceListing:
    entries: tuple[WorkspaceEntry, ...]
    truncated: bool
    skipped_symlinks: int
    skipped_special: int
    skipped_excluded: int


@dataclass(frozen=True)
class WorkspaceFileObservation:
    relative_path: str
    size: int
    mode: int
    modified_ns: int
    changed_ns: int
    device: int
    inode: int
    link_count: int
    hardlinked: bool
    sha256: str
    version_token: str


@dataclass(frozen=True)
class WorkspaceFileRead:
    data: bytes
    observation: WorkspaceFileObservation


@dataclass(frozen=True)
class WorkspaceFileState:
    """Metadata-only state for a selected file without opening its contents."""

    relative_path: str
    state: Literal[
        "ready",
        "missing",
        "remote_only",
        "directory",
        "symlink",
        "special",
    ]
    size: int | None = None
    modified_ns: int | None = None
    changed_ns: int | None = None
    device: int | None = None
    inode: int | None = None
    mode: int | None = None
    flags: int | None = None


def _metadata_flags(metadata: os.stat_result) -> int:
    return int(getattr(metadata, "st_flags", 0))


def _metadata_is_dataless(metadata: os.stat_result) -> bool:
    return bool(_metadata_flags(metadata) & SF_DATALESS)


def _validation_error(message: str, *, value: object, reason: str) -> WorkspaceValidationError:
    return WorkspaceValidationError(
        message,
        details={"relative_path": value, "reason": reason},
    )


def _require_component(component: str, *, relative_path: str) -> None:
    encoded = component.encode("utf-8")
    if (
        not component
        or component in {".", ".."}
        or "/" in component
        or "\\" in component
        or "\x00" in component
        or len(encoded) > WORKSPACE_MAX_COMPONENT_BYTES
        or any(unicodedata.category(character).startswith("C") for character in component)
    ):
        raise _validation_error(
            "work folder path contains an unsafe component",
            value=relative_path,
            reason="invalid_component",
        )


def normalize_workspace_relative_path(value: str) -> str:
    """Return one canonical NFC, root-relative POSIX path."""

    if not isinstance(value, str):
        raise _validation_error(
            "work folder path must be a string",
            value=value,
            reason="not_string",
        )
    if not value:
        raise _validation_error(
            "work folder path must not be empty",
            value=value,
            reason="empty",
        )
    if value.startswith(("/", "\\")) or "\\" in value or "\x00" in value:
        raise _validation_error(
            "work folder path must be a relative POSIX path",
            value=value,
            reason="not_relative_posix",
        )
    canonical = unicodedata.normalize("NFC", value)
    encoded = canonical.encode("utf-8")
    if len(encoded) > WORKSPACE_MAX_PATH_BYTES:
        raise _validation_error(
            "work folder path exceeds the byte limit",
            value=value,
            reason="path_too_long",
        )
    parts = canonical.split("/")
    if len(parts) > WORKSPACE_MAX_PATH_COMPONENTS:
        raise _validation_error(
            "work folder path is too deep",
            value=value,
            reason="path_too_deep",
        )
    for component in parts:
        _require_component(component, relative_path=canonical)
    return "/".join(parts)


def _canonical_parts(relative_path: str) -> tuple[str, tuple[str, ...]]:
    canonical = normalize_workspace_relative_path(relative_path)
    return canonical, tuple(canonical.split("/"))


def workspace_path_is_excluded(
    relative_path: str,
    *,
    policy: WorkspaceExclusionPolicy = DEFAULT_WORKSPACE_EXCLUSION_POLICY,
) -> bool:
    """Return whether any component is hidden, sensitive, temporary, or cached."""

    canonical, parts = _canonical_parts(relative_path)
    del canonical
    for component in parts:
        folded = component.casefold()
        if policy.exclude_hidden and component.startswith("."):
            return True
        if folded in policy.sensitive_names:
            return True
        if folded in policy.excluded_directory_names:
            return True
        if any(folded.startswith(prefix.casefold()) for prefix in policy.temporary_prefixes):
            return True
        if any(folded.endswith(suffix.casefold()) for suffix in policy.temporary_suffixes):
            return True
        if any(folded.endswith(suffix.casefold()) for suffix in policy.sensitive_suffixes):
            return True
    return False


def _require_included(relative_path: str, *, policy: WorkspaceExclusionPolicy) -> None:
    if workspace_path_is_excluded(relative_path, policy=policy):
        raise WorkspaceBoundaryError(
            "work folder path is excluded by policy",
            details={"relative_path": relative_path, "reason": "excluded_path"},
        )


def _open_absolute_directory(root: Path) -> int:
    if not root.is_absolute():
        raise WorkspaceValidationError(
            "work folder root must be absolute",
            details={"root": str(root), "reason": "not_absolute"},
        )
    parts = root.parts
    if len(parts) < 2:
        raise WorkspaceBoundaryError(
            "filesystem root cannot be used as a work folder",
            details={"root": str(root), "reason": "root_not_allowed"},
        )
    try:
        descriptor = os.open(parts[0], _DIRECTORY_FLAGS)
    except OSError as exc:
        raise WorkspaceUnavailableError(
            "work folder root is unavailable",
            details={"root": str(root), "reason": f"open_failed:{exc.errno}"},
        ) from exc
    try:
        for component in parts[1:]:
            _require_component(component, relative_path=str(root))
            next_descriptor = os.open(component, _DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            raise WorkspaceUnavailableError(
                "work folder root is not a directory",
                details={"root": str(root), "reason": "not_directory"},
            )
        return descriptor
    except WorkspaceValidationError as exc:
        os.close(descriptor)
        raise WorkspaceBoundaryError(
            "work folder root contains an unsafe component",
            details={"root": str(root), "reason": "unsafe_root"},
        ) from exc
    except OSError as exc:
        os.close(descriptor)
        reason = (
            "symlink_or_unsafe_root" if exc.errno in {getattr(os, "ELOOP", 62)} else "open_failed"
        )
        raise WorkspaceUnavailableError(
            "work folder root could not be opened",
            details={"root": str(root), "reason": f"{reason}:{exc.errno}"},
        ) from exc
    except Exception:
        os.close(descriptor)
        raise


def register_workspace_root(root: Path) -> WorkspaceRootRegistration:
    """Canonicalize a selected root and bind it to its current filesystem identity."""

    expanded = root.expanduser()
    if not expanded.is_absolute():
        raise WorkspaceValidationError(
            "work folder root must be absolute",
            details={"root": str(root), "reason": "not_absolute"},
        )
    try:
        canonical = expanded.resolve(strict=True)
    except OSError as exc:
        raise WorkspaceUnavailableError(
            "work folder root is unavailable",
            details={"root": str(expanded), "reason": f"resolve_failed:{exc.errno}"},
        ) from exc
    with _opened_unverified_root(canonical) as descriptor:
        metadata = os.fstat(descriptor)
        return WorkspaceRootRegistration(
            root=canonical,
            identity=WorkspaceRootIdentity(metadata.st_dev, metadata.st_ino),
        )


def workspace_root_identity(root: Path) -> WorkspaceRootIdentity:
    """Observe the current identity of a canonical root path."""

    with _opened_unverified_root(root) as descriptor:
        metadata = os.fstat(descriptor)
        return WorkspaceRootIdentity(metadata.st_dev, metadata.st_ino)


@contextmanager
def _opened_unverified_root(root: Path) -> Iterator[int]:
    descriptor = _open_absolute_directory(root)
    try:
        yield descriptor
    finally:
        os.close(descriptor)


@contextmanager
def opened_workspace_root(
    root: Path,
    expected_identity: WorkspaceRootIdentity,
) -> Iterator[int]:
    """Open a work-folder root and require the identity stored at registration."""

    with _opened_unverified_root(root) as descriptor:
        current = workspace_identity_from_stat(os.fstat(descriptor))
        if current != expected_identity:
            raise WorkspaceUnavailableError(
                "work folder root changed after it was connected",
                details={
                    "reason": "root_identity_changed",
                    "expected": expected_identity.as_tuple(),
                    "current": current.as_tuple(),
                },
            )
        yield descriptor


def workspace_identity_from_stat(metadata: os.stat_result) -> WorkspaceRootIdentity:
    return WorkspaceRootIdentity(metadata.st_dev, metadata.st_ino)


def _raw_component_for_canonical(
    directory_descriptor: int,
    canonical_component: str,
    *,
    relative_path: str,
) -> str | None:
    try:
        names = os.listdir(directory_descriptor)
    except OSError as exc:
        raise WorkspaceUnavailableError(
            "work folder directory is unavailable",
            details={"relative_path": relative_path, "reason": f"list_failed:{exc.errno}"},
        ) from exc
    matches = [name for name in names if unicodedata.normalize("NFC", name) == canonical_component]
    if len(matches) > 1:
        raise WorkspaceConflictError(
            "work folder path collides after Unicode normalization",
            details={"relative_path": relative_path, "reason": "unicode_collision"},
        )
    return matches[0] if matches else None


def _open_child_directory(
    parent_descriptor: int,
    raw_name: str,
    *,
    relative_path: str,
    expected: os.stat_result | None = None,
) -> int:
    try:
        before = os.stat(raw_name, dir_fd=parent_descriptor, follow_symlinks=False)
    except OSError as exc:
        raise WorkspaceUnavailableError(
            "work folder directory is unavailable",
            details={"relative_path": relative_path, "reason": f"stat_failed:{exc.errno}"},
        ) from exc
    if stat.S_ISLNK(before.st_mode):
        raise WorkspaceBoundaryError(
            "symbolic links are not available in work folders",
            details={"relative_path": relative_path, "reason": "symlink"},
        )
    if not stat.S_ISDIR(before.st_mode):
        raise WorkspaceBoundaryError(
            "work folder parent is not a directory",
            details={"relative_path": relative_path, "reason": "not_directory"},
        )
    if expected is not None and (
        before.st_dev != expected.st_dev or before.st_ino != expected.st_ino
    ):
        raise WorkspaceConflictError(
            "work folder directory changed before it was opened",
            details={"relative_path": relative_path, "reason": "directory_changed"},
        )
    try:
        descriptor = os.open(raw_name, _DIRECTORY_FLAGS, dir_fd=parent_descriptor)
    except OSError as exc:
        raise WorkspaceUnavailableError(
            "work folder directory could not be opened",
            details={"relative_path": relative_path, "reason": f"open_failed:{exc.errno}"},
        ) from exc
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
        ):
            raise WorkspaceConflictError(
                "work folder directory changed while it was opened",
                details={"relative_path": relative_path, "reason": "directory_changed"},
            )
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


@contextmanager
def opened_workspace_parent(
    root_descriptor: int,
    canonical_relative_path: str,
    *,
    create_parents: bool = False,
    policy: WorkspaceExclusionPolicy = DEFAULT_WORKSPACE_EXCLUSION_POLICY,
) -> Iterator[tuple[int, str, str | None]]:
    """Yield a pinned parent fd, canonical final name, and existing raw final name."""

    canonical, parts = _canonical_parts(canonical_relative_path)
    _require_included(canonical, policy=policy)
    current = os.dup(root_descriptor)
    try:
        for index, canonical_component in enumerate(parts[:-1], start=1):
            current_path = "/".join(parts[:index])
            raw_component = _raw_component_for_canonical(
                current,
                canonical_component,
                relative_path=current_path,
            )
            if raw_component is None:
                if not create_parents:
                    raise WorkspaceUnavailableError(
                        "work folder parent does not exist",
                        details={"relative_path": current_path, "reason": "missing"},
                    )
                try:
                    os.mkdir(canonical_component, 0o755, dir_fd=current)
                    os.fsync(current)
                except FileExistsError:
                    pass
                except OSError as exc:
                    raise WorkspaceUnavailableError(
                        "work folder parent could not be created",
                        details={
                            "relative_path": current_path,
                            "reason": f"create_failed:{exc.errno}",
                        },
                    ) from exc
                raw_component = _raw_component_for_canonical(
                    current,
                    canonical_component,
                    relative_path=current_path,
                )
                if raw_component is None:
                    raise WorkspaceConflictError(
                        "created work folder parent could not be found",
                        details={"relative_path": current_path, "reason": "create_race"},
                    )
            child = _open_child_directory(
                current,
                raw_component,
                relative_path=current_path,
            )
            os.close(current)
            current = child

        final_name = parts[-1]
        existing_raw_name = _raw_component_for_canonical(
            current,
            final_name,
            relative_path=canonical,
        )
        yield current, final_name, existing_raw_name
    finally:
        os.close(current)


def _validate_integer_bound(value: int, *, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise WorkspaceValidationError(
            f"{field} is outside the supported range",
            details={"field": field, "minimum": minimum, "maximum": maximum},
        )
    return value


def _entry_metadata(
    parent_descriptor: int,
    raw_name: str,
    *,
    relative_path: str,
) -> os.stat_result:
    try:
        return os.stat(raw_name, dir_fd=parent_descriptor, follow_symlinks=False)
    except OSError as exc:
        raise WorkspaceUnavailableError(
            "work folder entry is unavailable",
            details={"relative_path": relative_path, "reason": f"stat_failed:{exc.errno}"},
        ) from exc


def _open_directory_path(
    root_descriptor: int,
    relative_path: str,
    *,
    policy: WorkspaceExclusionPolicy,
) -> int:
    canonical, parts = _canonical_parts(relative_path)
    _require_included(canonical, policy=policy)
    current = os.dup(root_descriptor)
    try:
        for index, canonical_component in enumerate(parts, start=1):
            current_path = "/".join(parts[:index])
            raw = _raw_component_for_canonical(
                current,
                canonical_component,
                relative_path=current_path,
            )
            if raw is None:
                raise WorkspaceUnavailableError(
                    "work folder directory does not exist",
                    details={"relative_path": current_path, "reason": "missing"},
                )
            child = _open_child_directory(current, raw, relative_path=current_path)
            os.close(current)
            current = child
        return current
    except Exception:
        os.close(current)
        raise


def list_workspace(
    root: Path,
    expected_identity: WorkspaceRootIdentity,
    *,
    relative_path: str | None = None,
    max_entries: int = WORKSPACE_DEFAULT_MAX_ENTRIES,
    max_depth: int = 32,
    policy: WorkspaceExclusionPolicy = DEFAULT_WORKSPACE_EXCLUSION_POLICY,
) -> WorkspaceListing:
    """List a bounded work-folder subtree without following links or special files."""

    max_entries = _validate_integer_bound(
        max_entries,
        field="max_entries",
        minimum=1,
        maximum=WORKSPACE_MAX_ENTRIES,
    )
    max_depth = _validate_integer_bound(
        max_depth,
        field="max_depth",
        minimum=0,
        maximum=WORKSPACE_MAX_LIST_DEPTH,
    )
    canonical_base = (
        normalize_workspace_relative_path(relative_path) if relative_path is not None else None
    )

    entries: list[WorkspaceEntry] = []
    skipped_symlinks = 0
    skipped_special = 0
    skipped_excluded = 0
    truncated = False

    def walk_directory(
        directory_descriptor: int,
        *,
        parent_path: str,
        depth: int,
    ) -> None:
        nonlocal skipped_excluded
        nonlocal skipped_special
        nonlocal skipped_symlinks
        nonlocal truncated

        try:
            raw_names = os.listdir(directory_descriptor)
        except OSError as exc:
            raise WorkspaceUnavailableError(
                "work folder directory could not be listed",
                details={
                    "relative_path": parent_path or ".",
                    "reason": f"list_failed:{exc.errno}",
                },
            ) from exc

        canonical_names: dict[str, str] = {}
        for raw_name in raw_names:
            canonical_name = unicodedata.normalize("NFC", raw_name)
            _require_component(
                canonical_name,
                relative_path=(
                    f"{parent_path}/{canonical_name}" if parent_path else canonical_name
                ),
            )
            previous = canonical_names.setdefault(canonical_name, raw_name)
            if previous != raw_name:
                raise WorkspaceConflictError(
                    "work folder names collide after Unicode normalization",
                    details={
                        "relative_path": parent_path or ".",
                        "reason": "unicode_collision",
                    },
                )

        children: list[tuple[str, str, os.stat_result]] = []
        for canonical_name, raw_name in sorted(
            canonical_names.items(), key=lambda item: item[0].encode("utf-8")
        ):
            canonical_path = f"{parent_path}/{canonical_name}" if parent_path else canonical_name
            if workspace_path_is_excluded(canonical_path, policy=policy):
                skipped_excluded += 1
                continue
            metadata = _entry_metadata(
                directory_descriptor,
                raw_name,
                relative_path=canonical_path,
            )
            if stat.S_ISLNK(metadata.st_mode):
                skipped_symlinks += 1
                continue
            if stat.S_ISDIR(metadata.st_mode):
                if len(entries) >= max_entries:
                    truncated = True
                    break
                entries.append(
                    WorkspaceEntry(
                        relative_path=canonical_path,
                        kind="directory",
                        size=0,
                        modified_ns=metadata.st_mtime_ns,
                        residency_state="resident",
                    )
                )
                if depth < max_depth:
                    children.append((canonical_path, raw_name, metadata))
                continue
            if stat.S_ISREG(metadata.st_mode):
                if len(entries) >= max_entries:
                    truncated = True
                    break
                entries.append(
                    WorkspaceEntry(
                        relative_path=canonical_path,
                        kind="file",
                        size=metadata.st_size,
                        modified_ns=metadata.st_mtime_ns,
                        residency_state=(
                            "remote_only"
                            if _metadata_is_dataless(metadata)
                            else "resident"
                        ),
                    )
                )
                continue
            skipped_special += 1

        for child_path, raw_name, expected in children:
            if truncated:
                break
            child_descriptor = _open_child_directory(
                directory_descriptor,
                raw_name,
                relative_path=child_path,
                expected=expected,
            )
            try:
                walk_directory(
                    child_descriptor,
                    parent_path=child_path,
                    depth=depth + 1,
                )
            finally:
                os.close(child_descriptor)

    with opened_workspace_root(root, expected_identity) as root_descriptor:
        start_descriptor = (
            _open_directory_path(root_descriptor, canonical_base, policy=policy)
            if canonical_base is not None
            else os.dup(root_descriptor)
        )
        try:
            walk_directory(
                start_descriptor,
                parent_path=canonical_base or "",
                depth=0,
            )
        finally:
            os.close(start_descriptor)

        current = workspace_identity_from_stat(os.fstat(root_descriptor))
        if current != expected_identity:
            raise WorkspaceUnavailableError(
                "work folder root changed while it was listed",
                details={"reason": "root_identity_changed"},
            )
    return WorkspaceListing(
        entries=tuple(entries),
        truncated=truncated,
        skipped_symlinks=skipped_symlinks,
        skipped_special=skipped_special,
        skipped_excluded=skipped_excluded,
    )


def workspace_file_state_from_root_descriptor(
    root_descriptor: int,
    relative_path: str,
    *,
    policy: WorkspaceExclusionPolicy = DEFAULT_WORKSPACE_EXCLUSION_POLICY,
) -> WorkspaceFileState:
    """Inspect one path without opening or hydrating the final file."""

    canonical = normalize_workspace_relative_path(relative_path)
    _require_included(canonical, policy=policy)
    try:
        with opened_workspace_parent(
            root_descriptor,
            canonical,
            policy=policy,
        ) as (parent_descriptor, _canonical_name, raw_name):
            if raw_name is None:
                return WorkspaceFileState(relative_path=canonical, state="missing")
            metadata = _entry_metadata(
                parent_descriptor,
                raw_name,
                relative_path=canonical,
            )
    except WorkspaceUnavailableError as exc:
        if exc.details.get("reason") == "missing":
            return WorkspaceFileState(relative_path=canonical, state="missing")
        raise

    if stat.S_ISLNK(metadata.st_mode):
        state = "symlink"
    elif stat.S_ISDIR(metadata.st_mode):
        state = "directory"
    elif not stat.S_ISREG(metadata.st_mode):
        state = "special"
    elif _metadata_is_dataless(metadata):
        state = "remote_only"
    else:
        state = "ready"
    return WorkspaceFileState(
        relative_path=canonical,
        state=state,
        size=metadata.st_size,
        modified_ns=metadata.st_mtime_ns,
        changed_ns=metadata.st_ctime_ns,
        device=metadata.st_dev,
        inode=metadata.st_ino,
        mode=metadata.st_mode,
        flags=_metadata_flags(metadata),
    )


def inspect_workspace_file(
    root: Path,
    expected_identity: WorkspaceRootIdentity,
    relative_path: str,
    *,
    policy: WorkspaceExclusionPolicy = DEFAULT_WORKSPACE_EXCLUSION_POLICY,
) -> WorkspaceFileState:
    """Inspect a selected path while also revalidating the registered root."""

    with opened_workspace_root(root, expected_identity) as root_descriptor:
        return workspace_file_state_from_root_descriptor(
            root_descriptor,
            relative_path,
            policy=policy,
        )


def _stable_file_read(
    root: Path,
    expected_identity: WorkspaceRootIdentity,
    relative_path: str,
    *,
    max_bytes: int,
    return_data: bool,
    policy: WorkspaceExclusionPolicy,
) -> WorkspaceFileRead | WorkspaceFileObservation:
    max_bytes = _validate_integer_bound(
        max_bytes,
        field="max_bytes",
        minimum=0,
        maximum=WORKSPACE_MAX_READ_BYTES,
    )
    canonical = normalize_workspace_relative_path(relative_path)
    _require_included(canonical, policy=policy)
    with (
        opened_workspace_root(root, expected_identity) as root_descriptor,
        opened_workspace_parent(
            root_descriptor,
            canonical,
            policy=policy,
        ) as (parent_descriptor, _canonical_name, raw_name),
    ):
        if raw_name is None:
            raise WorkspaceUnavailableError(
                "work folder file does not exist",
                details={"relative_path": canonical, "reason": "missing"},
            )
        before = _entry_metadata(
            parent_descriptor,
            raw_name,
            relative_path=canonical,
        )
        if stat.S_ISLNK(before.st_mode):
            raise WorkspaceBoundaryError(
                "symbolic links are not available in work folders",
                details={"relative_path": canonical, "reason": "symlink"},
            )
        if not stat.S_ISREG(before.st_mode):
            raise WorkspaceBoundaryError(
                "only regular work folder files can be read",
                details={"relative_path": canonical, "reason": "not_regular_file"},
            )
        if _metadata_is_dataless(before):
            raise WorkspaceUnavailableError(
                "work folder file is stored remotely",
                details={"relative_path": canonical, "reason": "remote_only"},
            )
        if before.st_size > max_bytes:
            raise WorkspaceValidationError(
                "work folder file exceeds the read limit",
                details={
                    "relative_path": canonical,
                    "file_bytes": before.st_size,
                    "maximum_bytes": max_bytes,
                },
            )
        try:
            descriptor = os.open(raw_name, _FILE_FLAGS, dir_fd=parent_descriptor)
        except OSError as exc:
            raise WorkspaceUnavailableError(
                "work folder file could not be opened",
                details={"relative_path": canonical, "reason": f"open_failed:{exc.errno}"},
            ) from exc
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_dev != before.st_dev
                or opened.st_ino != before.st_ino
            ):
                raise WorkspaceConflictError(
                    "work folder file changed while it was opened",
                    details={"relative_path": canonical, "reason": "file_changed"},
                )
            result = workspace_file_observation_from_descriptor(
                descriptor,
                relative_path=canonical,
                max_bytes=max_bytes,
                return_data=return_data,
            )
        finally:
            os.close(descriptor)
        final_path = _entry_metadata(
            parent_descriptor,
            raw_name,
            relative_path=canonical,
        )
        observation = result.observation if isinstance(result, WorkspaceFileRead) else result
        if (
            final_path.st_dev != observation.device
            or final_path.st_ino != observation.inode
            or final_path.st_size != observation.size
            or final_path.st_mtime_ns != observation.modified_ns
            or final_path.st_ctime_ns != observation.changed_ns
            or final_path.st_mode != observation.mode
            or final_path.st_nlink != observation.link_count
        ):
            raise WorkspaceConflictError(
                "work folder file changed while it was read",
                details={"relative_path": canonical, "reason": "file_changed"},
            )
        return result


def _version_token(metadata: os.stat_result, sha256: str) -> str:
    token_payload = {
        "c": metadata.st_ctime_ns,
        "d": metadata.st_dev,
        "h": sha256,
        "i": metadata.st_ino,
        "m": metadata.st_mtime_ns,
        "n": metadata.st_nlink,
        "s": metadata.st_size,
        "v": 1,
    }
    encoded = (
        base64.urlsafe_b64encode(
            json.dumps(token_payload, separators=(",", ":"), sort_keys=True).encode()
        )
        .decode("ascii")
        .rstrip("=")
    )
    return f"{WORKSPACE_VERSION_PREFIX}{encoded}"


def workspace_file_observation_from_descriptor(
    descriptor: int,
    *,
    relative_path: str,
    max_bytes: int = WORKSPACE_DEFAULT_MAX_READ_BYTES,
    return_data: bool = False,
) -> WorkspaceFileObservation | WorkspaceFileRead:
    """Stably hash an already pinned regular-file descriptor.

    This is also used after an atomic exchange, when the old inode no longer has
    its original workspace path.  The descriptor is rewound before reading.  It
    remains owned by the caller and is left positioned at end-of-file.
    """

    canonical = normalize_workspace_relative_path(relative_path)
    max_bytes = _validate_integer_bound(
        max_bytes,
        field="max_bytes",
        minimum=0,
        maximum=WORKSPACE_MAX_READ_BYTES,
    )
    try:
        before = os.fstat(descriptor)
    except OSError as exc:
        raise WorkspaceUnavailableError(
            "work folder file descriptor is unavailable",
            details={"relative_path": canonical, "reason": f"fstat_failed:{exc.errno}"},
        ) from exc
    if not stat.S_ISREG(before.st_mode):
        raise WorkspaceBoundaryError(
            "only regular work folder files can be observed",
            details={"relative_path": canonical, "reason": "not_regular_file"},
        )
    if _metadata_is_dataless(before):
        raise WorkspaceUnavailableError(
            "work folder file is stored remotely",
            details={"relative_path": canonical, "reason": "remote_only"},
        )
    if before.st_size > max_bytes:
        raise WorkspaceValidationError(
            "work folder file exceeds the read limit",
            details={
                "relative_path": canonical,
                "file_bytes": before.st_size,
                "maximum_bytes": max_bytes,
            },
        )
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        digest = hashlib.sha256()
        chunks: list[bytes] = []
        observed_bytes = 0
        while True:
            request_bytes = min(_READ_CHUNK_BYTES, max_bytes + 1 - observed_bytes)
            try:
                chunk = os.read(descriptor, request_bytes)
            except InterruptedError:
                continue
            if not chunk:
                break
            observed_bytes += len(chunk)
            if observed_bytes > max_bytes:
                raise WorkspaceValidationError(
                    "work folder file grew beyond the read limit",
                    details={"relative_path": canonical, "maximum_bytes": max_bytes},
                )
            digest.update(chunk)
            if return_data:
                chunks.append(chunk)
        after = os.fstat(descriptor)
    except WorkspaceValidationError:
        raise
    except OSError as exc:
        raise WorkspaceUnavailableError(
            "work folder file could not be observed",
            details={"relative_path": canonical, "reason": f"read_failed:{exc.errno}"},
        ) from exc
    stable_fields = (
        "st_dev",
        "st_ino",
        "st_size",
        "st_mtime_ns",
        "st_ctime_ns",
        "st_mode",
        "st_nlink",
    )
    if observed_bytes != before.st_size or any(
        getattr(after, field) != getattr(before, field) for field in stable_fields
    ):
        raise WorkspaceConflictError(
            "work folder file changed while it was observed",
            details={"relative_path": canonical, "reason": "file_changed"},
        )
    sha256 = digest.hexdigest()
    observation = WorkspaceFileObservation(
        relative_path=canonical,
        size=before.st_size,
        mode=before.st_mode,
        modified_ns=before.st_mtime_ns,
        changed_ns=before.st_ctime_ns,
        device=before.st_dev,
        inode=before.st_ino,
        link_count=before.st_nlink,
        hardlinked=before.st_nlink != 1,
        sha256=sha256,
        version_token=_version_token(before, sha256),
    )
    if return_data:
        return WorkspaceFileRead(data=b"".join(chunks), observation=observation)
    return observation


def observe_workspace_file(
    root: Path,
    expected_identity: WorkspaceRootIdentity,
    relative_path: str,
    *,
    max_bytes: int = WORKSPACE_DEFAULT_MAX_READ_BYTES,
    policy: WorkspaceExclusionPolicy = DEFAULT_WORKSPACE_EXCLUSION_POLICY,
) -> WorkspaceFileObservation:
    """Return a stable, SHA-256-backed observation without returning file bytes."""

    result = _stable_file_read(
        root,
        expected_identity,
        relative_path,
        max_bytes=max_bytes,
        return_data=False,
        policy=policy,
    )
    assert isinstance(result, WorkspaceFileObservation)
    return result


def read_workspace_file(
    root: Path,
    expected_identity: WorkspaceRootIdentity,
    relative_path: str,
    *,
    max_bytes: int = WORKSPACE_DEFAULT_MAX_READ_BYTES,
    policy: WorkspaceExclusionPolicy = DEFAULT_WORKSPACE_EXCLUSION_POLICY,
) -> WorkspaceFileRead:
    """Read one regular file and return its bytes plus a stable version token."""

    result = _stable_file_read(
        root,
        expected_identity,
        relative_path,
        max_bytes=max_bytes,
        return_data=True,
        policy=policy,
    )
    assert isinstance(result, WorkspaceFileRead)
    return result


__all__ = [
    "DEFAULT_WORKSPACE_EXCLUSION_POLICY",
    "WORKSPACE_DEFAULT_MAX_ENTRIES",
    "WORKSPACE_DEFAULT_MAX_READ_BYTES",
    "WORKSPACE_MAX_COMPONENT_BYTES",
    "WORKSPACE_MAX_ENTRIES",
    "WORKSPACE_MAX_LIST_DEPTH",
    "WORKSPACE_MAX_PATH_BYTES",
    "WORKSPACE_MAX_PATH_COMPONENTS",
    "WORKSPACE_MAX_READ_BYTES",
    "WORKSPACE_VERSION_PREFIX",
    "WorkspaceEntry",
    "WorkspaceExclusionPolicy",
    "WorkspaceFileObservation",
    "WorkspaceFileRead",
    "WorkspaceFileState",
    "WorkspaceListing",
    "WorkspaceRootIdentity",
    "WorkspaceRootRegistration",
    "inspect_workspace_file",
    "list_workspace",
    "normalize_workspace_relative_path",
    "observe_workspace_file",
    "opened_workspace_parent",
    "opened_workspace_root",
    "read_workspace_file",
    "register_workspace_root",
    "workspace_identity_from_stat",
    "workspace_file_observation_from_descriptor",
    "workspace_file_state_from_root_descriptor",
    "workspace_path_is_excluded",
    "workspace_root_identity",
]
