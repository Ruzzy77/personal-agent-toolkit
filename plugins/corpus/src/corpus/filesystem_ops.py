"""Small, domain-neutral primitives for durable workspace file updates."""

from __future__ import annotations

import ctypes
import errno
import os
import stat
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class FileMetadataSnapshot:
    """Ownership, permissions, and ACL metadata preserved on replacement."""

    uid: int
    gid: int
    mode: int
    acl: bytes | None
    posix_acl_xattrs: tuple[tuple[str, bytes], ...]


class FileMetadataPreservationError(OSError):
    """A stable low-level failure raised before essential metadata loss."""

    def __init__(self, error: int, message: str, *, reason: str) -> None:
        super().__init__(error, message)
        self.reason = reason


_DARWIN_ACL_TYPE_EXTENDED = 0x00000100
_DARWIN_COPYFILE_ACL = 1 << 0
_NAMESPACE_BLOCKING_FLAGS = (
    0x00000002  # UF_IMMUTABLE
    | 0x00000004  # UF_APPEND
    | 0x00020000  # SF_IMMUTABLE
    | 0x00040000  # SF_APPEND
    | 0x00100000  # SF_NOUNLINK
)
_POSIX_ACL_XATTRS = {"system.posix_acl_access", "system.posix_acl_default"}


def _metadata_error(
    error: int, message: str, *, reason: str
) -> FileMetadataPreservationError:
    return FileMetadataPreservationError(error, message, reason=reason)


def _darwin_libc() -> ctypes.CDLL:
    return ctypes.CDLL(None, use_errno=True)


def _darwin_acl(descriptor: int) -> bytes | None:
    """Return the macOS extended ACL without interpreting policy in advance."""

    libc = _darwin_libc()
    acl_get_fd_np = libc.acl_get_fd_np
    acl_get_fd_np.argtypes = [ctypes.c_int, ctypes.c_int]
    acl_get_fd_np.restype = ctypes.c_void_p
    acl_free = libc.acl_free
    acl_free.argtypes = [ctypes.c_void_p]
    acl_free.restype = ctypes.c_int

    ctypes.set_errno(0)
    acl = acl_get_fd_np(descriptor, _DARWIN_ACL_TYPE_EXTENDED)
    if not acl:
        error = ctypes.get_errno()
        if error == errno.ENOENT:
            return None
        raise _metadata_error(
            error or errno.EIO,
            "file ACL could not be read",
            reason="acl_unreadable",
        )
    try:
        acl_size = libc.acl_size
        acl_size.argtypes = [ctypes.c_void_p]
        acl_size.restype = ctypes.c_ssize_t
        size = acl_size(acl)
        if size < 0:
            raise _metadata_error(
                ctypes.get_errno() or errno.EIO,
                "file ACL size could not be read",
                reason="acl_unreadable",
            )
        buffer = ctypes.create_string_buffer(size)
        acl_copy_ext = libc.acl_copy_ext
        acl_copy_ext.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ssize_t]
        acl_copy_ext.restype = ctypes.c_ssize_t
        copied = acl_copy_ext(buffer, acl, size)
        if copied != size:
            raise _metadata_error(
                ctypes.get_errno() or errno.EIO,
                "file ACL could not be serialized",
                reason="acl_unreadable",
            )
        return bytes(buffer.raw[:copied])
    finally:
        acl_free(acl)


def _posix_acl_xattrs(descriptor: int) -> tuple[tuple[str, bytes], ...]:
    if not sys.platform.startswith("linux"):
        return ()
    try:
        names = os.listxattr(descriptor)
    except OSError as exc:
        if exc.errno in {errno.ENOTSUP, errno.EOPNOTSUPP}:
            return ()
        raise _metadata_error(
            exc.errno or errno.EIO,
            "file ACL attributes could not be listed",
            reason="acl_unreadable",
        ) from exc
    values = []
    for name in sorted(set(names) & _POSIX_ACL_XATTRS):
        try:
            values.append((name, os.getxattr(descriptor, name)))
        except OSError as exc:
            raise _metadata_error(
                exc.errno or errno.EIO,
                "file ACL attribute could not be read",
                reason="acl_unreadable",
            ) from exc
    return tuple(values)


def snapshot_file_metadata(descriptor: int) -> FileMetadataSnapshot:
    """Read only metadata that replacement must preserve."""

    try:
        metadata = os.fstat(descriptor)
    except OSError as exc:
        raise _metadata_error(
            exc.errno or errno.EIO,
            "file metadata could not be read",
            reason="metadata_unreadable",
        ) from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise _metadata_error(
            errno.EINVAL,
            "metadata preservation requires a regular file",
            reason="not_regular_file",
        )
    return FileMetadataSnapshot(
        uid=int(metadata.st_uid),
        gid=int(metadata.st_gid),
        mode=stat.S_IMODE(metadata.st_mode),
        acl=_darwin_acl(descriptor) if sys.platform == "darwin" else None,
        posix_acl_xattrs=_posix_acl_xattrs(descriptor),
    )


def ensure_parent_directory_allows_replacement(parent_descriptor: int) -> None:
    """Reject actual namespace flags and leave ACL policy to the OS operation."""

    parent = os.fstat(parent_descriptor)
    if not stat.S_ISDIR(parent.st_mode):
        raise _metadata_error(
            errno.ENOTDIR,
            "metadata parent is not a directory",
            reason="unsafe_metadata_parent",
        )
    if int(getattr(parent, "st_flags", 0)) & _NAMESPACE_BLOCKING_FLAGS:
        raise _metadata_error(
            errno.EPERM,
            "parent directory flags prevent replacement",
            reason="parent_flags_block_replacement",
        )


def ensure_file_metadata_is_replaceable(
    source_descriptor: int,
    *,
    parent_descriptor: int,
) -> FileMetadataSnapshot:
    """Reject immutable namespace flags and capture essential metadata."""

    source = os.fstat(source_descriptor)
    if int(getattr(source, "st_flags", 0)) & _NAMESPACE_BLOCKING_FLAGS:
        raise _metadata_error(
            errno.EPERM,
            "immutable, append-only, or no-unlink files cannot be replaced",
            reason="immutable_append_or_nounlink_flags",
        )
    ensure_parent_directory_allows_replacement(parent_descriptor)
    return snapshot_file_metadata(source_descriptor)


def copy_file_metadata(
    source_descriptor: int,
    destination_descriptor: int,
    *,
    parent_descriptor: int,
) -> FileMetadataSnapshot:
    """Copy and verify ownership, permissions, and ACLs before exchange."""

    before = ensure_file_metadata_is_replaceable(
        source_descriptor,
        parent_descriptor=parent_descriptor,
    )
    destination = os.fstat(destination_descriptor)
    if not stat.S_ISREG(destination.st_mode) or destination.st_nlink != 1:
        raise _metadata_error(
            errno.EINVAL,
            "metadata destination is not a private regular file",
            reason="unsafe_metadata_destination",
        )
    try:
        os.fchown(destination_descriptor, before.uid, before.gid)
        os.fchmod(destination_descriptor, before.mode)
        if sys.platform == "darwin" and before.acl is not None:
            libc = _darwin_libc()
            fcopyfile = libc.fcopyfile
            fcopyfile.argtypes = [
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_void_p,
                ctypes.c_uint32,
            ]
            fcopyfile.restype = ctypes.c_int
            ctypes.set_errno(0)
            if (
                fcopyfile(
                    source_descriptor,
                    destination_descriptor,
                    None,
                    _DARWIN_COPYFILE_ACL,
                )
                != 0
            ):
                raise _metadata_error(
                    ctypes.get_errno() or errno.EIO,
                    "file ACL could not be copied",
                    reason="metadata_copy_failed",
                )
        target_acl_names = {name for name, _value in before.posix_acl_xattrs}
        if sys.platform.startswith("linux"):
            inherited_acl_names = (
                set(os.listxattr(destination_descriptor)) & _POSIX_ACL_XATTRS
            )
            for name in inherited_acl_names - target_acl_names:
                os.removexattr(destination_descriptor, name)
        for name, value in before.posix_acl_xattrs:
            os.setxattr(destination_descriptor, name, value)
        os.fsync(destination_descriptor)
    except FileMetadataPreservationError:
        raise
    except OSError as exc:
        raise _metadata_error(
            exc.errno or errno.EIO,
            "essential file metadata could not be copied",
            reason="metadata_copy_failed",
        ) from exc

    if snapshot_file_metadata(source_descriptor) != before:
        raise _metadata_error(
            errno.EBUSY,
            "essential source metadata changed while it was copied",
            reason="metadata_changed",
        )
    if snapshot_file_metadata(destination_descriptor) != before:
        raise _metadata_error(
            errno.ENOTSUP,
            "the filesystem did not preserve essential file metadata",
            reason="metadata_verification_failed",
        )
    return before


def write_all(descriptor: int, payload: bytes) -> None:
    """Write every byte or raise without treating a short write as success."""

    offset = 0
    while offset < len(payload):
        try:
            written = os.write(descriptor, payload[offset:])
        except InterruptedError:
            continue
        if written <= 0:
            raise OSError(errno.EIO, "write made no progress")
        offset += written


def atomic_exchange_at(parent_descriptor: int, first: str, second: str) -> None:
    """Atomically exchange two sibling entries.

    macOS and Linux expose this operation under different names.  Keeping the
    wrapper independent from source-sync and workspace errors lets both domains
    use the same race-safe primitive.  This function performs only the exchange;
    the caller must record that the namespace changed before separately syncing
    the parent directory.  Otherwise an ``fsync`` error would be indistinguishable
    from an exchange failure and a caller could run the wrong rollback path.
    """

    libc = ctypes.CDLL(None, use_errno=True)
    first_bytes = os.fsencode(first)
    second_bytes = os.fsencode(second)
    result: int
    if sys.platform == "darwin" and hasattr(libc, "renameatx_np"):
        function = libc.renameatx_np
        function.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        function.restype = ctypes.c_int
        result = function(
            parent_descriptor,
            first_bytes,
            parent_descriptor,
            second_bytes,
            0x00000002,  # RENAME_SWAP
        )
    elif sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        function = libc.renameat2
        function.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        function.restype = ctypes.c_int
        result = function(
            parent_descriptor,
            first_bytes,
            parent_descriptor,
            second_bytes,
            0x00000002,  # RENAME_EXCHANGE
        )
    else:
        raise OSError(errno.ENOTSUP, "atomic exchange is unavailable")
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def link_if_absent_at(parent_descriptor: int, source: str, target: str) -> None:
    """Publish a sibling temporary file only when the target is absent.

    The caller owns directory durability and must sync the parent after it has
    recorded that publication succeeded.
    """

    os.link(
        source,
        target,
        src_dir_fd=parent_descriptor,
        dst_dir_fd=parent_descriptor,
        follow_symlinks=False,
    )
