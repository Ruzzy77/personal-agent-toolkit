"""Descriptor-pinned local traversal and Finder move recovery."""

from __future__ import annotations

import asyncio
import ctypes
import errno
import hashlib
import os
import stat
import sys
import tempfile
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path

from .errors import SyncError
from .materialization import SF_DATALESS, NativeCapture

DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
FILE_FLAGS = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
COPY_CHUNK = 1024 * 1024
DARWIN_PATH_BUFFER = 1024
ABANDONED_CAPTURE_MIN_AGE_SECONDS = 24 * 60 * 60
ABANDONED_CAPTURE_CLEANUP_LIMIT = 100


def volume_uuid(descriptor: int) -> str | None:
    """Read a mounted filesystem UUID from an already pinned directory, not contents."""

    if sys.platform != "darwin":
        return None

    class AttrList(ctypes.Structure):
        _fields_ = [
            ("bitmapcount", ctypes.c_uint16),
            ("reserved", ctypes.c_uint16),
            ("commonattr", ctypes.c_uint32),
            ("volattr", ctypes.c_uint32),
            ("dirattr", ctypes.c_uint32),
            ("fileattr", ctypes.c_uint32),
            ("forkattr", ctypes.c_uint32),
        ]

    try:
        read_attributes = ctypes.CDLL(None, use_errno=True).fgetattrlist
        read_attributes.argtypes = [
            ctypes.c_int,
            ctypes.POINTER(AttrList),
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_ulong,
        ]
        read_attributes.restype = ctypes.c_int
        # Darwin sys/attr.h: ATTR_VOL_INFO | ATTR_VOL_UUID. The result is a
        # uint32 byte count followed by uuid_t; no file bytes are requested.
        attributes = AttrList(5, 0, 0, 0x80040000, 0, 0, 0)
        output = ctypes.create_string_buffer(20)
        if read_attributes(descriptor, ctypes.byref(attributes), output, 20, 0):
            return None
        if int.from_bytes(output.raw[:4], sys.byteorder) != 20:
            return None
        value = uuid.UUID(bytes=output.raw[4:20])
        return str(value) if value.int else None
    except (AttributeError, OSError, ValueError):
        return None


@dataclass(frozen=True)
class Snapshot:
    path: Path
    byte_size: int
    sha256: str
    modified_ns: int
    changed_ns: int
    device: int
    inode: int


def cleanup_abandoned_captures(
    staging_root: Path,
    *,
    now_ns: int | None = None,
    min_age_seconds: int = ABANDONED_CAPTURE_MIN_AGE_SECONDS,
    limit: int = ABANDONED_CAPTURE_CLEANUP_LIMIT,
) -> dict[str, int]:
    """Remove a bounded set of old capture files left by interrupted runs."""

    if min_age_seconds < 0 or not 1 <= limit <= ABANDONED_CAPTURE_CLEANUP_LIMIT:
        raise SyncError(
            "invalid_staging_cleanup",
            "staging cleanup limits are invalid",
        )
    try:
        root_metadata = staging_root.lstat()
    except FileNotFoundError:
        return {"removed": 0, "retained": 0, "skipped": 0}
    except OSError as exc:
        raise SyncError(
            "staging_unavailable", "local capture staging is unavailable"
        ) from exc
    if not stat.S_ISDIR(root_metadata.st_mode) or stat.S_ISLNK(root_metadata.st_mode):
        raise SyncError(
            "unsafe_staging", "local capture staging must be a real directory"
        )
    cutoff_ns = (now_ns if now_ns is not None else time.time_ns()) - (
        min_age_seconds * 1_000_000_000
    )
    removed = 0
    retained = 0
    skipped = 0
    try:
        entries = sorted(staging_root.iterdir(), key=lambda path: path.name)
    except OSError as exc:
        raise SyncError(
            "staging_unavailable", "local capture staging could not be listed"
        ) from exc
    for entry in entries:
        if not entry.name.startswith("capture-"):
            skipped += 1
            continue
        try:
            metadata = entry.lstat()
        except OSError:
            skipped += 1
            continue
        if not stat.S_ISREG(metadata.st_mode):
            skipped += 1
            continue
        if metadata.st_mtime_ns > cutoff_ns or removed >= limit:
            retained += 1
            continue
        try:
            entry.unlink()
        except OSError:
            retained += 1
        else:
            removed += 1
    return {"removed": removed, "retained": retained, "skipped": skipped}


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

        # F_GETPATH writes one MAXPATHLEN buffer. Python rejects larger mutable
        # fcntl buffers before the syscall, which previously prevented recovery
        # after a Connection root moved while Sync was stopped.
        encoded = fcntl.fcntl(descriptor, 50, b"\0" * DARWIN_PATH_BUFFER)
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
    *,
    expected_file_identity: tuple[int, int],
    native_capture: NativeCapture | None = None,
) -> Iterator[Snapshot]:
    root_descriptor = open_root(root, expected_root_identity)
    source_descriptor = -1
    temporary: Path | None = None
    try:
        source_descriptor = open_relative(root_descriptor, relative_path)
        before = os.fstat(source_descriptor)
        if (before.st_dev, before.st_ino) != expected_file_identity:
            raise SyncError(
                "source_changed", "Source file identity changed after reconciliation"
            )
        if before.st_size > max_bytes:
            raise SyncError(
                "source_too_large", "Source file exceeds its Connection budget"
            )
        staging_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        dataless = bool(getattr(before, "st_flags", 0) & SF_DATALESS)
        native_result = None
        if dataless:
            if native_capture is None:
                raise SyncError(
                    "source_materializer_unavailable",
                    "Online-only Source capture requires a File Provider helper",
                )
            temporary = staging_root / f"capture-{uuid.uuid4().hex}"
            native_result = native_capture.copy(
                source_descriptor,
                root,
                root.joinpath(*safe_parts(relative_path)),
                temporary,
                max_bytes,
            )
            descriptor = os.open(temporary, FILE_FLAGS)
            captured = os.fstat(descriptor)
            if (
                not stat.S_ISREG(captured.st_mode)
                or captured.st_size != before.st_size
                or captured.st_uid != os.getuid()
                or stat.S_IMODE(captured.st_mode) != 0o600
            ):
                os.close(descriptor)
                raise SyncError("source_capture_failed", "Native capture is invalid")
        else:
            descriptor, name = tempfile.mkstemp(prefix="capture-", dir=staging_root)
            temporary = Path(name)
            os.fchmod(descriptor, 0o600)
        digest = hashlib.sha256()
        copied = 0
        try:
            while True:
                try:
                    chunk = os.read(
                        descriptor if dataless else source_descriptor, COPY_CHUNK
                    )
                except OSError as exc:
                    code = (
                        "source_download_pending"
                        if exc.errno in {errno.EDEADLK, errno.EAGAIN, errno.ETIMEDOUT}
                        else "source_unavailable"
                    )
                    raise SyncError(
                        code, "Source bytes are not currently readable"
                    ) from exc
                if not chunk:
                    break
                copied += len(chunk)
                if copied > max_bytes:
                    raise SyncError(
                        "source_too_large", "Source file exceeds its Connection budget"
                    )
                digest.update(chunk)
                if not dataless:
                    offset = 0
                    while offset < len(chunk):
                        written = os.write(descriptor, chunk[offset:])
                        if written <= 0:
                            raise SyncError(
                                "staging_unavailable", "Capture write made no progress"
                            )
                        offset += written
            if not dataless:
                os.fsync(descriptor)
        finally:
            os.close(descriptor)
        after = os.fstat(source_descriptor)
        native_after = native_result.get("sourceAfter", {}) if native_result else {}
        materialized = (
            dataless
            and not (getattr(after, "st_flags", 0) & SF_DATALESS)
            and native_result is not None
            and native_result.get("stable") is True
            and native_result.get("identityStable") is True
            and native_result.get("exactByteCount") is True
            and native_result.get("bytesCopied") == copied
            and all(
                native_after.get(key) == value
                for key, value in {
                    "device": str(after.st_dev),
                    "inode": str(after.st_ino),
                    "logicalSize": after.st_size,
                    "modificationTimeNanoseconds": after.st_mtime_ns,
                    "changeTimeNanoseconds": after.st_ctime_ns,
                }.items()
            )
            and (
                native_result.get("versionStable") is True
                or native_result.get("hydrationStateChanged") is True
            )
        )
        if (
            copied != before.st_size
            or (dataless and not materialized)
            or (
                before.st_dev,
                before.st_ino,
                before.st_size,
            )
            != (
                after.st_dev,
                after.st_ino,
                after.st_size,
            )
            or (
                not materialized
                and (before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_mtime_ns, after.st_ctime_ns)
            )
        ):
            raise SyncError(
                "source_changed", "Source file changed while it was captured"
            )
        # A download can take time. Verify the path still names this pinned
        # version, not merely an old descriptor after a rename/replacement.
        current_root = open_root(root, expected_root_identity)
        try:
            current_file = open_relative(current_root, relative_path)
            try:
                current = os.fstat(current_file)
                if (
                    current.st_dev,
                    current.st_ino,
                    current.st_size,
                    current.st_mtime_ns,
                    current.st_ctime_ns,
                ) != (
                    after.st_dev,
                    after.st_ino,
                    after.st_size,
                    after.st_mtime_ns,
                    after.st_ctime_ns,
                ):
                    raise SyncError(
                        "source_changed", "Source path changed during capture"
                    )
            finally:
                os.close(current_file)
        finally:
            os.close(current_root)
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


@asynccontextmanager
async def capture_snapshot_async(*args, **kwargs) -> AsyncIterator[Snapshot]:
    """Keep provider waits off the broker loop and clean up even on cancellation."""
    manager = capture_snapshot(*args, **kwargs)
    pending = asyncio.create_task(asyncio.to_thread(manager.__enter__))
    try:
        snapshot = await asyncio.shield(pending)
    except asyncio.CancelledError:
        # Cancelling to_thread does not stop its worker. Wait for the bounded
        # capture before closing its descriptors and deleting its private copy.
        with suppress(Exception):
            await pending
            manager.__exit__(None, None, None)
        raise
    try:
        yield snapshot
    finally:
        manager.__exit__(None, None, None)
