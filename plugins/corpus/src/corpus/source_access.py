"""Descriptor-pinned traversal for registered source trees."""

from __future__ import annotations

import os
import stat
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .errors import SourceBoundaryError, SourceChangedError

_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
_SOURCE_FLAGS = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)


def _source_error(
    message: str, *, relative_path: str, reason: str
) -> SourceBoundaryError:
    return SourceBoundaryError(
        message,
        details={"relative_path": relative_path, "reason": reason},
    )


def _require_component(component: str, *, relative_path: str) -> None:
    if (
        not component
        or component in {".", ".."}
        or "/" in component
        or "\x00" in component
    ):
        raise _source_error(
            "source path contains an unsafe component",
            relative_path=relative_path,
            reason="invalid_component",
        )


def relative_source_parts(source: Path, source_root: Path) -> tuple[str, ...]:
    """Return a lexical relative path without resolving attacker-controlled parents."""

    if not source.is_absolute() or not source_root.is_absolute():
        raise _source_error(
            "source and source-root must be absolute",
            relative_path=str(source),
            reason="not_absolute",
        )
    try:
        relative = source.relative_to(source_root)
    except ValueError as exc:
        raise _source_error(
            "source path escapes the registered corpus root",
            relative_path=str(source),
            reason="outside_root",
        ) from exc
    parts = relative.parts
    relative_path = relative.as_posix()
    if not parts:
        raise _source_error(
            "source must name a file below the registered corpus root",
            relative_path=relative_path,
            reason="root_is_not_file",
        )
    for component in parts:
        _require_component(component, relative_path=relative_path)
    return parts


def open_source_root(source_root: Path) -> int:
    """Open every absolute source-root component without following symlinks."""

    if not source_root.is_absolute():
        raise _source_error(
            "source-root must be absolute",
            relative_path=str(source_root),
            reason="not_absolute",
        )
    parts = source_root.parts
    if len(parts) < 2:
        raise _source_error(
            "filesystem root cannot be registered as a corpus",
            relative_path=str(source_root),
            reason="root_not_allowed",
        )
    descriptor = os.open(parts[0], _DIRECTORY_FLAGS)
    try:
        for component in parts[1:]:
            _require_component(component, relative_path=str(source_root))
            next_descriptor = os.open(
                component,
                _DIRECTORY_FLAGS,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = next_descriptor
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            raise _source_error(
                "source-root is not a directory",
                relative_path=str(source_root),
                reason="not_directory",
            )
        return descriptor
    except OSError as exc:
        os.close(descriptor)
        raise _source_error(
            "source-root could not be opened without following symlinks",
            relative_path=str(source_root),
            reason=f"open_failed:{exc.errno}",
        ) from exc
    except Exception:
        os.close(descriptor)
        raise


def source_root_identity(descriptor: int) -> tuple[int, int]:
    """Return the stable filesystem identity of an opened source root."""

    metadata = os.fstat(descriptor)
    if not stat.S_ISDIR(metadata.st_mode):
        raise _source_error(
            "opened source-root is not a directory",
            relative_path=".",
            reason="not_directory",
        )
    return (metadata.st_dev, metadata.st_ino)


def resolve_source_root_identity_path(device: int, inode: int) -> Path | None:
    """Resolve a moved directory by filesystem identity on macOS.

    APFS exposes stable file identities through ``/.vol``. F_GETPATH then
    returns the current user-visible path after a Finder rename or move on the
    same volume. Other platforms deliberately fall back to explicit rebind.
    """

    if sys.platform != "darwin" or device < 0 or inode < 0:
        return None
    try:
        import fcntl

        descriptor = os.open(
            f"/.vol/{device}/{inode}",
            _DIRECTORY_FLAGS,
        )
    except (ImportError, OSError):
        return None
    try:
        if source_root_identity(descriptor) != (device, inode):
            return None
        try:
            encoded = fcntl.fcntl(descriptor, 50, b"\0" * 1024)
            current_path = encoded.split(b"\0", 1)[0].decode("utf-8")
        except (OSError, UnicodeError, ValueError):
            return None
        if not current_path or not current_path.startswith("/"):
            return None
        return Path(current_path)
    finally:
        os.close(descriptor)


@contextmanager
def opened_source_root(source_root: Path) -> Iterator[int]:
    descriptor = open_source_root(source_root)
    try:
        yield descriptor
    finally:
        os.close(descriptor)


@contextmanager
def opened_current_source_root(
    source_root: Path,
    expected_identity: tuple[int, int],
) -> Iterator[int]:
    """Reopen the registered root path and require its original identity."""

    with opened_source_root(source_root) as descriptor:
        current_identity = source_root_identity(descriptor)
        if current_identity != expected_identity:
            raise SourceChangedError(
                "registered source-root changed while it was being observed",
                details={
                    "source_root": str(source_root),
                    "expected": expected_identity,
                    "current": current_identity,
                },
            )
        yield descriptor


def open_directory_at(
    parent_descriptor: int,
    name: str,
    *,
    relative_path: str,
    expected: os.stat_result | None = None,
) -> int:
    """Open one child directory and optionally bind it to a prior fstatat result."""

    _require_component(name, relative_path=relative_path)
    try:
        descriptor = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_descriptor)
    except OSError as exc:
        raise _source_error(
            "source directory changed while it was being scanned",
            relative_path=relative_path,
            reason=f"open_failed:{exc.errno}",
        ) from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISDIR(opened.st_mode):
            raise _source_error(
                "source directory is not a directory",
                relative_path=relative_path,
                reason="not_directory",
            )
        if expected is not None and (
            opened.st_dev != expected.st_dev or opened.st_ino != expected.st_ino
        ):
            raise SourceChangedError(
                "source directory changed while it was being opened",
                details={"relative_path": relative_path},
            )
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _open_regular_at(parent_descriptor: int, name: str) -> int:
    return os.open(name, _SOURCE_FLAGS, dir_fd=parent_descriptor)


def open_source_file(
    source_root_descriptor: int,
    relative_parts: tuple[str, ...],
) -> int:
    """Open one regular source beneath a pinned root without path re-resolution."""

    relative_path = "/".join(relative_parts)
    if not relative_parts:
        raise _source_error(
            "source must name a file below source-root",
            relative_path=relative_path,
            reason="root_is_not_file",
        )
    current = os.dup(source_root_descriptor)
    try:
        for index, component in enumerate(relative_parts[:-1], start=1):
            current_path = "/".join(relative_parts[:index])
            next_descriptor = open_directory_at(
                current,
                component,
                relative_path=current_path,
            )
            os.close(current)
            current = next_descriptor

        name = relative_parts[-1]
        _require_component(name, relative_path=relative_path)
        try:
            before = os.stat(name, dir_fd=current, follow_symlinks=False)
        except OSError as exc:
            raise _source_error(
                "source file could not be inspected",
                relative_path=relative_path,
                reason=f"stat_failed:{exc.errno}",
            ) from exc
        if not stat.S_ISREG(before.st_mode):
            raise _source_error(
                "source is not a regular file",
                relative_path=relative_path,
                reason="not_regular_file",
            )
        try:
            descriptor = _open_regular_at(current, name)
        except OSError as exc:
            raise _source_error(
                "source file could not be opened without following symlinks",
                relative_path=relative_path,
                reason=f"open_failed:{exc.errno}",
            ) from exc
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode):
                raise _source_error(
                    "opened source is not a regular file",
                    relative_path=relative_path,
                    reason="not_regular_file",
                )
            if opened.st_dev != before.st_dev or opened.st_ino != before.st_ino:
                raise SourceChangedError(
                    "source file changed while it was being opened",
                    details={"relative_path": relative_path},
                )
            return descriptor
        except Exception:
            os.close(descriptor)
            raise
    finally:
        os.close(current)


@contextmanager
def opened_source_file(
    source_root_descriptor: int,
    relative_parts: tuple[str, ...],
) -> Iterator[int]:
    descriptor = open_source_file(source_root_descriptor, relative_parts)
    try:
        yield descriptor
    finally:
        os.close(descriptor)


@contextmanager
def opened_current_source_file(
    source_root: Path,
    expected_root_identity: tuple[int, int],
    relative_parts: tuple[str, ...],
) -> Iterator[int]:
    """Reopen the registered root and relative file as one verified chain."""

    with (
        opened_current_source_root(
            source_root,
            expected_root_identity,
        ) as source_root_descriptor,
        opened_source_file(source_root_descriptor, relative_parts) as descriptor,
    ):
        yield descriptor
