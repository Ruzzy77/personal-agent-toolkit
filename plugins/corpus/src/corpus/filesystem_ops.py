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
    """Metadata that must survive replacement of an existing workspace file."""

    uid: int
    gid: int
    mode: int
    flags: int
    xattrs: tuple[tuple[bytes, bytes], ...]
    acl: bytes | None


class FileMetadataPreservationError(OSError):
    """A stable low-level failure raised before unsafe metadata loss."""

    def __init__(self, error: int, message: str, *, reason: str) -> None:
        super().__init__(error, message)
        self.reason = reason


_DARWIN_ACL_TYPE_EXTENDED = 0x00000100
_DARWIN_ACL_FIRST_ENTRY = 0
_DARWIN_ACL_NEXT_ENTRY = -1
_DARWIN_ACL_EXTENDED_ALLOW = 1
_DARWIN_ACL_EXTENDED_DENY = 2
_DARWIN_ACL_DELETE = 1 << 4
_DARWIN_ACL_DELETE_CHILD = 1 << 6
_DARWIN_ACL_ENTRY_FILE_INHERIT = 1 << 5
_DARWIN_ACL_ENTRY_ONLY_INHERIT = 1 << 8
_DARWIN_COPYFILE_ACL = 1 << 0
_DARWIN_COPYFILE_XATTR = 1 << 2
_DARWIN_NAMESPACE_BLOCKING_FLAGS = (
    0x00000002  # UF_IMMUTABLE
    | 0x00000004  # UF_APPEND
    | 0x00020000  # SF_IMMUTABLE
    | 0x00040000  # SF_APPEND
    | 0x00100000  # SF_NOUNLINK
)


def _metadata_error(error: int, message: str, *, reason: str) -> FileMetadataPreservationError:
    return FileMetadataPreservationError(error, message, reason=reason)


def _darwin_libc() -> ctypes.CDLL:
    if sys.platform != "darwin":
        raise _metadata_error(
            errno.ENOTSUP,
            "complete file metadata preservation is unavailable",
            reason="unsupported_platform",
        )
    return ctypes.CDLL(None, use_errno=True)


def _darwin_acl(
    descriptor: int,
) -> tuple[bytes | None, bool, bool, bool]:
    """Return an external ACL snapshot and namespace-denial indicators."""

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
            return (None, False, False, False)
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
            error = ctypes.get_errno()
            raise _metadata_error(
                error or errno.EIO,
                "file ACL size could not be read",
                reason="acl_unreadable",
            )
        buffer = ctypes.create_string_buffer(size)
        acl_copy_ext = libc.acl_copy_ext
        acl_copy_ext.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ssize_t]
        acl_copy_ext.restype = ctypes.c_ssize_t
        copied = acl_copy_ext(buffer, acl, size)
        if copied != size:
            error = ctypes.get_errno()
            raise _metadata_error(
                error or errno.EIO,
                "file ACL could not be serialized completely",
                reason="acl_unreadable",
            )

        acl_get_entry = libc.acl_get_entry
        acl_get_entry.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)]
        acl_get_entry.restype = ctypes.c_int
        acl_get_tag_type = libc.acl_get_tag_type
        acl_get_tag_type.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
        acl_get_tag_type.restype = ctypes.c_int
        acl_get_permset = libc.acl_get_permset
        acl_get_permset.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        acl_get_permset.restype = ctypes.c_int
        acl_get_perm_np = libc.acl_get_perm_np
        acl_get_perm_np.argtypes = [ctypes.c_void_p, ctypes.c_int]
        acl_get_perm_np.restype = ctypes.c_int
        acl_get_flagset_np = libc.acl_get_flagset_np
        acl_get_flagset_np.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        acl_get_flagset_np.restype = ctypes.c_int
        acl_get_flag_np = libc.acl_get_flag_np
        acl_get_flag_np.argtypes = [ctypes.c_void_p, ctypes.c_int]
        acl_get_flag_np.restype = ctypes.c_int

        denies_delete = False
        denies_delete_child = False
        inherits_delete = False
        entry_id = _DARWIN_ACL_FIRST_ENTRY
        while True:
            entry = ctypes.c_void_p()
            ctypes.set_errno(0)
            result = acl_get_entry(acl, entry_id, ctypes.byref(entry))
            if result != 0:
                error = ctypes.get_errno()
                if error == errno.EINVAL:
                    break
                raise _metadata_error(
                    error or errno.EIO,
                    "file ACL entries could not be read",
                    reason="acl_unreadable",
                )
            tag = ctypes.c_int()
            if acl_get_tag_type(entry, ctypes.byref(tag)) != 0:
                error = ctypes.get_errno()
                raise _metadata_error(
                    error or errno.EIO,
                    "file ACL tag could not be read",
                    reason="acl_unreadable",
                )
            if tag.value not in {
                _DARWIN_ACL_EXTENDED_ALLOW,
                _DARWIN_ACL_EXTENDED_DENY,
            }:
                raise _metadata_error(
                    errno.EINVAL,
                    "file ACL contains an unsupported entry type",
                    reason="acl_unreadable",
                )
            if tag.value == _DARWIN_ACL_EXTENDED_DENY:
                permissions = ctypes.c_void_p()
                if acl_get_permset(entry, ctypes.byref(permissions)) != 0:
                    error = ctypes.get_errno()
                    raise _metadata_error(
                        error or errno.EIO,
                        "file ACL permissions could not be read",
                        reason="acl_unreadable",
                    )
                delete = acl_get_perm_np(permissions, _DARWIN_ACL_DELETE)
                delete_child = acl_get_perm_np(permissions, _DARWIN_ACL_DELETE_CHILD)
                flags = ctypes.c_void_p()
                if acl_get_flagset_np(entry, ctypes.byref(flags)) != 0:
                    error = ctypes.get_errno()
                    raise _metadata_error(
                        error or errno.EIO,
                        "file ACL flags could not be read",
                        reason="acl_unreadable",
                    )
                only_inherit = acl_get_flag_np(flags, _DARWIN_ACL_ENTRY_ONLY_INHERIT)
                file_inherit = acl_get_flag_np(flags, _DARWIN_ACL_ENTRY_FILE_INHERIT)
                if min(delete, delete_child, only_inherit, file_inherit) < 0:
                    error = ctypes.get_errno()
                    raise _metadata_error(
                        error or errno.EIO,
                        "file ACL permissions or flags could not be evaluated",
                        reason="acl_unreadable",
                    )
                denies_delete = denies_delete or (delete == 1 and only_inherit == 0)
                denies_delete_child = denies_delete_child or (
                    delete_child == 1 and only_inherit == 0
                )
                inherits_delete = inherits_delete or (delete == 1 and file_inherit == 1)
            entry_id = _DARWIN_ACL_NEXT_ENTRY

        return (
            bytes(buffer.raw[:copied]),
            denies_delete,
            denies_delete_child,
            inherits_delete,
        )
    finally:
        acl_free(acl)


def _darwin_xattrs(descriptor: int) -> tuple[tuple[bytes, bytes], ...]:
    libc = _darwin_libc()
    flistxattr = libc.flistxattr
    flistxattr.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]
    flistxattr.restype = ctypes.c_ssize_t
    fgetxattr = libc.fgetxattr
    fgetxattr.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_uint32,
        ctypes.c_int,
    ]
    fgetxattr.restype = ctypes.c_ssize_t

    ctypes.set_errno(0)
    size = flistxattr(descriptor, None, 0, 0)
    if size < 0:
        error = ctypes.get_errno()
        raise _metadata_error(
            error or errno.EIO,
            "file extended attributes could not be listed",
            reason="xattr_unreadable",
        )
    if size == 0:
        return ()
    names_buffer = ctypes.create_string_buffer(size)
    names_size = flistxattr(descriptor, names_buffer, size, 0)
    if names_size < 0:
        error = ctypes.get_errno()
        reason = "metadata_changed" if error == errno.ERANGE else "xattr_unreadable"
        raise _metadata_error(
            error or errno.EIO,
            "file extended attributes changed while being listed",
            reason=reason,
        )
    names_raw = bytes(names_buffer.raw[:names_size])
    if not names_raw.endswith(b"\0"):
        raise _metadata_error(
            errno.EIO,
            "file extended attribute list is malformed",
            reason="xattr_unreadable",
        )
    names = sorted(name for name in names_raw[:-1].split(b"\0") if name)
    result: list[tuple[bytes, bytes]] = []
    for name in names:
        ctypes.set_errno(0)
        value_size = fgetxattr(descriptor, name, None, 0, 0, 0)
        if value_size < 0:
            error = ctypes.get_errno()
            raise _metadata_error(
                error or errno.EIO,
                "file extended attribute could not be read",
                reason="metadata_changed"
                if error in {errno.ENOENT, errno.ERANGE}
                else "xattr_unreadable",
            )
        if value_size == 0:
            value = b""
        else:
            value_buffer = ctypes.create_string_buffer(value_size)
            copied = fgetxattr(descriptor, name, value_buffer, value_size, 0, 0)
            if copied != value_size:
                error = ctypes.get_errno()
                raise _metadata_error(
                    error or errno.EIO,
                    "file extended attribute changed while being read",
                    reason="metadata_changed"
                    if error in {errno.ENOENT, errno.ERANGE}
                    else "xattr_unreadable",
                )
            value = bytes(value_buffer.raw[:copied])
        result.append((name, value))
    return tuple(result)


def snapshot_file_metadata(descriptor: int) -> FileMetadataSnapshot:
    """Read all macOS metadata that an existing-file replacement must retain."""

    if sys.platform != "darwin":
        raise _metadata_error(
            errno.ENOTSUP,
            "complete file metadata preservation is unavailable",
            reason="unsupported_platform",
        )
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
    acl, _denies_delete, _denies_delete_child, _inherits_delete = _darwin_acl(descriptor)
    return FileMetadataSnapshot(
        uid=int(metadata.st_uid),
        gid=int(metadata.st_gid),
        mode=stat.S_IMODE(metadata.st_mode),
        flags=int(metadata.st_flags),
        xattrs=_darwin_xattrs(descriptor),
        acl=acl,
    )


def ensure_parent_directory_allows_replacement(parent_descriptor: int) -> None:
    """Reject a directory whose flags or ACL can strand an exchange temporary."""

    _darwin_libc()
    parent = os.fstat(parent_descriptor)
    if not stat.S_ISDIR(parent.st_mode):
        raise _metadata_error(
            errno.ENOTDIR,
            "metadata parent is not a directory",
            reason="unsafe_metadata_parent",
        )
    if int(parent.st_flags) & _DARWIN_NAMESPACE_BLOCKING_FLAGS:
        raise _metadata_error(
            errno.EPERM,
            "parent directory flags prevent replacement",
            reason="parent_flags_block_replacement",
        )
    _acl, _denies_delete, denies_delete_child, inherits_delete = _darwin_acl(parent_descriptor)
    if denies_delete_child or inherits_delete:
        raise _metadata_error(
            errno.EPERM,
            "parent directory ACL prevents a safe replacement temporary",
            reason="parent_acl_blocks_replacement",
        )


def ensure_file_metadata_is_replaceable(
    source_descriptor: int,
    *,
    parent_descriptor: int,
) -> FileMetadataSnapshot:
    """Reject metadata that can prevent a safe exchange or rollback."""

    _darwin_libc()
    source = os.fstat(source_descriptor)
    if int(source.st_flags) & _DARWIN_NAMESPACE_BLOCKING_FLAGS:
        raise _metadata_error(
            errno.EPERM,
            "immutable, append-only, or no-unlink files cannot be replaced",
            reason="immutable_append_or_nounlink_flags",
        )
    acl, denies_delete, _denies_delete_child, _inherits_delete = _darwin_acl(source_descriptor)
    if denies_delete:
        raise _metadata_error(
            errno.EPERM,
            "file ACL denies the namespace change required for replacement",
            reason="acl_denies_delete",
        )

    ensure_parent_directory_allows_replacement(parent_descriptor)

    return FileMetadataSnapshot(
        uid=int(source.st_uid),
        gid=int(source.st_gid),
        mode=stat.S_IMODE(source.st_mode),
        flags=int(source.st_flags),
        xattrs=_darwin_xattrs(source_descriptor),
        acl=acl,
    )


def copy_file_metadata(
    source_descriptor: int,
    destination_descriptor: int,
    *,
    parent_descriptor: int,
) -> FileMetadataSnapshot:
    """Copy and verify complete existing-file metadata before an exchange.

    Content and timestamps are deliberately excluded: replacement supplies new
    content and therefore a new modification time.  ACLs, every extended
    attribute, ownership, permission bits, and file flags must match exactly.
    Any unsupported or racing metadata fails before the namespace is changed.
    """

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

    libc = _darwin_libc()
    fcopyfile = libc.fcopyfile
    fcopyfile.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    fcopyfile.restype = ctypes.c_int
    fchflags = libc.fchflags
    fchflags.argtypes = [ctypes.c_int, ctypes.c_uint]
    fchflags.restype = ctypes.c_int
    try:
        os.fchown(destination_descriptor, before.uid, before.gid)
        os.fchmod(destination_descriptor, before.mode)
        for copy_flags in (_DARWIN_COPYFILE_XATTR, _DARWIN_COPYFILE_ACL):
            ctypes.set_errno(0)
            if fcopyfile(source_descriptor, destination_descriptor, None, copy_flags) != 0:
                error = ctypes.get_errno()
                raise _metadata_error(
                    error or errno.EIO,
                    "file metadata could not be copied completely",
                    reason="metadata_copy_failed",
                )
        ctypes.set_errno(0)
        if fchflags(destination_descriptor, before.flags) != 0:
            error = ctypes.get_errno()
            raise _metadata_error(
                error or errno.EIO,
                "file flags could not be preserved",
                reason="metadata_copy_failed",
            )
        os.fsync(destination_descriptor)
    except FileMetadataPreservationError:
        raise
    except OSError as exc:
        raise _metadata_error(
            exc.errno or errno.EIO,
            "file metadata could not be copied completely",
            reason="metadata_copy_failed",
        ) from exc

    after_source = snapshot_file_metadata(source_descriptor)
    if after_source != before:
        raise _metadata_error(
            errno.EBUSY,
            "source metadata changed while it was being copied",
            reason="metadata_changed",
        )
    after_destination = snapshot_file_metadata(destination_descriptor)
    if after_destination != before:
        raise _metadata_error(
            errno.ENOTSUP,
            "the filesystem did not preserve all file metadata",
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
