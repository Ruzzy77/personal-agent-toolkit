"""Safe, temporary capture of source bytes for extraction."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import time
import uuid
from contextlib import ExitStack, suppress
from dataclasses import dataclass
from pathlib import Path

from .config import (
    RuntimePaths,
    ensure_private_directory_at,
    is_within,
    open_private_file_at,
)
from .errors import (
    BudgetExceededError,
    ConfigurationError,
    HydrationRequiredError,
    HydrationUnavailableError,
    SourceBoundaryError,
    SourceChangedError,
)
from .scanner import SF_DATALESS
from .source_access import (
    opened_current_source_file,
    opened_source_file,
    opened_source_root,
    relative_source_parts,
    source_root_identity,
)

COPY_CHUNK_BYTES = 1024 * 1024
CAPTURE_NAME_RE = re.compile(r"^capture-[0-9a-f]{32}\.part$")
BLOB_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
BLOB_DIRECTORY_RE = re.compile(r"^[0-9a-f]{2}$")
BLOB_NAME_RE = re.compile(r"^([0-9a-f]{64})\.blob$")


@dataclass(frozen=True)
class SourceIdentity:
    size: int
    modified_ns: int
    changed_ns: int
    device: int
    inode: int
    mode: int
    flags: int
    allocated_size: int = 0

    @property
    def dataless(self) -> bool:
        return bool(self.flags & SF_DATALESS)

    def stable_key(self) -> tuple[int, int, int, int, int]:
        return (
            self.size,
            self.modified_ns,
            self.changed_ns,
            self.device,
            self.inode,
        )


@dataclass(frozen=True)
class CapturedSource:
    sha256: str
    capture_path: Path
    bytes_copied: int
    pre_identity: SourceIdentity
    post_identity: SourceIdentity
    used_native_helper: bool
    hydration_was_required: bool


@dataclass(frozen=True)
class SourceCopyCleanup:
    persistent_blob_files: int
    persistent_blob_bytes: int
    staged_capture_files: int
    staged_capture_bytes: int
    unexpected_entries: int
    canonical_blob_digests: tuple[str, ...]

    def as_dict(self, *, deleted: bool) -> dict:
        return {
            "mode": "deleted" if deleted else "plan",
            "persistent_blob_files": self.persistent_blob_files,
            "persistent_blob_bytes": self.persistent_blob_bytes,
            "staged_capture_files": self.staged_capture_files,
            "staged_capture_bytes": self.staged_capture_bytes,
            "total_source_copy_files": (
                self.persistent_blob_files + self.staged_capture_files
            ),
            "total_source_copy_bytes": (
                self.persistent_blob_bytes + self.staged_capture_bytes
            ),
            "unexpected_entries_skipped": self.unexpected_entries,
        }


def capture_identity_is_stable(
    *,
    before: SourceIdentity,
    after: SourceIdentity,
    hydration_required: bool,
    used_native_helper: bool,
    native_result: dict,
) -> bool:
    if hydration_required and used_native_helper:
        provider_hydration_completed = bool(
            before.dataless
            and not after.dataless
            and native_result.get("hydrationStateChanged") is True
        )
        return bool(
            before.size == after.size
            and before.device == after.device
            and before.inode == after.inode
            and native_result.get("stable") is True
            and native_result.get("identityStable") is True
            and (
                native_result.get("versionStable") is True
                or provider_hydration_completed
            )
            and native_result.get("exactByteCount") is True
        )
    return before.stable_key() == after.stable_key()


def source_identity_from_stat(metadata: os.stat_result) -> SourceIdentity:
    if not stat.S_ISREG(metadata.st_mode):
        raise SourceBoundaryError(
            "source is not a regular file",
            details={"mode": metadata.st_mode},
        )
    return SourceIdentity(
        size=metadata.st_size,
        modified_ns=metadata.st_mtime_ns,
        changed_ns=metadata.st_ctime_ns,
        device=metadata.st_dev,
        inode=metadata.st_ino,
        mode=metadata.st_mode,
        flags=int(getattr(metadata, "st_flags", 0)),
        allocated_size=int(getattr(metadata, "st_blocks", 0) * 512),
    )


def current_source_path_identity(
    source_root: Path,
    expected_root_identity: tuple[int, int],
    relative_parts: tuple[str, ...],
) -> SourceIdentity:
    """Securely reopen the registered root and relative path as one chain."""

    with opened_current_source_file(
        source_root,
        expected_root_identity,
        relative_parts,
    ) as descriptor:
        return source_identity_from_stat(os.fstat(descriptor))


def _require_current_source_path_identity(
    *,
    source_root: Path,
    expected_root_identity: tuple[int, int],
    relative_parts: tuple[str, ...],
    captured_identity: SourceIdentity,
) -> None:
    current_path_identity = current_source_path_identity(
        source_root,
        expected_root_identity,
        relative_parts,
    )
    if current_path_identity.stable_key() != captured_identity.stable_key():
        raise SourceChangedError(
            "registered source path changed while it was being captured",
            details={
                "relative_path": "/".join(relative_parts),
                "captured": captured_identity.stable_key(),
                "current_path": current_path_identity.stable_key(),
            },
        )


def validate_file_boundary(source: Path, source_root: Path, staging_root: Path) -> None:
    relative_source_parts(source, source_root)
    canonical_staging = staging_root.resolve(strict=False)
    if is_within(canonical_staging, source_root) or is_within(
        source_root, canonical_staging
    ):
        raise SourceBoundaryError(
            "staging and source roots overlap",
            details={
                "source_root": str(source_root),
                "staging_root": str(canonical_staging),
            },
        )


def native_source_path() -> Path | None:
    source = Path(__file__).with_name("native") / "corpus_file_provider.swift"
    return source if source.exists() else None


def build_native_helper(paths: RuntimePaths) -> Path:
    source = native_source_path()
    if source is None:
        raise HydrationUnavailableError(
            "native File Provider helper source is not present",
            details={"expected_directory": str(Path(__file__).with_name("native"))},
        )
    source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
    destination_name = f"corpus-hydrator-{source_digest[:24]}"
    destination = paths.runtime / destination_name

    with paths.open_runtime() as runtime_descriptor:
        try:
            existing_descriptor, _ = open_private_file_at(
                runtime_descriptor,
                destination_name,
                path=destination,
                flags=os.O_RDONLY | getattr(os, "O_EXEC", 0),
                expected_mode=0o700,
            )
        except ConfigurationError as exc:
            if exc.details.get("reason") != "missing":
                raise
        else:
            os.close(existing_descriptor)
            return destination

        with tempfile.TemporaryDirectory(prefix="corpus-helper-") as temporary:
            temporary_root = Path(temporary)
            temporary_output = temporary_root / destination_name
            command = ["swiftc", "-O", str(source), "-o", str(temporary_output)]
            try:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    check=False,
                    text=True,
                    timeout=120,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
                raise HydrationUnavailableError(
                    "could not build the native File Provider helper",
                    details={"error": str(exc), "source": str(source)},
                ) from exc
            if completed.returncode != 0:
                raise HydrationUnavailableError(
                    "native helper compilation failed",
                    details={
                        "source": str(source),
                        "stderr": completed.stderr[-4000:],
                    },
                )

            temporary_descriptor = os.open(
                temporary_output,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            try:
                temporary_stat = os.fstat(temporary_descriptor)
                if (
                    not stat.S_ISREG(temporary_stat.st_mode)
                    or temporary_stat.st_uid != os.geteuid()
                    or temporary_stat.st_nlink != 1
                ):
                    raise HydrationUnavailableError(
                        "native helper compiler output is unsafe",
                        details={"source": str(source)},
                    )
                runtime_temporary_name = f".{destination_name}.{uuid.uuid4().hex}.tmp"
                runtime_temporary_path = paths.runtime / runtime_temporary_name
                runtime_temporary_descriptor, _ = open_private_file_at(
                    runtime_descriptor,
                    runtime_temporary_name,
                    path=runtime_temporary_path,
                    flags=os.O_WRONLY,
                    create=True,
                    exclusive=True,
                    expected_mode=0o700,
                )
                try:
                    try:
                        while True:
                            chunk = os.read(temporary_descriptor, COPY_CHUNK_BYTES)
                            if not chunk:
                                break
                            view = memoryview(chunk)
                            while view:
                                written = os.write(runtime_temporary_descriptor, view)
                                if written <= 0:
                                    raise OSError(
                                        "native helper install made no progress"
                                    )
                                view = view[written:]
                        os.fsync(runtime_temporary_descriptor)
                    finally:
                        os.close(runtime_temporary_descriptor)
                    with suppress(FileExistsError):
                        os.link(
                            runtime_temporary_name,
                            destination_name,
                            src_dir_fd=runtime_descriptor,
                            dst_dir_fd=runtime_descriptor,
                            follow_symlinks=False,
                        )
                finally:
                    _unlink_if_present(runtime_descriptor, runtime_temporary_name)
                os.fsync(runtime_descriptor)
            finally:
                os.close(temporary_descriptor)

        installed_descriptor, _ = open_private_file_at(
            runtime_descriptor,
            destination_name,
            path=destination,
            flags=os.O_RDONLY | getattr(os, "O_EXEC", 0),
            expected_mode=0o700,
        )
        os.close(installed_descriptor)
    return destination


def _copy_with_native(
    *,
    helper: Path,
    source_descriptor: int,
    source: Path,
    source_root: Path,
    destination_directory_descriptor: int,
    destination_name: str,
    destination_path: Path,
    maximum_bytes: int,
    timeout_seconds: float,
) -> tuple[int, dict]:
    command = [
        str(helper),
        "copy",
        "--source",
        str(source),
        "--source-fd",
        str(source_descriptor),
        "--source-root",
        str(source_root),
        "--destination",
        str(destination_path),
        "--destination-dir-fd",
        str(destination_directory_descriptor),
        "--destination-name",
        destination_name,
        "--max-bytes",
        str(maximum_bytes),
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout_seconds,
            pass_fds=(source_descriptor, destination_directory_descriptor),
        )
    except subprocess.TimeoutExpired as exc:
        with suppress(FileNotFoundError):
            os.unlink(destination_name, dir_fd=destination_directory_descriptor)
        raise HydrationUnavailableError(
            "source capture timed out; cloud hydration state may still be changing",
            details={"path": str(source), "timeout_seconds": timeout_seconds},
        ) from exc
    payload_text = completed.stdout.strip().splitlines()
    payload = {}
    if payload_text:
        try:
            payload = json.loads(payload_text[-1])
        except json.JSONDecodeError:
            payload = {}
    if completed.returncode != 0:
        with suppress(FileNotFoundError):
            os.unlink(destination_name, dir_fd=destination_directory_descriptor)
        error = payload.get("error", {})
        if error.get("code") in {
            "source_changed_during_copy",
            "source_exceeds_maximum_bytes",
        }:
            raise SourceChangedError(
                "native helper detected a changed source during capture",
                details={
                    "path": str(source),
                    "result": payload,
                    "maximum_bytes": maximum_bytes,
                },
            )
        raise HydrationUnavailableError(
            "native source capture failed",
            details={
                "path": str(source),
                "returncode": completed.returncode,
                "result": payload,
                "stderr": completed.stderr[-4000:],
            },
        )
    result = payload.get("result", payload)
    if result.get("stable") is False:
        with suppress(FileNotFoundError):
            os.unlink(destination_name, dir_fd=destination_directory_descriptor)
        raise SourceChangedError(
            "native helper detected an unstable source during capture",
            details={"path": str(source), "result": result},
        )
    try:
        destination_descriptor, _ = open_private_file_at(
            destination_directory_descriptor,
            destination_name,
            path=destination_path,
        )
    except ConfigurationError as exc:
        if exc.details.get("reason") == "missing":
            raise HydrationUnavailableError(
                "native helper reported success without a staged copy",
                details={"path": str(source), "result": payload},
            ) from exc
        raise
    try:
        destination_size = os.fstat(destination_descriptor).st_size
    finally:
        os.close(destination_descriptor)
    copied = int(
        result.get("bytesCopied", result.get("bytes_copied", destination_size))
    )
    return copied, result


def _copy_resident_python(
    source_descriptor: int,
    *,
    destination_directory_descriptor: int,
    destination_name: str,
    destination_path: Path,
    maximum_bytes: int,
) -> int:
    destination_fd = None
    copied = 0
    try:
        opened = os.fstat(source_descriptor)
        opened_flags = int(getattr(opened, "st_flags", 0))
        if opened_flags & SF_DATALESS:
            raise HydrationRequiredError(
                "source became remote-only before capture",
                details={},
            )
        if opened.st_size > maximum_bytes:
            raise SourceChangedError(
                "source grew beyond the approved capture size",
                details={
                    "observed_bytes": opened.st_size,
                    "maximum_bytes": maximum_bytes,
                },
            )
        os.lseek(source_descriptor, 0, os.SEEK_SET)
        destination_fd, _ = open_private_file_at(
            destination_directory_descriptor,
            destination_name,
            path=destination_path,
            flags=os.O_WRONLY,
            create=True,
            exclusive=True,
        )
        while True:
            remaining = maximum_bytes - copied
            if remaining <= 0:
                break
            chunk = os.read(source_descriptor, min(COPY_CHUNK_BYTES, remaining))
            if not chunk:
                break
            view = memoryview(chunk)
            while view:
                written = os.write(destination_fd, view)
                view = view[written:]
                copied += written
        os.fsync(destination_fd)
    finally:
        if destination_fd is not None:
            os.close(destination_fd)
    return copied


def _hash_descriptor(descriptor: int) -> str:
    digest = hashlib.sha256()
    os.lseek(descriptor, 0, os.SEEK_SET)
    while True:
        chunk = os.read(descriptor, COPY_CHUNK_BYTES)
        if not chunk:
            break
        digest.update(chunk)
    return digest.hexdigest()


def _unlink_if_present(directory_descriptor: int, name: str) -> None:
    with suppress(FileNotFoundError):
        os.unlink(name, dir_fd=directory_descriptor)


def capture_to_staging(
    *,
    paths: RuntimePaths,
    source_root: Path,
    source: Path,
    allow_hydration: bool,
    maximum_bytes: int,
    timeout_seconds: float,
    expected_source_identity: tuple[int, int, int, int, int] | None = None,
) -> CapturedSource:
    if isinstance(maximum_bytes, bool) or maximum_bytes < 0:
        raise BudgetExceededError(
            "capture byte limit must be a nonnegative integer",
            details={"maximum_bytes": maximum_bytes},
        )
    paths.ensure()
    validate_file_boundary(source, source_root, paths.staging)
    relative_parts = relative_source_parts(source, source_root)

    staged_name = f"capture-{uuid.uuid4().hex}.part"
    if not CAPTURE_NAME_RE.fullmatch(staged_name):
        raise AssertionError("generated capture name is not canonical")
    staged = paths.staging / staged_name
    used_native = False
    native_result: dict = {}
    with ExitStack() as descriptors:
        source_root_descriptor = descriptors.enter_context(
            opened_source_root(source_root)
        )
        initial_source_root_identity = source_root_identity(source_root_descriptor)
        source_descriptor = descriptors.enter_context(
            opened_source_file(source_root_descriptor, relative_parts)
        )
        staging_descriptor = descriptors.enter_context(
            paths.open_corpus_directory("staging")
        )
        pre = source_identity_from_stat(os.fstat(source_descriptor))
        if (
            expected_source_identity is not None
            and pre.stable_key() != expected_source_identity
        ):
            raise SourceChangedError(
                "source metadata differs from the latest scan; rescan before ingest",
                details={
                    "relative_path": "/".join(relative_parts),
                    "scanned": expected_source_identity,
                    "current": pre.stable_key(),
                },
            )
        if pre.size > maximum_bytes:
            raise BudgetExceededError(
                "source exceeds the approved capture size",
                details={
                    "path": str(source),
                    "source_bytes": pre.size,
                    "maximum_bytes": maximum_bytes,
                },
            )
        hydration_required = pre.dataless
        if hydration_required and not allow_hydration:
            raise HydrationRequiredError(
                "source is a remote-only placeholder",
                details={"path": str(source), "logical_bytes": pre.size},
            )
        try:
            helper: Path | None = None
            try:
                helper = build_native_helper(paths)
            except HydrationUnavailableError:
                if hydration_required:
                    raise
            if helper is not None:
                copied, native_result = _copy_with_native(
                    helper=helper,
                    source_descriptor=source_descriptor,
                    source=source,
                    source_root=source_root,
                    destination_directory_descriptor=staging_descriptor,
                    destination_name=staged_name,
                    destination_path=staged,
                    maximum_bytes=maximum_bytes,
                    timeout_seconds=timeout_seconds,
                )
                used_native = True
            else:
                copied = _copy_resident_python(
                    source_descriptor,
                    destination_directory_descriptor=staging_descriptor,
                    destination_name=staged_name,
                    destination_path=staged,
                    maximum_bytes=maximum_bytes,
                )

            post = source_identity_from_stat(os.fstat(source_descriptor))
            provider_materialization = hydration_required and used_native
            source_stable = capture_identity_is_stable(
                before=pre,
                after=post,
                hydration_required=hydration_required,
                used_native_helper=used_native,
                native_result=native_result,
            )
            if not source_stable:
                raise SourceChangedError(
                    "source identity changed while it was being captured",
                    details={
                        "path": str(source),
                        "before": pre.__dict__,
                        "after": post.__dict__,
                        "provider_materialization": provider_materialization,
                        "native_result": native_result,
                    },
                )
            _require_current_source_path_identity(
                source_root=source_root,
                expected_root_identity=initial_source_root_identity,
                relative_parts=relative_parts,
                captured_identity=post,
            )
            staged_descriptor, _ = open_private_file_at(
                staging_descriptor,
                staged_name,
                path=staged,
            )
            try:
                staged_size = os.fstat(staged_descriptor).st_size
                if copied != pre.size or staged_size != pre.size:
                    raise SourceChangedError(
                        "captured byte count does not match source metadata",
                        details={
                            "path": str(source),
                            "expected": pre.size,
                            "reported_copied": copied,
                            "staged_size": staged_size,
                        },
                    )
                digest = _hash_descriptor(staged_descriptor)
            finally:
                os.close(staged_descriptor)

            if not BLOB_DIGEST_RE.fullmatch(digest):
                raise AssertionError("captured digest is not canonical")
            _require_current_source_path_identity(
                source_root=source_root,
                expected_root_identity=initial_source_root_identity,
                relative_parts=relative_parts,
                captured_identity=post,
            )
            return CapturedSource(
                sha256=digest,
                capture_path=staged,
                bytes_copied=copied,
                pre_identity=pre,
                post_identity=post,
                used_native_helper=used_native,
                hydration_was_required=hydration_required,
            )
        except Exception:
            _unlink_if_present(staging_descriptor, staged_name)
            raise


def discard_staged_capture(paths: RuntimePaths, captured: CapturedSource) -> None:
    """Delete one completed temporary capture through the owned staging directory."""

    capture_path = captured.capture_path
    if capture_path.parent != paths.staging or not CAPTURE_NAME_RE.fullmatch(
        capture_path.name
    ):
        raise ConfigurationError(
            "temporary source capture path is not canonical",
            details={"path": str(capture_path)},
        )
    with paths.open_corpus_directory("staging") as staging_descriptor:
        try:
            descriptor, _ = open_private_file_at(
                staging_descriptor,
                capture_path.name,
                path=capture_path,
            )
        except ConfigurationError as exc:
            if exc.details.get("reason") == "missing":
                return
            raise
        else:
            os.close(descriptor)
        os.unlink(capture_path.name, dir_fd=staging_descriptor)
        os.fsync(staging_descriptor)


def cleanup_abandoned_staging(paths: RuntimePaths) -> dict:
    """Delete canonical crash residues while no corpus writer is active."""

    staged_files = 0
    staged_bytes = 0
    unexpected = 0
    with paths.open_corpus_directory("staging") as staging_descriptor:
        for name in os.listdir(staging_descriptor):
            if not CAPTURE_NAME_RE.fullmatch(name):
                unexpected += 1
                continue
            candidate = paths.staging / name
            descriptor, _ = open_private_file_at(
                staging_descriptor,
                name,
                path=candidate,
            )
            try:
                staged_bytes += os.fstat(descriptor).st_size
            finally:
                os.close(descriptor)
            staged_files += 1
            os.unlink(name, dir_fd=staging_descriptor)
        if staged_files:
            os.fsync(staging_descriptor)
    return {
        "files_removed": staged_files,
        "bytes_removed": staged_bytes,
        "unexpected_entries_skipped": unexpected,
    }


def observe_staging(paths: RuntimePaths) -> dict:
    """Report a neutral point-in-time view of canonical staging files."""

    staged_files = 0
    staged_bytes = 0
    oldest_modified_ns: int | None = None
    unexpected = 0
    observed_at_ns = time.time_ns()
    with paths.open_corpus_directory("staging") as staging_descriptor:
        for name in os.listdir(staging_descriptor):
            if not CAPTURE_NAME_RE.fullmatch(name):
                unexpected += 1
                continue
            candidate = paths.staging / name
            try:
                descriptor, _ = open_private_file_at(
                    staging_descriptor,
                    name,
                    path=candidate,
                )
            except ConfigurationError as exc:
                # A writer may finish and delete its capture after listdir().
                if exc.details.get("reason") == "missing":
                    continue
                raise
            try:
                metadata = os.fstat(descriptor)
                staged_bytes += metadata.st_size
                oldest_modified_ns = (
                    metadata.st_mtime_ns
                    if oldest_modified_ns is None
                    else min(oldest_modified_ns, metadata.st_mtime_ns)
                )
            finally:
                os.close(descriptor)
            staged_files += 1
    return {
        "staging_files": staged_files,
        "staging_bytes": staged_bytes,
        "oldest_age_seconds": (
            max(0.0, (observed_at_ns - oldest_modified_ns) / 1_000_000_000)
            if oldest_modified_ns is not None
            else None
        ),
        "classification": "active_or_abandoned_not_determined",
        "unexpected_entries_skipped": unexpected,
    }


def cleanup_source_copies(
    paths: RuntimePaths,
    *,
    delete: bool,
) -> SourceCopyCleanup:
    """Plan or delete canonical source-byte copies owned by one corpus runtime."""

    paths.ensure()
    blob_files = 0
    blob_bytes = 0
    staged_files = 0
    staged_bytes = 0
    unexpected = 0
    canonical_digests: list[str] = []

    with paths.open_corpus_directory("staging") as staging_descriptor:
        for name in os.listdir(staging_descriptor):
            if not CAPTURE_NAME_RE.fullmatch(name):
                unexpected += 1
                continue
            candidate = paths.staging / name
            descriptor, _ = open_private_file_at(
                staging_descriptor,
                name,
                path=candidate,
            )
            try:
                staged_bytes += os.fstat(descriptor).st_size
            finally:
                os.close(descriptor)
            staged_files += 1
            if delete:
                os.unlink(name, dir_fd=staging_descriptor)
        if delete and staged_files:
            os.fsync(staging_descriptor)

    with paths.open_corpus_directory("blobs") as blobs_descriptor:
        for directory_name in os.listdir(blobs_descriptor):
            if not BLOB_DIRECTORY_RE.fullmatch(directory_name):
                unexpected += 1
                continue
            directory_path = paths.blobs / directory_name
            directory_descriptor = ensure_private_directory_at(
                blobs_descriptor,
                directory_name,
                path=directory_path,
            )
            deleted_from_directory = False
            try:
                for name in os.listdir(directory_descriptor):
                    match = BLOB_NAME_RE.fullmatch(name)
                    if match is None or not match.group(1).startswith(directory_name):
                        unexpected += 1
                        continue
                    candidate = directory_path / name
                    descriptor, _ = open_private_file_at(
                        directory_descriptor,
                        name,
                        path=candidate,
                    )
                    try:
                        blob_bytes += os.fstat(descriptor).st_size
                    finally:
                        os.close(descriptor)
                    blob_files += 1
                    canonical_digests.append(match.group(1))
                    if delete:
                        os.unlink(name, dir_fd=directory_descriptor)
                        deleted_from_directory = True
                if deleted_from_directory:
                    os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
            if delete:
                with suppress(OSError):
                    os.rmdir(directory_name, dir_fd=blobs_descriptor)
        if delete and blob_files:
            os.fsync(blobs_descriptor)

    return SourceCopyCleanup(
        persistent_blob_files=blob_files,
        persistent_blob_bytes=blob_bytes,
        staged_capture_files=staged_files,
        staged_capture_bytes=staged_bytes,
        unexpected_entries=unexpected,
        canonical_blob_digests=tuple(sorted(set(canonical_digests))),
    )
