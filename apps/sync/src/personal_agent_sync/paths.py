"""Descriptor-pinned local traversal and Finder move recovery."""

from __future__ import annotations

import hashlib
import os
import stat
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .errors import SyncError

DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
FILE_FLAGS = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
COPY_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class Snapshot:
    path: Path
    byte_size: int
    sha256: str
    modified_ns: int
    changed_ns: int
    device: int
    inode: int


def safe_parts(relative_path: str) -> tuple[str, ...]:
    if not isinstance(relative_path, str) or not relative_path or "\0" in relative_path:
        raise SyncError("unsafe_relative_path", "relative path is invalid")
    normalized = relative_path.replace("\\", "/")
    if normalized.startswith("/"):
        raise SyncError("unsafe_relative_path", "absolute paths are not accepted")
    parts = tuple(normalized.split("/"))
    if any(not part or part in {".", ".."} for part in parts):
        raise SyncError(
            "unsafe_relative_path", "relative path contains an unsafe component"
        )
    return parts


def resolve_moved_root(path: Path, device: int, inode: int) -> Path | None:
    try:
        metadata = path.stat()
        if stat.S_ISDIR(metadata.st_mode) and (metadata.st_dev, metadata.st_ino) == (
            device,
            inode,
        ):
            return path
    except OSError:
        pass
    if sys.platform != "darwin" or device < 0 or inode < 0:
        return None
    descriptor = -1
    try:
        descriptor = os.open(f"/.vol/{device}/{inode}", DIRECTORY_FLAGS)
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (device, inode):
            return None
        import fcntl

        encoded = fcntl.fcntl(descriptor, 50, b"\0" * 4096)
        current = encoded.split(b"\0", 1)[0].decode("utf-8")
        if not current.startswith("/"):
            return None
        resolved = Path(current)
        verified = resolved.stat()
        return (
            resolved if (verified.st_dev, verified.st_ino) == (device, inode) else None
        )
    except (ImportError, OSError, UnicodeError, ValueError):
        return None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def open_root(root: Path, expected: tuple[int, int]) -> int:
    try:
        descriptor = os.open(root, DIRECTORY_FLAGS)
        metadata = os.fstat(descriptor)
    except OSError as exc:
        raise SyncError(
            "source_unavailable", "Connection root cannot be opened safely"
        ) from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or (metadata.st_dev, metadata.st_ino) != expected
    ):
        os.close(descriptor)
        raise SyncError(
            "connection_identity_changed", "Connection root identity changed"
        )
    return descriptor


def open_relative(root_descriptor: int, relative_path: str) -> int:
    parts = safe_parts(relative_path)
    current = os.dup(root_descriptor)
    try:
        for component in parts[:-1]:
            next_descriptor = os.open(component, DIRECTORY_FLAGS, dir_fd=current)
            metadata = os.fstat(next_descriptor)
            if not stat.S_ISDIR(metadata.st_mode):
                os.close(next_descriptor)
                raise SyncError(
                    "unsafe_relative_path", "a path parent is not a directory"
                )
            os.close(current)
            current = next_descriptor
        before = os.stat(parts[-1], dir_fd=current, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode):
            raise SyncError("not_regular_file", "Source item is not a regular file")
        descriptor = os.open(parts[-1], FILE_FLAGS, dir_fd=current)
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            os.close(descriptor)
            raise SyncError("source_changed", "Source file changed while it was opened")
        return descriptor
    except OSError as exc:
        raise SyncError(
            "source_unavailable", "Source file cannot be opened safely"
        ) from exc
    finally:
        os.close(current)


@contextmanager
def capture_snapshot(
    root: Path,
    expected_root_identity: tuple[int, int],
    relative_path: str,
    staging_root: Path,
    max_bytes: int,
) -> Iterator[Snapshot]:
    root_descriptor = open_root(root, expected_root_identity)
    source_descriptor = -1
    temporary: Path | None = None
    try:
        source_descriptor = open_relative(root_descriptor, relative_path)
        before = os.fstat(source_descriptor)
        if before.st_size > max_bytes:
            raise SyncError(
                "source_too_large", "Source file exceeds its Connection budget"
            )
        staging_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, name = tempfile.mkstemp(prefix="capture-", dir=staging_root)
        temporary = Path(name)
        os.fchmod(descriptor, 0o600)
        digest = hashlib.sha256()
        copied = 0
        try:
            while True:
                chunk = os.read(source_descriptor, COPY_CHUNK)
                if not chunk:
                    break
                copied += len(chunk)
                if copied > max_bytes:
                    raise SyncError(
                        "source_too_large", "Source file exceeds its Connection budget"
                    )
                digest.update(chunk)
                offset = 0
                while offset < len(chunk):
                    offset += os.write(descriptor, chunk[offset:])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        after = os.fstat(source_descriptor)
        if copied != before.st_size or (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise SyncError(
                "source_changed", "Source file changed while it was captured"
            )
        yield Snapshot(
            path=temporary,
            byte_size=copied,
            sha256=digest.hexdigest(),
            modified_ns=after.st_mtime_ns,
            changed_ns=after.st_ctime_ns,
            device=after.st_dev,
            inode=after.st_ino,
        )
    finally:
        if source_descriptor >= 0:
            os.close(source_descriptor)
        os.close(root_descriptor)
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass
