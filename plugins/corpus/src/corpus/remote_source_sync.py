"""Durable, principal-vault-bound source transfer for a remote Corpus host.

This is a server-side primitive for a trusted control plane.  It is deliberately
not an MCP tool: callers provide a principal-derived source slot and an ownership
guard, then stream only canonical source files.  Corpus databases, indexes, local
paths, provider records, and session state are never accepted as transfer input.
"""

from __future__ import annotations

import ctypes
import fcntl
import hashlib
import json
import os
import shutil
import stat
import sys
import time
import unicodedata
import uuid
from collections.abc import Callable, Iterable
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

from .config import (
    ensure_private_directory_at,
    is_within,
    normalize_corpus_id,
    normalize_source_scope,
    open_private_file_at,
    private_directory,
)
from .database import encode_json, get_corpus, utc_now
from .errors import CorpusError, CorpusNotFoundError
from .formats import classify
from .locking import context_writer_lock
from .remote_deletion_state import require_no_remote_delete_intent
from .service import CorpusService
from .source_access import open_directory_at, opened_source_file, opened_source_root

SOURCE_SYNC_FORMAT = "corpus-source-sync-v1"
SOURCE_SYNC_STATE_FORMAT = "corpus-source-sync-state-v1"
SOURCE_SYNC_GENERATION_FORMAT = "corpus-source-sync-generation-v1"
SOURCE_SYNC_EPOCH_FORMAT = "corpus-source-sync-deletion-epoch-v1"
SOURCE_SYNC_EPOCH_MAX_BYTES = 64 * 1024
SOURCE_SYNC_MAX_FILE_BYTES = 250 * 1024 * 1024
SOURCE_SYNC_MAX_TOTAL_BYTES = 500 * 1024 * 1024
SOURCE_SYNC_MAX_PATH_BYTES = 4096
SOURCE_SYNC_MAX_COMPONENT_BYTES = 255
SOURCE_SYNC_MAX_PATH_COMPONENTS = 64
SOURCE_SYNC_INDEX_BATCH_FILES = 50
SOURCE_SYNC_MAX_INDEX_BATCHES = 20
SOURCE_SYNC_MAX_FILES = SOURCE_SYNC_INDEX_BATCH_FILES * SOURCE_SYNC_MAX_INDEX_BATCHES
# A JSON string can escape every path byte once. Keep the durable/control bound
# derived from the canonical schema rather than from typical short filenames.
SOURCE_SYNC_MAX_MANIFEST_BYTES = (
    SOURCE_SYNC_MAX_FILES * ((2 * SOURCE_SYNC_MAX_PATH_BYTES) + 256)
) + 4096
SOURCE_SYNC_MAX_IDEMPOTENCY_KEY_BYTES = 512
SOURCE_SYNC_MAX_TENANT_ACTIVE_OPERATIONS = 4
SOURCE_SYNC_MAX_TENANT_RESERVED_BYTES = 1024 * 1024 * 1024
SOURCE_SYNC_MAX_COMPLETED_RECEIPTS_PER_CORPUS = 16
SOURCE_SYNC_RETIRED_OPERATION_PREFIX = ".retired-sync-"
SOURCE_SYNC_COPY_CHUNK_BYTES = 1024 * 1024
SOURCE_SYNC_STATE_MAX_BYTES = (
    SOURCE_SYNC_MAX_FILES * ((2 * SOURCE_SYNC_MAX_PATH_BYTES) + 64)
) + (2 * 1024 * 1024)
SOURCE_SYNC_INVENTORY_PAGE_FILES = 100
SOURCE_SYNC_RESULT_DOCUMENTS = 50
SOURCE_SYNC_TIMEOUT_SECONDS = 600.0
SOURCE_SYNC_OPERATION_STATUSES = frozenset(
    {
        "staging",
        "ready",
        "applying",
        "source_installed",
        "indexing",
        "recovery_required",
        "cancelling",
        "cancelled",
        "applied_cleanup_required",
        "applied",
    }
)
SOURCE_SYNC_TERMINAL_STATUSES = frozenset({"applied", "cancelled"})
SOURCE_SYNC_STATE_FIELDS = frozenset(
    {
        "base_generation",
        "base_generation_receipt",
        "base_manifest_sha256",
        "corpus_id",
        "created_at",
        "file_count",
        "format",
        "idempotency_key_sha256",
        "incoming_name",
        "index_batch_cursor",
        "index_batch_plan_sha256",
        "manifest_sha256",
        "operation_id",
        "previous_slot_preserved",
        "recovery",
        "registration_existed_at_begin",
        "registration_state",
        "result",
        "source_installed",
        "staged_paths",
        "status",
        "total_bytes",
        "updated_at",
    }
)

SourceSlotGuard = Callable[[CorpusService, str, Path], bool]


def _source_sync_incoming_name(corpus_id: str, operation_id: str) -> str:
    return f".{corpus_id}.{operation_id}.incoming"


class SourceSyncValidationError(CorpusError):
    code = "source_sync_validation_error"


class SourceSyncConflictError(CorpusError):
    code = "source_sync_conflict"


class SourceSyncUnavailableError(CorpusError):
    code = "source_sync_unavailable"


class SourceSyncRecoveryRequiredError(CorpusError):
    code = "source_sync_recovery_required"


def _sha256_json(value: object) -> str:
    return hashlib.sha256(encode_json(value).encode()).hexdigest()


def _require_plain_component(component: str, *, relative_path: str) -> None:
    encoded = component.encode("utf-8")
    if (
        not component
        or component in {".", ".."}
        or "/" in component
        or "\\" in component
        or "\x00" in component
        or len(encoded) > SOURCE_SYNC_MAX_COMPONENT_BYTES
        or any(unicodedata.category(character).startswith("C") for character in component)
    ):
        raise SourceSyncValidationError(
            "source sync path contains an unsafe component",
            details={"relative_path": relative_path, "reason": "invalid_component"},
        )


def _canonical_relative_path(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise SourceSyncValidationError("source sync relative_path must be a non-empty string")
    if value != unicodedata.normalize("NFC", value):
        raise SourceSyncValidationError(
            "source sync relative_path must already be NFC-normalized",
            details={"reason": "unicode_not_canonical"},
        )
    if value.startswith(("/", "\\")) or "\\" in value or "\x00" in value:
        raise SourceSyncValidationError(
            "source sync path must be a relative POSIX path",
            details={"relative_path": value, "reason": "not_relative_posix"},
        )
    encoded = value.encode("utf-8")
    if len(encoded) > SOURCE_SYNC_MAX_PATH_BYTES:
        raise SourceSyncValidationError(
            "source sync path exceeds the byte limit",
            details={
                "relative_path": value,
                "maximum_bytes": SOURCE_SYNC_MAX_PATH_BYTES,
            },
        )
    parts = value.split("/")
    if len(parts) > SOURCE_SYNC_MAX_PATH_COMPONENTS:
        raise SourceSyncValidationError(
            "source sync path is too deep",
            details={
                "relative_path": value,
                "maximum_components": SOURCE_SYNC_MAX_PATH_COMPONENTS,
            },
        )
    for component in parts:
        _require_plain_component(component, relative_path=value)
    return "/".join(parts)


def canonical_source_sync_manifest(manifest: object) -> tuple[dict[str, Any], str]:
    """Validate and canonicalize a v1 source-only transfer manifest."""

    if not isinstance(manifest, dict) or set(manifest) != {
        "format",
        "corpus_id",
        "files",
        "total_bytes",
    }:
        raise SourceSyncValidationError(
            "source sync manifest fields are invalid",
            details={
                "required": ["corpus_id", "files", "format", "total_bytes"],
            },
        )
    if manifest["format"] != SOURCE_SYNC_FORMAT:
        raise SourceSyncValidationError(
            "source sync manifest format is unsupported",
            details={"required_format": SOURCE_SYNC_FORMAT},
        )
    raw_corpus_id = manifest["corpus_id"]
    if not isinstance(raw_corpus_id, str):
        raise SourceSyncValidationError("manifest corpus_id must be a string")
    try:
        corpus_id = normalize_corpus_id(raw_corpus_id)
    except CorpusError as exc:
        raise SourceSyncValidationError("manifest corpus_id is invalid") from exc
    if raw_corpus_id != corpus_id:
        raise SourceSyncValidationError(
            "manifest corpus_id must already be canonical",
            details={"canonical_corpus_id": corpus_id},
        )
    raw_files = manifest["files"]
    if not isinstance(raw_files, list) or len(raw_files) > SOURCE_SYNC_MAX_FILES:
        raise SourceSyncValidationError(
            "source sync file count exceeds the supported bound",
            details={"maximum_files": SOURCE_SYNC_MAX_FILES},
        )

    files: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    seen_folded_paths: set[str] = set()
    total_bytes = 0
    for raw_file in raw_files:
        if not isinstance(raw_file, dict) or set(raw_file) != {
            "relative_path",
            "size",
            "sha256",
        }:
            raise SourceSyncValidationError(
                "source sync file fields are invalid",
                details={"required": ["relative_path", "sha256", "size"]},
            )
        relative_path = _canonical_relative_path(raw_file["relative_path"])
        if classify(relative_path.rsplit("/", 1)[-1])[3] != "supported":
            raise SourceSyncValidationError(
                "source sync manifest may contain supported Corpus formats only",
                details={
                    "relative_path": relative_path,
                    "reason": "unsupported_format",
                },
            )
        folded = relative_path.casefold()
        if relative_path in seen_paths:
            raise SourceSyncValidationError(
                "source sync paths must be unique",
                details={"relative_path": relative_path, "reason": "duplicate_path"},
            )
        if folded in seen_folded_paths:
            raise SourceSyncValidationError(
                "source sync paths collide on a case-insensitive filesystem",
                details={"relative_path": relative_path, "reason": "casefold_collision"},
            )
        raw_size = raw_file["size"]
        if type(raw_size) is not int or not 0 <= raw_size <= SOURCE_SYNC_MAX_FILE_BYTES:
            raise SourceSyncValidationError(
                "source sync file size exceeds the supported bound",
                details={
                    "relative_path": relative_path,
                    "maximum_bytes": SOURCE_SYNC_MAX_FILE_BYTES,
                },
            )
        raw_sha256 = raw_file["sha256"]
        if (
            not isinstance(raw_sha256, str)
            or len(raw_sha256) != 64
            or any(character not in "0123456789abcdef" for character in raw_sha256)
        ):
            raise SourceSyncValidationError(
                "source sync sha256 must be a lowercase hex digest",
                details={"relative_path": relative_path},
            )
        seen_paths.add(relative_path)
        seen_folded_paths.add(folded)
        total_bytes += raw_size
        if total_bytes > SOURCE_SYNC_MAX_TOTAL_BYTES:
            raise SourceSyncValidationError(
                "source sync total bytes exceed the supported bound",
                details={"maximum_bytes": SOURCE_SYNC_MAX_TOTAL_BYTES},
            )
        files.append(
            {
                "relative_path": relative_path,
                "size": raw_size,
                "sha256": raw_sha256,
            }
        )

    files.sort(key=lambda item: item["relative_path"].encode("utf-8"))
    folded_paths = {item["relative_path"].casefold() for item in files}
    for item in files:
        parts = item["relative_path"].split("/")
        if any(
            "/".join(parts[:index]).casefold() in folded_paths for index in range(1, len(parts))
        ):
            raise SourceSyncValidationError(
                "a source sync file path cannot also be a directory",
                details={
                    "relative_path": item["relative_path"],
                    "reason": "file_directory_collision",
                },
            )

    if type(manifest["total_bytes"]) is not int or manifest["total_bytes"] != total_bytes:
        raise SourceSyncValidationError(
            "manifest total_bytes does not match its file entries",
            details={"computed_total_bytes": total_bytes},
        )
    canonical = {
        "format": SOURCE_SYNC_FORMAT,
        "corpus_id": corpus_id,
        "files": files,
        "total_bytes": total_bytes,
    }
    serialized = encode_json(canonical).encode()
    if len(serialized) > SOURCE_SYNC_MAX_MANIFEST_BYTES:
        raise SourceSyncValidationError(
            "source sync manifest exceeds its serialized byte limit",
            details={"maximum_bytes": SOURCE_SYNC_MAX_MANIFEST_BYTES},
        )
    return canonical, hashlib.sha256(serialized).hexdigest()


def _source_sync_index_batches(manifest: dict[str, Any]) -> list[list[dict[str, Any]]]:
    files = manifest["files"]
    return [
        files[offset : offset + SOURCE_SYNC_INDEX_BATCH_FILES]
        for offset in range(0, len(files), SOURCE_SYNC_INDEX_BATCH_FILES)
    ]


def _source_sync_index_batch_receipts(
    manifest: dict[str, Any],
) -> list[dict[str, Any]]:
    receipts = []
    for batch_index, files in enumerate(_source_sync_index_batches(manifest)):
        receipt = {
            "format": "corpus-source-sync-index-batch-v1",
            "batch_index": batch_index,
            "files": files,
        }
        receipts.append(
            {
                "batch_index": batch_index,
                "file_count": len(files),
                "logical_bytes": sum(item["size"] for item in files),
                "sha256": _sha256_json(receipt),
            }
        )
    return receipts


def _source_sync_index_plan_sha256(manifest: dict[str, Any]) -> str:
    return _sha256_json(
        {
            "format": "corpus-source-sync-index-plan-v1",
            "manifest_sha256": _sha256_json(manifest),
            "batches": _source_sync_index_batch_receipts(manifest),
        }
    )


def _source_scope_excludes(
    relative_path_nfc: str,
    name_nfc: str,
    *,
    excluded_directory_names: set[str],
    excluded_path_prefixes: tuple[str, ...],
) -> bool:
    return name_nfc in excluded_directory_names or any(
        relative_path_nfc == prefix or relative_path_nfc.startswith(f"{prefix}/")
        for prefix in excluded_path_prefixes
    )


def build_source_sync_manifest(
    *,
    corpus_id: str,
    source_root: Path,
    source_scope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Hash the current supported/included local files into a canonical manifest.

    Selection reuses Corpus' format eligibility and source-scope rules. Symbolic
    links, special files, ignored files, unsupported formats, and excluded
    directories are not uploaded. A selected hard-linked or changing regular file
    is rejected rather than copied through an unstable alias.
    """

    try:
        normalized_corpus_id = normalize_corpus_id(corpus_id)
    except CorpusError as exc:
        raise SourceSyncValidationError("manifest corpus_id is invalid") from exc
    if corpus_id != normalized_corpus_id:
        raise SourceSyncValidationError("manifest corpus_id must already be canonical")
    if source_scope is None:
        normalized_scope = normalize_source_scope()
    elif isinstance(source_scope, dict):
        normalized_scope = normalize_source_scope(
            exclude_directory_names=source_scope.get("exclude_directory_names", ()),
            exclude_path_prefixes=source_scope.get("exclude_path_prefixes", ()),
        )
    else:
        raise SourceSyncValidationError("source_scope must be an object")
    excluded_names = set(normalized_scope["exclude_directory_names"])
    excluded_prefixes = tuple(normalized_scope["exclude_path_prefixes"])

    files: list[dict[str, Any]] = []
    total_bytes = 0
    canonical_to_raw: dict[str, tuple[str, ...]] = {}
    with opened_source_root(source_root) as root_descriptor:
        root_identity = os.fstat(root_descriptor)
        stack: list[tuple[tuple[str, ...], int]] = [((), os.dup(root_descriptor))]
        try:
            while stack:
                directory_parts, directory_descriptor = stack.pop()
                try:
                    with os.scandir(directory_descriptor) as iterator:
                        entries = sorted(
                            list(iterator),
                            key=lambda entry: unicodedata.normalize("NFC", entry.name).encode(
                                "utf-8"
                            ),
                        )
                    for entry in entries:
                        raw_parts = (*directory_parts, entry.name)
                        raw_relative = "/".join(raw_parts)
                        canonical_relative = unicodedata.normalize("NFC", raw_relative)
                        canonical_name = unicodedata.normalize("NFC", entry.name)
                        _canonical_relative_path(canonical_relative)
                        try:
                            metadata = os.stat(
                                entry.name,
                                dir_fd=directory_descriptor,
                                follow_symlinks=False,
                            )
                        except OSError as exc:
                            raise SourceSyncValidationError(
                                "source changed while its sync manifest was built",
                                details={"relative_path": canonical_relative},
                            ) from exc
                        if stat.S_ISLNK(metadata.st_mode):
                            continue
                        if stat.S_ISDIR(metadata.st_mode):
                            if _source_scope_excludes(
                                canonical_relative,
                                canonical_name,
                                excluded_directory_names=excluded_names,
                                excluded_path_prefixes=excluded_prefixes,
                            ):
                                continue
                            child = open_directory_at(
                                directory_descriptor,
                                entry.name,
                                relative_path=canonical_relative,
                                expected=metadata,
                            )
                            stack.append((raw_parts, child))
                            continue
                        if not stat.S_ISREG(metadata.st_mode):
                            continue
                        if classify(entry.name)[3] != "supported":
                            continue
                        if metadata.st_nlink != 1:
                            raise SourceSyncValidationError(
                                "selected source files must not be hard-linked",
                                details={
                                    "relative_path": canonical_relative,
                                    "reason": "unexpected_link_count",
                                },
                            )
                        if canonical_relative in canonical_to_raw:
                            raise SourceSyncValidationError(
                                "source paths collide after Unicode normalization",
                                details={
                                    "relative_path": canonical_relative,
                                    "reason": "unicode_collision",
                                },
                            )
                        if any(
                            existing.casefold() == canonical_relative.casefold()
                            for existing in canonical_to_raw
                        ):
                            raise SourceSyncValidationError(
                                "source paths collide on a case-insensitive filesystem",
                                details={
                                    "relative_path": canonical_relative,
                                    "reason": "casefold_collision",
                                },
                            )
                        if metadata.st_size > SOURCE_SYNC_MAX_FILE_BYTES:
                            raise SourceSyncValidationError(
                                "selected source file exceeds the transfer limit",
                                details={
                                    "relative_path": canonical_relative,
                                    "maximum_bytes": SOURCE_SYNC_MAX_FILE_BYTES,
                                },
                            )
                        if len(files) >= SOURCE_SYNC_MAX_FILES:
                            raise SourceSyncValidationError(
                                "selected source file count exceeds the transfer limit",
                                details={"maximum_files": SOURCE_SYNC_MAX_FILES},
                            )
                        digest = hashlib.sha256()
                        observed_bytes = 0
                        with opened_source_file(root_descriptor, raw_parts) as descriptor:
                            opened = os.fstat(descriptor)
                            if opened.st_nlink != 1:
                                raise SourceSyncValidationError(
                                    "selected source files must not be hard-linked",
                                    details={"relative_path": canonical_relative},
                                )
                            while chunk := os.read(descriptor, SOURCE_SYNC_COPY_CHUNK_BYTES):
                                observed_bytes += len(chunk)
                                if observed_bytes > SOURCE_SYNC_MAX_FILE_BYTES:
                                    raise SourceSyncValidationError(
                                        "selected source file grew beyond the transfer limit",
                                        details={"relative_path": canonical_relative},
                                    )
                                digest.update(chunk)
                            closed = os.fstat(descriptor)
                        if (
                            opened.st_dev != metadata.st_dev
                            or opened.st_ino != metadata.st_ino
                            or opened.st_size != metadata.st_size
                            or closed.st_dev != opened.st_dev
                            or closed.st_ino != opened.st_ino
                            or closed.st_size != opened.st_size
                            or closed.st_mtime_ns != opened.st_mtime_ns
                            or closed.st_ctime_ns != opened.st_ctime_ns
                            or observed_bytes != opened.st_size
                        ):
                            raise SourceSyncValidationError(
                                "source changed while its sync manifest was built",
                                details={"relative_path": canonical_relative},
                            )
                        total_bytes += observed_bytes
                        if total_bytes > SOURCE_SYNC_MAX_TOTAL_BYTES:
                            raise SourceSyncValidationError(
                                "selected source bytes exceed the transfer limit",
                                details={"maximum_bytes": SOURCE_SYNC_MAX_TOTAL_BYTES},
                            )
                        canonical_to_raw[canonical_relative] = raw_parts
                        files.append(
                            {
                                "relative_path": canonical_relative,
                                "size": observed_bytes,
                                "sha256": digest.hexdigest(),
                            }
                        )
                finally:
                    os.close(directory_descriptor)
        finally:
            for _parts, descriptor in stack:
                os.close(descriptor)
        current_root = os.fstat(root_descriptor)
        if (
            current_root.st_dev != root_identity.st_dev
            or current_root.st_ino != root_identity.st_ino
        ):
            raise SourceSyncValidationError("source root changed while its sync manifest was built")

    raw_manifest = {
        "format": SOURCE_SYNC_FORMAT,
        "corpus_id": normalized_corpus_id,
        "files": files,
        "total_bytes": total_bytes,
    }
    canonical, _manifest_sha256 = canonical_source_sync_manifest(raw_manifest)
    return canonical


def _raw_component_for_canonical(
    directory_descriptor: int,
    canonical_component: str,
    *,
    relative_path: str,
) -> str:
    try:
        names = os.listdir(directory_descriptor)
    except OSError as exc:
        raise SourceSyncValidationError(
            "source directory could not be read before upload",
            details={"relative_path": relative_path},
        ) from exc
    matches = [name for name in names if unicodedata.normalize("NFC", name) == canonical_component]
    if len(matches) != 1:
        raise SourceSyncValidationError(
            "source path is missing or collides after Unicode normalization",
            details={
                "relative_path": relative_path,
                "reason": "canonical_path_not_unique",
            },
        )
    return matches[0]


def iter_source_sync_file(
    *,
    source_root: Path,
    relative_path: str,
    expected_size: int,
    expected_sha256: str,
) -> Iterable[bytes]:
    """Reopen and stream one manifest file through the descriptor-pinned boundary.

    The canonical NFC path is mapped back to exactly one raw filesystem name per
    component. The generator raises after the last yielded chunk if the bytes or
    any observed file identity differ from the manifest, so callers must exhaust
    it and fail the upload request on any iterator exception.
    """

    canonical_path = _canonical_relative_path(relative_path)
    single = {
        "format": SOURCE_SYNC_FORMAT,
        "corpus_id": "validation",
        "files": [
            {
                "relative_path": canonical_path,
                "size": expected_size,
                "sha256": expected_sha256,
            }
        ],
        "total_bytes": expected_size,
    }
    canonical_source_sync_manifest(single)
    canonical_parts = canonical_path.split("/")

    with opened_source_root(source_root) as root_descriptor:
        root_before = os.fstat(root_descriptor)
        current = os.dup(root_descriptor)
        raw_parts: list[str] = []
        try:
            for index, canonical_component in enumerate(canonical_parts[:-1], start=1):
                current_path = "/".join(canonical_parts[:index])
                raw_component = _raw_component_for_canonical(
                    current,
                    canonical_component,
                    relative_path=current_path,
                )
                try:
                    before = os.stat(
                        raw_component,
                        dir_fd=current,
                        follow_symlinks=False,
                    )
                except OSError as exc:
                    raise SourceSyncValidationError(
                        "source directory changed before upload",
                        details={"relative_path": current_path},
                    ) from exc
                if not stat.S_ISDIR(before.st_mode):
                    raise SourceSyncValidationError(
                        "manifest path parent is not a directory",
                        details={"relative_path": current_path},
                    )
                child = open_directory_at(
                    current,
                    raw_component,
                    relative_path=current_path,
                    expected=before,
                )
                os.close(current)
                current = child
                raw_parts.append(raw_component)

            raw_name = _raw_component_for_canonical(
                current,
                canonical_parts[-1],
                relative_path=canonical_path,
            )
            raw_parts.append(raw_name)
            try:
                path_before = os.stat(
                    raw_name,
                    dir_fd=current,
                    follow_symlinks=False,
                )
            except OSError as exc:
                raise SourceSyncValidationError(
                    "source file changed before upload",
                    details={"relative_path": canonical_path},
                ) from exc
            if not stat.S_ISREG(path_before.st_mode) or path_before.st_nlink != 1:
                raise SourceSyncValidationError(
                    "manifest source must remain one regular non-hardlinked file",
                    details={"relative_path": canonical_path},
                )
            if path_before.st_size != expected_size:
                raise SourceSyncConflictError(
                    "source file size changed after manifest creation",
                    details={"relative_path": canonical_path},
                )

            digest = hashlib.sha256()
            observed = 0
            with opened_source_file(root_descriptor, tuple(raw_parts)) as descriptor:
                opened = os.fstat(descriptor)
                if (
                    opened.st_dev != path_before.st_dev
                    or opened.st_ino != path_before.st_ino
                    or opened.st_nlink != 1
                    or not stat.S_ISREG(opened.st_mode)
                ):
                    raise SourceSyncValidationError(
                        "source file changed while opening for upload",
                        details={"relative_path": canonical_path},
                    )
                while chunk := os.read(descriptor, SOURCE_SYNC_COPY_CHUNK_BYTES):
                    observed += len(chunk)
                    if observed > expected_size:
                        raise SourceSyncConflictError(
                            "source file grew after manifest creation",
                            details={"relative_path": canonical_path},
                        )
                    digest.update(chunk)
                    yield chunk
                closed = os.fstat(descriptor)
            try:
                path_after = os.stat(
                    raw_name,
                    dir_fd=current,
                    follow_symlinks=False,
                )
            except OSError as exc:
                raise SourceSyncValidationError(
                    "source file changed during upload",
                    details={"relative_path": canonical_path},
                ) from exc
            root_after = os.fstat(root_descriptor)
            if (
                observed != expected_size
                or digest.hexdigest() != expected_sha256
                or closed.st_dev != opened.st_dev
                or closed.st_ino != opened.st_ino
                or closed.st_size != opened.st_size
                or closed.st_mtime_ns != opened.st_mtime_ns
                or closed.st_ctime_ns != opened.st_ctime_ns
                or path_after.st_dev != opened.st_dev
                or path_after.st_ino != opened.st_ino
                or path_after.st_size != opened.st_size
                or path_after.st_mtime_ns != opened.st_mtime_ns
                or path_after.st_ctime_ns != opened.st_ctime_ns
                or path_after.st_nlink != 1
                or root_after.st_dev != root_before.st_dev
                or root_after.st_ino != root_before.st_ino
            ):
                raise SourceSyncConflictError(
                    "source file bytes or identity changed during upload",
                    details={"relative_path": canonical_path},
                )
        finally:
            os.close(current)


def _require_private_directory_stat(metadata: os.stat_result, *, reason: str) -> None:
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise SourceSyncUnavailableError(
            "source sync vault directory is not private",
            details={"reason": reason},
        )


def _require_private_file_stat(
    metadata: os.stat_result,
    *,
    relative_path: str,
) -> None:
    if not stat.S_ISREG(metadata.st_mode):
        raise SourceSyncValidationError(
            "staged source contains a non-regular file",
            details={"relative_path": relative_path, "reason": "not_regular_file"},
        )
    if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != 0o600:
        raise SourceSyncValidationError(
            "staged source file is not private",
            details={"relative_path": relative_path, "reason": "unsafe_permissions"},
        )
    if metadata.st_nlink != 1:
        raise SourceSyncValidationError(
            "staged source files must not be hard-linked",
            details={"relative_path": relative_path, "reason": "unexpected_link_count"},
        )


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise OSError("source sync write made no progress")
        offset += written


def _atomic_json_write(directory_descriptor: int, name: str, value: object) -> None:
    payload = encode_json(value).encode()
    temporary_name = f".{name}.{uuid.uuid4().hex}.tmp"
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
            dir_fd=directory_descriptor,
        )
        os.fchmod(descriptor, 0o600)
        _write_all(descriptor, payload)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.rename(
            temporary_name,
            name,
            src_dir_fd=directory_descriptor,
            dst_dir_fd=directory_descriptor,
        )
        os.fsync(directory_descriptor)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        with suppress(FileNotFoundError):
            os.unlink(temporary_name, dir_fd=directory_descriptor)


def _read_private_json(
    directory_descriptor: int,
    name: str,
    *,
    path: Path,
    maximum_bytes: int,
) -> dict[str, Any]:
    descriptor, _created = open_private_file_at(
        directory_descriptor,
        name,
        path=path,
    )
    try:
        payload = b""
        while len(payload) <= maximum_bytes:
            chunk = os.read(descriptor, min(65536, maximum_bytes + 1 - len(payload)))
            if not chunk:
                break
            payload += chunk
        if len(payload) > maximum_bytes:
            raise SourceSyncValidationError("source sync state exceeds its byte limit")
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceSyncValidationError("source sync durable state is invalid") from exc
    finally:
        os.close(descriptor)
    if not isinstance(value, dict):
        raise SourceSyncValidationError("source sync durable state is invalid")
    return value


def _read_source_sync_epoch(data_root: Path, corpus_id: str) -> dict[str, Any]:
    corpus_id = normalize_corpus_id(corpus_id)
    root = data_root / "source-sync-epochs"
    try:
        with private_directory(root) as root_descriptor:
            try:
                value = _read_private_json(
                    root_descriptor,
                    f"{corpus_id}.json",
                    path=root / f"{corpus_id}.json",
                    maximum_bytes=SOURCE_SYNC_EPOCH_MAX_BYTES,
                )
            except CorpusError as exc:
                if exc.details.get("reason") == "missing":
                    return {"generation": 0, "manifest_sha256": None}
                raise
    except CorpusError as exc:
        if exc.details.get("reason") in {"missing", "missing_parent"}:
            return {"generation": 0, "manifest_sha256": None}
        raise SourceSyncRecoveryRequiredError(
            "source deletion epoch storage is unavailable",
            details={"reason": "source_epoch_unavailable"},
        ) from exc
    if (
        set(value) != {"corpus_id", "format", "generation", "manifest_sha256", "updated_at"}
        or value.get("format") != SOURCE_SYNC_EPOCH_FORMAT
        or value.get("corpus_id") != corpus_id
        or type(value.get("generation")) is not int
        or value["generation"] <= 0
        or not isinstance(value.get("manifest_sha256"), str)
        or len(value["manifest_sha256"]) != 64
        or any(character not in "0123456789abcdef" for character in value["manifest_sha256"])
        or not isinstance(value.get("updated_at"), str)
    ):
        raise SourceSyncRecoveryRequiredError(
            "source deletion epoch is invalid",
            details={"reason": "source_epoch_invalid"},
        )
    return {
        "generation": value["generation"],
        "manifest_sha256": value["manifest_sha256"],
    }


def persist_source_sync_deletion_epoch(
    data_root: Path,
    corpus_id: str,
    generation: int,
    manifest_sha256: str,
) -> None:
    """Durably preserve a content-free lifecycle floor across Corpus deletion."""

    corpus_id = normalize_corpus_id(corpus_id)
    if type(generation) is not int or generation <= 0:
        raise SourceSyncValidationError("source deletion epoch must be a positive integer")
    if (
        not isinstance(manifest_sha256, str)
        or len(manifest_sha256) != 64
        or any(character not in "0123456789abcdef" for character in manifest_sha256)
    ):
        raise SourceSyncValidationError("source deletion epoch digest must be lowercase sha256")
    current = _read_source_sync_epoch(data_root, corpus_id)
    if current["generation"] > generation or (
        current["generation"] == generation and current["manifest_sha256"] != manifest_sha256
    ):
        raise SourceSyncRecoveryRequiredError(
            "source deletion epoch advanced beyond this deletion intent",
            details={"reason": "source_epoch_advanced"},
        )
    root = data_root / "source-sync-epochs"
    with private_directory(data_root, create=True) as data_descriptor:
        try:
            os.stat("source-sync-epochs", dir_fd=data_descriptor, follow_symlinks=False)
            created = False
        except FileNotFoundError:
            created = True
        epoch_descriptor = ensure_private_directory_at(
            data_descriptor,
            "source-sync-epochs",
            path=root,
        )
        try:
            if created:
                os.fsync(data_descriptor)
            if current["generation"] < generation:
                _atomic_json_write(
                    epoch_descriptor,
                    f"{corpus_id}.json",
                    {
                        "format": SOURCE_SYNC_EPOCH_FORMAT,
                        "corpus_id": corpus_id,
                        "generation": generation,
                        "manifest_sha256": manifest_sha256,
                        "updated_at": utc_now(),
                    },
                )
            else:
                # A retry after write/rename may not know whether the parent fsync
                # completed, so sync the authoritative directory again.
                os.fsync(epoch_descriptor)
        finally:
            os.close(epoch_descriptor)


def next_source_sync_deletion_epoch(data_root: Path, corpus_id: str) -> dict[str, Any]:
    """Choose one monotonic lifecycle tombstone while deletion locks are held."""

    corpus_id = normalize_corpus_id(corpus_id)
    epoch = _read_source_sync_epoch(data_root, corpus_id)
    corpus_root = data_root / "source-sync" / corpus_id
    try:
        with private_directory(corpus_root) as corpus_descriptor:
            generation = _read_generation(
                corpus_descriptor,
                corpus_root,
                absent_generation=epoch["generation"],
                absent_manifest_sha256=epoch["manifest_sha256"],
            )
    except CorpusError as exc:
        if exc.details.get("reason") not in {"missing", "missing_parent"}:
            raise
        generation = {
            "generation": epoch["generation"],
            "manifest_sha256": epoch["manifest_sha256"],
            "active_operation_id": None,
            "index_state": "deleted" if epoch["generation"] else "legacy_or_absent",
        }
    if generation.get("active_operation_id") is not None or generation["index_state"] not in {
        "deleted",
        "indexed",
        "legacy_or_absent",
    }:
        raise SourceSyncConflictError(
            "source sync must settle before its lifecycle can be deleted",
            details={"reason": "source_sync_in_progress"},
        )
    return {
        "generation": max(epoch["generation"], generation["generation"]) + 1,
        "manifest_sha256": hashlib.sha256(os.urandom(32)).hexdigest(),
    }


def _read_durable_operation(
    operation_descriptor: int,
    operation_path: Path,
    operation_id: str,
    *,
    expected_corpus_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = _read_private_json(
        operation_descriptor,
        "manifest.json",
        path=operation_path / "manifest.json",
        maximum_bytes=SOURCE_SYNC_MAX_MANIFEST_BYTES,
    )
    canonical, digest = canonical_source_sync_manifest(manifest)
    state = _read_private_json(
        operation_descriptor,
        "state.json",
        path=operation_path / "state.json",
        maximum_bytes=SOURCE_SYNC_STATE_MAX_BYTES,
    )
    manifest_paths = {item["relative_path"] for item in canonical["files"]}
    raw_staged_paths = state.get("staged_paths")
    staged_paths_valid = (
        isinstance(raw_staged_paths, list)
        and all(isinstance(value, str) for value in raw_staged_paths)
        and len(set(raw_staged_paths)) == len(raw_staged_paths)
        and raw_staged_paths == sorted(raw_staged_paths, key=lambda value: value.encode("utf-8"))
        and set(raw_staged_paths) <= manifest_paths
    )
    base_receipt = state.get("base_generation_receipt")
    batch_count = (
        len(canonical["files"]) + SOURCE_SYNC_INDEX_BATCH_FILES - 1
    ) // SOURCE_SYNC_INDEX_BATCH_FILES
    legacy_state_fields = SOURCE_SYNC_STATE_FIELDS - {
        "index_batch_cursor",
        "index_batch_plan_sha256",
    }
    if set(state) == legacy_state_fields:
        # 0.2 receipts used the same durable format before server-side indexing
        # was split into batches. Normalize them in memory so an exact retry can
        # still finish cleanup or replay a terminal result.
        state = dict(state)
        if state.get("status") in {"applied", "applied_cleanup_required"}:
            state["index_batch_cursor"] = batch_count
            state["index_batch_plan_sha256"] = _source_sync_index_plan_sha256(
                canonical
            )
        else:
            state["index_batch_cursor"] = 0
            state["index_batch_plan_sha256"] = None
    batch_plan_sha256 = state.get("index_batch_plan_sha256")
    if (
        set(state) != SOURCE_SYNC_STATE_FIELDS
        or state.get("format") != SOURCE_SYNC_STATE_FORMAT
        or state.get("manifest_sha256") != digest
        or state.get("corpus_id") != canonical["corpus_id"]
        or (expected_corpus_id is not None and state.get("corpus_id") != expected_corpus_id)
        or state.get("operation_id") != operation_id
        or operation_path.name != operation_id
        or state.get("incoming_name")
        != _source_sync_incoming_name(canonical["corpus_id"], operation_id)
        or not isinstance(state.get("idempotency_key_sha256"), str)
        or len(state["idempotency_key_sha256"]) != 64
        or state.get("file_count") != len(canonical["files"])
        or state.get("total_bytes") != canonical["total_bytes"]
        or type(state.get("index_batch_cursor")) is not int
        or not 0 <= state["index_batch_cursor"] <= batch_count
        or (
            batch_plan_sha256 is not None
            and (
                not isinstance(batch_plan_sha256, str)
                or len(batch_plan_sha256) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in batch_plan_sha256
                )
            )
        )
        or (batch_plan_sha256 is None and state["index_batch_cursor"] != 0)
        or (
            state.get("status") in {"applied", "applied_cleanup_required"}
            and (
                batch_plan_sha256 is None
                or state["index_batch_cursor"] != batch_count
            )
        )
        or state.get("status") not in SOURCE_SYNC_OPERATION_STATUSES
        or not staged_paths_valid
        or type(state.get("source_installed")) is not bool
        or type(state.get("previous_slot_preserved")) is not bool
        or type(state.get("registration_existed_at_begin")) is not bool
        or state.get("registration_state") not in {"absent", "verified", "registered"}
        or type(state.get("base_generation")) is not int
        or state["base_generation"] < 0
        or (
            state.get("base_manifest_sha256") is not None
            and (
                not isinstance(state["base_manifest_sha256"], str)
                or len(state["base_manifest_sha256"]) != 64
            )
        )
        or not isinstance(base_receipt, dict)
        or base_receipt.get("generation") != state["base_generation"]
        or base_receipt.get("manifest_sha256") != state["base_manifest_sha256"]
        or state.get("recovery") is not None
        and not isinstance(state["recovery"], dict)
        or state.get("result") is not None
        and not isinstance(state["result"], dict)
        or not isinstance(state.get("created_at"), str)
        or not isinstance(state.get("updated_at"), str)
    ):
        raise SourceSyncConflictError(
            "source sync durable state does not match its manifest",
            details={"reason": "durable_state_mismatch"},
        )
    return canonical, state


def _read_generation(
    corpus_descriptor: int,
    corpus_root: Path,
    *,
    absent_generation: int = 0,
    absent_manifest_sha256: str | None = None,
) -> dict[str, Any]:
    try:
        os.stat(
            "source-generation.json",
            dir_fd=corpus_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return {
            "format": SOURCE_SYNC_GENERATION_FORMAT,
            "generation": absent_generation,
            "manifest_sha256": absent_manifest_sha256,
            "operation_id": None,
            "active_operation_id": None,
            "index_state": "deleted" if absent_generation else "legacy_or_absent",
            "reserved_bytes": 0,
        }
    value = _read_private_json(
        corpus_descriptor,
        "source-generation.json",
        path=corpus_root / "source-generation.json",
        maximum_bytes=64 * 1024,
    )
    if (
        set(value)
        != {
            "active_operation_id",
            "committed_at",
            "format",
            "generation",
            "index_state",
            "manifest_sha256",
            "operation_id",
            "reserved_bytes",
            "updated_at",
        }
        or value.get("format") != SOURCE_SYNC_GENERATION_FORMAT
        or type(value.get("generation")) is not int
        or value["generation"] < 0
        or value.get("index_state")
        not in {
            "staging_reserved",
            "swap_pending",
            "index_pending",
            "index_failed",
            "indexed",
            "deleted",
        }
        or type(value.get("reserved_bytes")) is not int
        or not 0 <= value["reserved_bytes"] <= SOURCE_SYNC_MAX_TOTAL_BYTES
        or (
            value.get("manifest_sha256") is not None
            and (
                not isinstance(value["manifest_sha256"], str) or len(value["manifest_sha256"]) != 64
            )
        )
        or (
            value.get("index_state") == "deleted"
            and (
                not isinstance(value.get("manifest_sha256"), str)
                or len(value["manifest_sha256"]) != 64
                or value.get("operation_id") is not None
                or value.get("active_operation_id") is not None
                or value.get("reserved_bytes") != 0
            )
        )
        or (value.get("operation_id") is not None and not isinstance(value["operation_id"], str))
        or (
            value.get("active_operation_id") is not None
            and not isinstance(value["active_operation_id"], str)
        )
    ):
        raise SourceSyncConflictError(
            "source sync generation receipt is invalid",
            details={"reason": "generation_receipt_invalid"},
        )
    return value


def _write_generation(
    corpus_descriptor: int,
    value: dict[str, Any],
) -> None:
    value["updated_at"] = utc_now()
    value.setdefault("committed_at", None)
    _atomic_json_write(corpus_descriptor, "source-generation.json", value)


def _restore_generation(
    corpus_descriptor: int,
    base_generation: dict[str, Any],
) -> None:
    if base_generation["generation"] == 0:
        with suppress(FileNotFoundError):
            os.unlink("source-generation.json", dir_fd=corpus_descriptor)
        os.fsync(corpus_descriptor)
        return
    restored = dict(base_generation)
    restored["active_operation_id"] = None
    _write_generation(corpus_descriptor, restored)


def source_sync_delete_blocker(data_root: Path, corpus_id: str) -> dict[str, str] | None:
    """Return a fail-closed delete blocker for a committed-but-unindexed source."""

    try:
        corpus_id = normalize_corpus_id(corpus_id)
        corpus_root = data_root / "source-sync" / corpus_id
        with private_directory(corpus_root) as corpus_descriptor:
            generation = _read_generation(corpus_descriptor, corpus_root)
            operation_status: str | None = None
            if generation.get("active_operation_id") is None:
                for operation_name in os.listdir(corpus_descriptor):
                    if operation_name in {"writer.lock", "source-generation.json"} or (
                        operation_name.startswith(SOURCE_SYNC_RETIRED_OPERATION_PREFIX)
                    ):
                        continue
                    if not operation_name.startswith("sync_"):
                        raise SourceSyncUnavailableError(
                            "source sync state contains an unexpected entry",
                            details={"reason": "unexpected_corpus_state"},
                        )
                    operation_descriptor = _open_operation_directory(
                        corpus_descriptor,
                        corpus_root,
                        operation_name,
                        create=False,
                    )
                    try:
                        _manifest, state = _read_durable_operation(
                            operation_descriptor,
                            corpus_root / operation_name,
                            operation_name,
                            expected_corpus_id=corpus_id,
                        )
                    finally:
                        os.close(operation_descriptor)
                    status = state.get("status")
                    if status not in SOURCE_SYNC_TERMINAL_STATUSES:
                        operation_status = status if isinstance(status, str) else "unknown"
                        break
    except CorpusError as exc:
        if exc.details.get("reason") in {"missing", "missing_parent"}:
            return None
        return {
            "reason": "source_sync_state_unavailable",
            "index_state": "unknown",
        }
    if generation.get("active_operation_id") is not None or generation["index_state"] in {
        "staging_reserved",
        "swap_pending",
        "index_pending",
        "index_failed",
    }:
        return {
            "reason": "source_sync_in_progress",
            "index_state": generation["index_state"],
        }
    if operation_status is not None:
        return {
            "reason": "source_sync_in_progress",
            "index_state": operation_status,
        }
    return None


def require_source_sync_readable(data_root: Path, corpus_id: str) -> None:
    """Fail closed when the live source and managed index may be split-brain."""

    try:
        corpus_id = normalize_corpus_id(corpus_id)
        corpus_root = data_root / "source-sync" / corpus_id
        with private_directory(corpus_root) as corpus_descriptor:
            generation = _read_generation(corpus_descriptor, corpus_root)
    except CorpusError as exc:
        if exc.details.get("reason") in {"missing", "missing_parent"}:
            return
        raise SourceSyncRecoveryRequiredError(
            "source sync state is unavailable for a coherent remote read",
            details={
                "reason": "source_sync_recovery_required",
                "index_state": "unknown",
            },
        ) from exc
    if generation["index_state"] in {"swap_pending", "index_pending", "index_failed"}:
        raise SourceSyncRecoveryRequiredError(
            "source generation and server index must converge before remote reads resume",
            details={
                "reason": "source_sync_recovery_required",
                "index_state": generation["index_state"],
            },
        )


def read_coordinated_source_sync_head(
    service: CorpusService,
    corpus_id: str,
) -> dict[str, Any]:
    """Read a content-free source head while the caller coordinates writers.

    This helper intentionally does not acquire the source-sync lock. Its caller
    must already hold the tenant context writer lock and must compare the exact
    returned value again before committing a cross-corpus operation. A staged
    upload projects the still-installed base generation, matching ``head()``.
    """

    corpus_id = normalize_corpus_id(corpus_id)
    epoch = _read_source_sync_epoch(service.data_root, corpus_id)
    corpus_root = service.data_root / "source-sync" / corpus_id
    try:
        with private_directory(corpus_root) as corpus_descriptor:
            generation = _read_generation(
                corpus_descriptor,
                corpus_root,
                absent_generation=epoch["generation"],
                absent_manifest_sha256=epoch["manifest_sha256"],
            )
            if generation["index_state"] != "staging_reserved":
                return {
                    "generation": generation["generation"],
                    "manifest_sha256": generation["manifest_sha256"],
                    "index_state": generation["index_state"],
                }
            operation_id = generation.get("active_operation_id")
            if not isinstance(operation_id, str):
                raise SourceSyncRecoveryRequiredError(
                    "source generation reservation is incomplete",
                    details={"reason": "source_generation_state_invalid"},
                )
            operation_descriptor = _open_operation_directory(
                corpus_descriptor,
                corpus_root,
                operation_id,
                create=False,
            )
            try:
                _manifest, state = _read_durable_operation(
                    operation_descriptor,
                    corpus_root / operation_id,
                    operation_id,
                    expected_corpus_id=corpus_id,
                )
            finally:
                os.close(operation_descriptor)
            if (
                state["status"] not in {"staging", "ready", "cancelling"}
                or state["base_generation"] != generation["generation"]
                or state["manifest_sha256"] != generation["manifest_sha256"]
            ):
                raise SourceSyncRecoveryRequiredError(
                    "source generation reservation does not match its operation",
                    details={"reason": "source_generation_state_invalid"},
                )
            return {
                "generation": state["base_generation"],
                "manifest_sha256": state["base_manifest_sha256"],
                "index_state": generation["index_state"],
            }
    except CorpusError as exc:
        if exc.details.get("reason") not in {"missing", "missing_parent"}:
            raise
    return {
        "generation": epoch["generation"],
        "manifest_sha256": epoch["manifest_sha256"],
        "index_state": "deleted" if epoch["generation"] else "legacy_or_absent",
    }


def _require_tenant_reservation_capacity(
    data_root: Path,
    *,
    corpus_id: str,
    operation_id: str,
    requested_bytes: int,
) -> None:
    root = data_root / "source-sync"
    active_count = 0
    reserved_bytes = 0
    current_reserved = False
    with private_directory(root) as root_descriptor:
        for name in os.listdir(root_descriptor):
            if name == "tenant.writer.lock":
                continue
            try:
                normalized_name = normalize_corpus_id(name)
            except CorpusError as exc:
                raise SourceSyncUnavailableError(
                    "source sync tenant reservation state is invalid",
                    details={"reason": "unexpected_tenant_state"},
                ) from exc
            if normalized_name != name:
                raise SourceSyncUnavailableError(
                    "source sync tenant reservation state is invalid",
                    details={"reason": "unexpected_tenant_state"},
                )
            corpus_descriptor = _open_private_child_directory(
                root_descriptor,
                name,
                relative_path="__tenant_reservation__",
                create=False,
            )
            try:
                generation = _read_generation(
                    corpus_descriptor,
                    root / name,
                )
                active = generation.get("active_operation_id")
                reservations: list[tuple[str, int]] = []
                if active is not None:
                    reservations.append((active, generation["reserved_bytes"]))
                else:
                    for operation_name in os.listdir(corpus_descriptor):
                        if operation_name in {"writer.lock", "source-generation.json"}:
                            continue
                        if operation_name.startswith(SOURCE_SYNC_RETIRED_OPERATION_PREFIX):
                            _safe_remove_tree(root / name / operation_name)
                            os.fsync(corpus_descriptor)
                            continue
                        if not operation_name.startswith("sync_"):
                            raise SourceSyncUnavailableError(
                                "source sync tenant reservation state is invalid",
                                details={"reason": "unexpected_corpus_state"},
                            )
                        operation_descriptor = _open_operation_directory(
                            corpus_descriptor,
                            root / name,
                            operation_name,
                            create=False,
                        )
                        try:
                            try:
                                _manifest, state = _read_durable_operation(
                                    operation_descriptor,
                                    root / name / operation_name,
                                    operation_name,
                                    expected_corpus_id=name,
                                )
                            except CorpusError:
                                reservations.append((operation_name, SOURCE_SYNC_MAX_TOTAL_BYTES))
                                continue
                            if state.get("status") not in SOURCE_SYNC_TERMINAL_STATUSES:
                                raw_total = state.get("total_bytes")
                                total = (
                                    raw_total
                                    if type(raw_total) is int
                                    and 0 <= raw_total <= SOURCE_SYNC_MAX_TOTAL_BYTES
                                    else SOURCE_SYNC_MAX_TOTAL_BYTES
                                )
                                reservations.append((operation_name, total))
                        finally:
                            os.close(operation_descriptor)
                for reserved_operation, total in reservations:
                    active_count += 1
                    reserved_bytes += total
                    if name == corpus_id:
                        if reserved_operation == operation_id:
                            current_reserved = True
                        else:
                            raise SourceSyncConflictError(
                                "another incomplete source upload already exists for this corpus",
                                details={"reason": "incomplete_operation_exists"},
                            )
            finally:
                os.close(corpus_descriptor)

    if current_reserved:
        return
    if active_count + 1 > SOURCE_SYNC_MAX_TENANT_ACTIVE_OPERATIONS:
        raise SourceSyncUnavailableError(
            "source sync tenant active-operation limit is reached",
            details={
                "reason": "tenant_active_operation_limit",
                "maximum": SOURCE_SYNC_MAX_TENANT_ACTIVE_OPERATIONS,
            },
        )
    if reserved_bytes + requested_bytes > SOURCE_SYNC_MAX_TENANT_RESERVED_BYTES:
        raise SourceSyncUnavailableError(
            "source sync tenant reserved-byte limit is reached",
            details={
                "reason": "tenant_reserved_byte_limit",
                "maximum_bytes": SOURCE_SYNC_MAX_TENANT_RESERVED_BYTES,
            },
        )


@contextmanager
def _tenant_reservation_lock(data_root: Path, *, create: bool):
    root = data_root / "source-sync"
    with private_directory(root, create=create) as root_descriptor:
        tenant_lock_path = root / "tenant.writer.lock"
        tenant_lock, _created = open_private_file_at(
            root_descriptor,
            "tenant.writer.lock",
            path=tenant_lock_path,
            flags=os.O_RDWR,
            create=True,
        )
        fcntl.flock(tenant_lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(tenant_lock, fcntl.LOCK_UN)
            os.close(tenant_lock)


@contextmanager
def _corpus_sync_lock(data_root: Path, corpus_id: str, *, create: bool):
    root = data_root / "source-sync"
    corpus_root = root / corpus_id
    with private_directory(root, create=create) as root_descriptor:
        if create:
            corpus_descriptor = ensure_private_directory_at(
                root_descriptor,
                corpus_id,
                path=corpus_root,
            )
            os.fsync(root_descriptor)
        else:
            corpus_descriptor = _open_private_child_directory(
                root_descriptor,
                corpus_id,
                relative_path="__source_sync_corpus__",
                create=False,
            )
        try:
            lock_path = corpus_root / "writer.lock"
            descriptor, _created = open_private_file_at(
                corpus_descriptor,
                "writer.lock",
                path=lock_path,
                flags=os.O_RDWR,
                create=True,
            )
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                yield corpus_descriptor, corpus_root
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)
        finally:
            os.close(corpus_descriptor)


def _remove_empty_corpus_sync_root(data_root: Path, corpus_id: str) -> None:
    root = data_root / "source-sync"
    corpus_root = root / corpus_id
    with private_directory(root) as root_descriptor:
        try:
            corpus_descriptor = _open_private_child_directory(
                root_descriptor,
                corpus_id,
                relative_path="__source_sync_corpus__",
                create=False,
            )
        except CorpusError:
            return
        try:
            entries = os.listdir(corpus_descriptor)
            if entries not in ([], ["writer.lock"]):
                return
            if entries:
                descriptor, _created = open_private_file_at(
                    corpus_descriptor,
                    "writer.lock",
                    path=corpus_root / "writer.lock",
                    flags=os.O_RDWR,
                    create=False,
                )
                os.close(descriptor)
                os.unlink("writer.lock", dir_fd=corpus_descriptor)
                os.fsync(corpus_descriptor)
        finally:
            os.close(corpus_descriptor)
        try:
            os.rmdir(corpus_id, dir_fd=root_descriptor)
        except FileNotFoundError:
            return
        os.fsync(root_descriptor)


@contextmanager
def _source_sync_begin_lock(
    data_root: Path,
    corpus_id: str,
    *,
    operation_id: str,
    requested_bytes: int,
):
    # Capacity is checked before a per-corpus directory is created, so denied
    # requests cannot consume unbounded empty directories. Apply never waits on
    # this tenant lock, which keeps the global reservation section short.
    with _tenant_reservation_lock(data_root, create=True):
        _require_tenant_reservation_capacity(
            data_root,
            corpus_id=corpus_id,
            operation_id=operation_id,
            requested_bytes=requested_bytes,
        )
        corpus_root = data_root / "source-sync" / corpus_id
        corpus_root_existed = corpus_root.exists()
        try:
            with (
                _corpus_sync_lock(data_root, corpus_id, create=True) as locked,
                context_writer_lock(data_root),
            ):
                yield locked
        except BaseException:
            if not corpus_root_existed:
                _remove_empty_corpus_sync_root(data_root, corpus_id)
            raise


@contextmanager
def _source_sync_cancel_lock(data_root: Path, corpus_id: str):
    with (
        _tenant_reservation_lock(data_root, create=False),
        _corpus_sync_lock(data_root, corpus_id, create=False) as locked,
    ):
        yield locked


@contextmanager
def remote_source_sync_deletion_lock(data_root: Path, corpus_id: str):
    """Serialize deletion with begin/stage/apply using the shared lock order."""

    corpus_id = normalize_corpus_id(corpus_id)
    root = data_root / "source-sync"
    with _tenant_reservation_lock(data_root, create=True):
        with private_directory(root) as root_descriptor:
            try:
                metadata = os.stat(
                    corpus_id,
                    dir_fd=root_descriptor,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                corpus_state_exists = False
            else:
                _require_private_directory_stat(
                    metadata,
                    reason="unsafe_source_sync_corpus_state",
                )
                corpus_state_exists = True
        if corpus_state_exists:
            with (
                _corpus_sync_lock(data_root, corpus_id, create=False) as locked,
                context_writer_lock(data_root),
            ):
                yield locked
        else:
            with context_writer_lock(data_root):
                yield None


def _open_operation_directory(
    corpus_descriptor: int,
    corpus_root: Path,
    operation_id: str,
    *,
    create: bool,
) -> int:
    operation_path = corpus_root / operation_id
    if create:
        return ensure_private_directory_at(
            corpus_descriptor,
            operation_id,
            path=operation_path,
        )
    try:
        metadata = os.stat(
            operation_id,
            dir_fd=corpus_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError as exc:
        raise SourceSyncConflictError(
            "source sync idempotency key is unknown",
            details={"reason": "unknown_idempotency_key"},
        ) from exc
    _require_private_directory_stat(metadata, reason="unsafe_operation_directory")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(operation_id, flags, dir_fd=corpus_descriptor)
    except OSError as exc:
        raise SourceSyncConflictError(
            "source sync durable operation could not be opened",
            details={"reason": f"operation_open_failed:{exc.errno}"},
        ) from exc
    opened = os.fstat(descriptor)
    if opened.st_dev != metadata.st_dev or opened.st_ino != metadata.st_ino:
        os.close(descriptor)
        raise SourceSyncConflictError(
            "source sync durable operation changed while opening",
            details={"reason": "operation_changed_during_open"},
        )
    return descriptor


def _prune_completed_operations(
    corpus_descriptor: int,
    corpus_root: Path,
    *,
    keep_operation_id: str,
) -> None:
    for name in os.listdir(corpus_descriptor):
        if name.startswith(SOURCE_SYNC_RETIRED_OPERATION_PREFIX):
            _safe_remove_tree(corpus_root / name)
            os.fsync(corpus_descriptor)
    completed: list[tuple[str, str]] = []
    for name in os.listdir(corpus_descriptor):
        if not name.startswith("sync_") or name == keep_operation_id:
            continue
        operation_descriptor = _open_operation_directory(
            corpus_descriptor,
            corpus_root,
            name,
            create=False,
        )
        try:
            try:
                _manifest, state = _read_durable_operation(
                    operation_descriptor,
                    corpus_root / name,
                    name,
                )
            except CorpusError:
                continue
            if state.get("status") in SOURCE_SYNC_TERMINAL_STATUSES and isinstance(
                state.get("updated_at"), str
            ):
                completed.append((state["updated_at"], name))
        finally:
            os.close(operation_descriptor)
    completed.sort()
    # The current applied operation is preserved separately by the caller, so
    # leave room for it inside the corpus-wide receipt bound.
    retained_other_receipts = max(
        0,
        SOURCE_SYNC_MAX_COMPLETED_RECEIPTS_PER_CORPUS - 1,
    )
    remove_count = max(0, len(completed) - retained_other_receipts)
    for _updated_at, operation_id in completed[:remove_count]:
        retired_name = f"{SOURCE_SYNC_RETIRED_OPERATION_PREFIX}{uuid.uuid4().hex}"
        os.rename(
            operation_id,
            retired_name,
            src_dir_fd=corpus_descriptor,
            dst_dir_fd=corpus_descriptor,
        )
        os.fsync(corpus_descriptor)
        _safe_remove_tree(corpus_root / retired_name)
        os.fsync(corpus_descriptor)


def _operation_id(idempotency_key: str) -> tuple[str, str]:
    if not isinstance(idempotency_key, str) or not idempotency_key:
        raise SourceSyncValidationError("idempotency key must be a non-empty string")
    encoded = idempotency_key.encode("utf-8")
    if len(encoded) > SOURCE_SYNC_MAX_IDEMPOTENCY_KEY_BYTES:
        raise SourceSyncValidationError(
            "idempotency key exceeds its byte limit",
            details={"maximum_bytes": SOURCE_SYNC_MAX_IDEMPOTENCY_KEY_BYTES},
        )
    digest = hashlib.sha256(encoded).hexdigest()
    return f"sync_{digest[:40]}", digest


def _slot_lexical_path(source_slot: Path, corpus_id: str) -> Path:
    expanded = source_slot.expanduser()
    if not expanded.is_absolute() or any(part in {".", ".."} for part in expanded.parts):
        raise SourceSyncUnavailableError(
            "source sync slot must be an absolute canonical path",
            details={"reason": "invalid_slot_path"},
        )
    if expanded.name != corpus_id:
        raise SourceSyncUnavailableError(
            "source sync slot does not match the corpus id",
            details={"reason": "slot_name_mismatch"},
        )
    return expanded


@contextmanager
def _opened_vault_parent(source_slot: Path):
    with opened_source_root(source_slot.parent) as parent_descriptor:
        _require_private_directory_stat(
            os.fstat(parent_descriptor),
            reason="unsafe_vault_parent",
        )
        yield parent_descriptor


def _slot_metadata(parent_descriptor: int, slot_name: str) -> os.stat_result | None:
    try:
        metadata = os.stat(slot_name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise SourceSyncUnavailableError(
            "source sync slot could not be inspected",
            details={"reason": f"slot_stat_failed:{exc.errno}"},
        ) from exc
    _require_private_directory_stat(metadata, reason="unsafe_source_slot")
    return metadata


def _open_private_child_directory(
    parent_descriptor: int,
    name: str,
    *,
    relative_path: str,
    create: bool,
) -> int:
    _require_plain_component(name, relative_path=relative_path)
    created = False
    if create:
        try:
            os.mkdir(name, 0o700, dir_fd=parent_descriptor)
            created = True
        except FileExistsError:
            pass
        except OSError as exc:
            raise SourceSyncUnavailableError(
                "source sync staging directory could not be created",
                details={"relative_path": relative_path, "reason": f"mkdir_failed:{exc.errno}"},
            ) from exc
    if created:
        os.fsync(parent_descriptor)
    try:
        before = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError as exc:
        raise SourceSyncUnavailableError(
            "source sync staging directory is unavailable",
            details={"relative_path": relative_path, "reason": "missing"},
        ) from exc
    except OSError as exc:
        raise SourceSyncUnavailableError(
            "source sync staging directory is unavailable",
            details={"relative_path": relative_path, "reason": f"stat_failed:{exc.errno}"},
        ) from exc
    _require_private_directory_stat(before, reason="unsafe_staging_directory")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(name, flags, dir_fd=parent_descriptor)
    except OSError as exc:
        raise SourceSyncUnavailableError(
            "source sync staging directory could not be opened",
            details={"relative_path": relative_path, "reason": f"open_failed:{exc.errno}"},
        ) from exc
    opened = os.fstat(descriptor)
    if opened.st_dev != before.st_dev or opened.st_ino != before.st_ino:
        os.close(descriptor)
        raise SourceSyncUnavailableError(
            "source sync staging directory changed while opening",
            details={"relative_path": relative_path},
        )
    return descriptor


def _open_file_parent(
    root_descriptor: int,
    relative_path: str,
    *,
    create: bool,
) -> tuple[int, str]:
    parts = relative_path.split("/")
    current = os.dup(root_descriptor)
    try:
        for index, component in enumerate(parts[:-1], start=1):
            next_descriptor = _open_private_child_directory(
                current,
                component,
                relative_path="/".join(parts[:index]),
                create=create,
            )
            os.close(current)
            current = next_descriptor
        return current, parts[-1]
    except Exception:
        os.close(current)
        raise


def _read_and_hash_regular_file(
    parent_descriptor: int,
    name: str,
    *,
    relative_path: str,
) -> tuple[int, str]:
    try:
        before = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except OSError as exc:
        raise SourceSyncValidationError(
            "staged source file is unavailable",
            details={"relative_path": relative_path},
        ) from exc
    _require_private_file_stat(before, relative_path=relative_path)
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_descriptor,
        )
    except OSError as exc:
        raise SourceSyncValidationError(
            "staged source file could not be opened securely",
            details={"relative_path": relative_path},
        ) from exc
    digest = hashlib.sha256()
    observed = 0
    try:
        opened = os.fstat(descriptor)
        _require_private_file_stat(opened, relative_path=relative_path)
        if opened.st_dev != before.st_dev or opened.st_ino != before.st_ino:
            raise SourceSyncValidationError(
                "staged source file changed while opening",
                details={"relative_path": relative_path},
            )
        while chunk := os.read(descriptor, SOURCE_SYNC_COPY_CHUNK_BYTES):
            observed += len(chunk)
            if observed > SOURCE_SYNC_MAX_FILE_BYTES:
                raise SourceSyncValidationError(
                    "staged source file exceeds the byte limit",
                    details={"relative_path": relative_path},
                )
            digest.update(chunk)
        closed = os.fstat(descriptor)
        if (
            closed.st_dev != opened.st_dev
            or closed.st_ino != opened.st_ino
            or closed.st_size != opened.st_size
            or closed.st_mtime_ns != opened.st_mtime_ns
            or closed.st_ctime_ns != opened.st_ctime_ns
            or observed != opened.st_size
        ):
            raise SourceSyncValidationError(
                "staged source file changed while reading",
                details={"relative_path": relative_path},
            )
    finally:
        os.close(descriptor)
    return observed, digest.hexdigest()


def _tree_observation(
    root_descriptor: int,
    manifest: dict[str, Any],
) -> tuple[bool, dict[str, Any]]:
    expected_files = {item["relative_path"]: item for item in manifest["files"]}
    expected_directories = {
        "/".join(parts[:index])
        for relative_path in expected_files
        for parts in [relative_path.split("/")]
        for index in range(1, len(parts))
    }
    observed_files: set[str] = set()
    observed_directories: set[str] = set()
    stack: list[tuple[tuple[str, ...], int]] = [((), os.dup(root_descriptor))]
    try:
        while stack:
            directory_parts, directory_descriptor = stack.pop()
            try:
                with os.scandir(directory_descriptor) as iterator:
                    entries = list(iterator)
                for entry in entries:
                    parts = (*directory_parts, entry.name)
                    raw_path = "/".join(parts)
                    canonical_path = unicodedata.normalize("NFC", raw_path)
                    if canonical_path != raw_path:
                        return False, {"reason": "unicode_not_canonical"}
                    try:
                        metadata = os.stat(
                            entry.name,
                            dir_fd=directory_descriptor,
                            follow_symlinks=False,
                        )
                    except OSError:
                        return False, {"reason": "entry_stat_failed"}
                    if stat.S_ISDIR(metadata.st_mode):
                        try:
                            _require_private_directory_stat(
                                metadata,
                                reason="unsafe_staged_directory",
                            )
                        except SourceSyncUnavailableError:
                            return False, {"reason": "unsafe_staged_directory"}
                        if canonical_path not in expected_directories:
                            return False, {"reason": "unexpected_directory"}
                        observed_directories.add(canonical_path)
                        child = _open_private_child_directory(
                            directory_descriptor,
                            entry.name,
                            relative_path=canonical_path,
                            create=False,
                        )
                        stack.append((parts, child))
                        continue
                    if not stat.S_ISREG(metadata.st_mode):
                        return False, {"reason": "non_regular_entry"}
                    expected = expected_files.get(canonical_path)
                    if expected is None:
                        return False, {"reason": "unexpected_file"}
                    try:
                        size, digest = _read_and_hash_regular_file(
                            directory_descriptor,
                            entry.name,
                            relative_path=canonical_path,
                        )
                    except CorpusError:
                        return False, {"reason": "unsafe_or_changed_file"}
                    if size != expected["size"] or digest != expected["sha256"]:
                        return False, {"reason": "file_digest_mismatch"}
                    observed_files.add(canonical_path)
            finally:
                os.close(directory_descriptor)
    finally:
        for _parts, descriptor in stack:
            os.close(descriptor)
    matches = observed_files == set(expected_files) and observed_directories == expected_directories
    return matches, {
        "reason": "match" if matches else "manifest_entries_missing",
        "observed_files": len(observed_files),
        "expected_files": len(expected_files),
    }


def _atomic_exchange_at(parent_descriptor: int, first: str, second: str) -> None:
    """Atomically exchange two sibling directories or fail before changing either."""

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
        raise SourceSyncUnavailableError(
            "this host cannot atomically exchange source directories",
            details={"reason": "atomic_exchange_unavailable"},
        )
    if result != 0:
        error = ctypes.get_errno()
        raise SourceSyncUnavailableError(
            "source directories could not be atomically exchanged",
            details={"reason": f"atomic_exchange_failed:{error}"},
        )
    os.fsync(parent_descriptor)


def _safe_remove_tree(path: Path) -> None:
    if not shutil.rmtree.avoids_symlink_attacks:
        raise SourceSyncUnavailableError(
            "this host cannot safely remove a private source staging tree",
            details={"reason": "safe_tree_removal_unavailable"},
        )
    shutil.rmtree(path)


class RemoteSourceSyncService:
    """Durable source-only transfer bound to one principal-owned vault slot."""

    def __init__(
        self,
        service: CorpusService,
        *,
        source_slot: Path,
        source_slot_guard: SourceSlotGuard,
    ) -> None:
        if not callable(source_slot_guard):
            raise SourceSyncUnavailableError("source sync requires an ownership guard")
        self.service = service
        self.source_slot = source_slot
        self.source_slot_guard = source_slot_guard

    def _require_slot(self, corpus_id: str) -> Path:
        slot = _slot_lexical_path(self.source_slot, corpus_id)
        try:
            allowed = self.source_slot_guard(self.service, corpus_id, slot) is True
        except Exception:
            allowed = False
        if not allowed:
            raise SourceSyncUnavailableError(
                "source sync slot is not owned by this principal",
                details={"reason": "source_slot_not_owned"},
            )
        data_root = self.service.data_root.expanduser().resolve(strict=False)
        lexical_slot = slot.parent.resolve(strict=False) / slot.name
        if is_within(lexical_slot, data_root) or is_within(data_root, lexical_slot):
            raise SourceSyncUnavailableError(
                "source sync slot overlaps Corpus managed state",
                details={"reason": "slot_runtime_overlap"},
            )
        with _opened_vault_parent(slot) as parent_descriptor:
            _slot_metadata(parent_descriptor, slot.name)
        return slot

    def _registration(self, corpus_id: str, slot: Path) -> dict[str, Any] | None:
        try:
            corpus = get_corpus(self.service.data_root, corpus_id)
        except CorpusNotFoundError:
            return None
        if (
            Path(corpus["source_root"]) != slot
            or corpus["execution_policy"] != "external_host_allowed"
            or corpus["provider_kind"] != "filesystem"
        ):
            raise SourceSyncConflictError(
                "corpus registration does not match its principal-owned source slot",
                details={"reason": "registration_mismatch"},
            )
        return corpus

    def _generation(
        self,
        corpus_descriptor: int,
        corpus_root: Path,
    ) -> dict[str, Any]:
        epoch = _read_source_sync_epoch(
            self.service.data_root,
            self.source_slot.name,
        )
        return _read_generation(
            corpus_descriptor,
            corpus_root,
            absent_generation=epoch["generation"],
            absent_manifest_sha256=epoch["manifest_sha256"],
        )

    @staticmethod
    def _incoming_name(corpus_id: str, operation_id: str) -> str:
        return f".{corpus_id}.{operation_id}.incoming"

    @staticmethod
    def _public_state(state: dict[str, Any]) -> dict[str, Any]:
        result = {
            "operation_id": state["operation_id"],
            "corpus_id": state["corpus_id"],
            "manifest_sha256": state["manifest_sha256"],
            "status": state["status"],
            "staged_file_count": len(state.get("staged_paths", [])),
            "file_count": state["file_count"],
            "indexed_batch_count": state["index_batch_cursor"],
            "index_batch_count": (
                state["file_count"] + SOURCE_SYNC_INDEX_BATCH_FILES - 1
            )
            // SOURCE_SYNC_INDEX_BATCH_FILES,
            "total_bytes": state["total_bytes"],
            "idempotent_replay": state.get("idempotent_replay", False),
            "recovery": state.get("recovery"),
        }
        if state.get("result") is not None:
            result["result"] = state["result"]
        return result

    def _load_operation(
        self,
        operation_descriptor: int,
        operation_path: Path,
        operation_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        return _read_durable_operation(
            operation_descriptor,
            operation_path,
            operation_id,
        )

    def _save_state(
        self,
        operation_descriptor: int,
        state: dict[str, Any],
    ) -> None:
        state["updated_at"] = utc_now()
        _atomic_json_write(operation_descriptor, "state.json", state)

    @staticmethod
    def _ensure_staging_reservation(
        corpus_descriptor: int,
        generation: dict[str, Any],
        state: dict[str, Any],
    ) -> None:
        operation_id = state["operation_id"]
        active_operation = generation.get("active_operation_id")
        if active_operation == operation_id:
            if generation.get("manifest_sha256") != state["manifest_sha256"]:
                raise SourceSyncConflictError(
                    "active source reservation has a different manifest",
                    details={"reason": "active_reservation_mismatch"},
                )
            return
        if active_operation is not None:
            raise SourceSyncConflictError(
                "another source operation owns the corpus reservation",
                details={"reason": "active_source_operation"},
            )
        if (
            generation["generation"] != state["base_generation"]
            or generation["manifest_sha256"] != state["base_manifest_sha256"]
        ):
            raise SourceSyncConflictError(
                "source generation changed before upload reservation",
                details={"reason": "source_generation_changed"},
            )
        _write_generation(
            corpus_descriptor,
            {
                "format": SOURCE_SYNC_GENERATION_FORMAT,
                "generation": state["base_generation"],
                "manifest_sha256": state["manifest_sha256"],
                "operation_id": operation_id,
                "active_operation_id": operation_id,
                "index_state": "staging_reserved",
                "reserved_bytes": state["total_bytes"],
                "committed_at": None,
                "updated_at": utc_now(),
            },
        )

    @staticmethod
    def _require_terminal_generation(
        generation: dict[str, Any],
        state: dict[str, Any],
    ) -> None:
        if (
            generation.get("operation_id") != state["operation_id"]
            or generation.get("manifest_sha256") != state["manifest_sha256"]
            or generation.get("generation") != state["base_generation"] + 1
        ):
            raise SourceSyncConflictError(
                "completed source sync operation is not the current generation",
                details={"reason": "completed_operation_not_current"},
            )
        if state["status"] == "applied":
            if generation.get("active_operation_id") not in {
                None,
                state["operation_id"],
            } or generation.get("index_state") not in {
                "index_pending",
                "index_failed",
                "indexed",
            }:
                raise SourceSyncConflictError(
                    "completed source sync generation state is inconsistent",
                    details={"reason": "completed_generation_inconsistent"},
                )
        elif generation.get("active_operation_id") != state["operation_id"] or generation.get(
            "index_state"
        ) not in {"index_pending", "index_failed"}:
            raise SourceSyncConflictError(
                "source cleanup receipt is not the active generation",
                details={"reason": "cleanup_generation_inconsistent"},
            )

    def _finalize_applied_operation(
        self,
        *,
        corpus_descriptor: int,
        corpus_root: Path,
        state: dict[str, Any],
    ) -> None:
        generation = self._generation(corpus_descriptor, corpus_root)
        self._require_terminal_generation(generation, state)
        if generation.get("active_operation_id") is None:
            if generation.get("index_state") != "indexed" or generation.get("reserved_bytes") != 0:
                raise SourceSyncConflictError(
                    "completed source generation receipt is not settled",
                    details={"reason": "completed_generation_inconsistent"},
                )
            return
        _prune_completed_operations(
            corpus_descriptor,
            corpus_root,
            keep_operation_id=state["operation_id"],
        )
        generation["active_operation_id"] = None
        generation["index_state"] = "indexed"
        generation["reserved_bytes"] = 0
        _write_generation(corpus_descriptor, generation)

    def _initial_state(
        self,
        *,
        canonical: dict[str, Any],
        manifest_sha256: str,
        operation_id: str,
        idempotency_key_sha256: str,
        incoming_name: str,
        generation: dict[str, Any],
        registration: dict[str, Any] | None,
    ) -> dict[str, Any]:
        now = utc_now()
        return {
            "format": SOURCE_SYNC_STATE_FORMAT,
            "operation_id": operation_id,
            "idempotency_key_sha256": idempotency_key_sha256,
            "corpus_id": canonical["corpus_id"],
            "manifest_sha256": manifest_sha256,
            "file_count": len(canonical["files"]),
            "total_bytes": canonical["total_bytes"],
            "incoming_name": incoming_name,
            "index_batch_cursor": 0,
            "index_batch_plan_sha256": None,
            "status": "staging",
            "staged_paths": [],
            "source_installed": False,
            "previous_slot_preserved": False,
            "registration_state": ("verified" if registration is not None else "absent"),
            "registration_existed_at_begin": registration is not None,
            "base_generation": generation["generation"],
            "base_manifest_sha256": generation["manifest_sha256"],
            "base_generation_receipt": generation,
            "recovery": None,
            "result": None,
            "created_at": now,
            "updated_at": now,
        }

    def _recover_incomplete_begin(
        self,
        *,
        operation_descriptor: int,
        operation_path: Path,
        canonical: dict[str, Any],
        manifest_sha256: str,
        operation_id: str,
        idempotency_key_sha256: str,
        slot: Path,
        generation: dict[str, Any],
        registration: dict[str, Any] | None,
    ) -> dict[str, Any]:
        try:
            os.stat(
                "state.json",
                dir_fd=operation_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            pass
        else:
            raise SourceSyncConflictError(
                "source sync state exists but is not valid",
                details={"reason": "durable_state_invalid"},
            )
        for name in os.listdir(operation_descriptor):
            if name == "manifest.json":
                stored = _read_private_json(
                    operation_descriptor,
                    name,
                    path=operation_path / name,
                    maximum_bytes=SOURCE_SYNC_MAX_MANIFEST_BYTES,
                )
                stored_canonical, stored_digest = canonical_source_sync_manifest(stored)
                if stored_digest != manifest_sha256 or stored_canonical != canonical:
                    raise SourceSyncConflictError(
                        "idempotency key was prepared with a different manifest",
                        details={"reason": "idempotency_payload_mismatch"},
                    )
                continue
            if name.startswith((".manifest.json.", ".state.json.")) and name.endswith(".tmp"):
                metadata = os.stat(
                    name,
                    dir_fd=operation_descriptor,
                    follow_symlinks=False,
                )
                _require_private_file_stat(metadata, relative_path="__durable_state__")
                os.unlink(name, dir_fd=operation_descriptor)
                continue
            raise SourceSyncConflictError(
                "incomplete source sync operation contains unexpected durable state",
                details={"reason": "unexpected_operation_state"},
            )

        incoming_name = self._incoming_name(canonical["corpus_id"], operation_id)
        with _opened_vault_parent(slot) as parent_descriptor:
            incoming_metadata = _slot_metadata(parent_descriptor, incoming_name)
            incoming_descriptor = _open_private_child_directory(
                parent_descriptor,
                incoming_name,
                relative_path="__incoming__",
                create=incoming_metadata is None,
            )
            try:
                if os.listdir(incoming_descriptor):
                    raise SourceSyncConflictError(
                        "incomplete source sync staging is not empty",
                        details={"reason": "ambiguous_incoming_state"},
                    )
            finally:
                os.close(incoming_descriptor)
            os.fsync(parent_descriptor)

        state = self._initial_state(
            canonical=canonical,
            manifest_sha256=manifest_sha256,
            operation_id=operation_id,
            idempotency_key_sha256=idempotency_key_sha256,
            incoming_name=incoming_name,
            generation=generation,
            registration=registration,
        )
        _atomic_json_write(operation_descriptor, "manifest.json", canonical)
        _atomic_json_write(operation_descriptor, "state.json", state)
        return state

    @staticmethod
    def _require_expected_digest(state: dict[str, Any], manifest_sha256: str) -> None:
        if manifest_sha256 != state["manifest_sha256"]:
            raise SourceSyncConflictError(
                "source sync manifest digest does not match the idempotent operation",
                details={"reason": "manifest_digest_mismatch"},
            )

    @staticmethod
    def _validate_expected_head(
        *,
        expected_generation: int,
        expected_manifest_sha256: str | None,
    ) -> None:
        if type(expected_generation) is not int or expected_generation < 0:
            raise SourceSyncValidationError(
                "expected source generation must be a non-negative integer"
            )
        if expected_manifest_sha256 is not None and (
            not isinstance(expected_manifest_sha256, str)
            or len(expected_manifest_sha256) != 64
            or any(character not in "0123456789abcdef" for character in expected_manifest_sha256)
        ):
            raise SourceSyncValidationError(
                "expected source manifest digest must be lowercase sha256 or null"
            )
        if expected_generation == 0 and expected_manifest_sha256 is not None:
            raise SourceSyncValidationError(
                "generation zero cannot have an expected source manifest digest"
            )
        if expected_generation > 0 and expected_manifest_sha256 is None:
            raise SourceSyncValidationError(
                "a positive expected source generation requires its manifest digest"
            )

    def _head_from_generation(
        self,
        *,
        corpus_descriptor: int,
        corpus_root: Path,
        generation: dict[str, Any],
    ) -> dict[str, Any]:
        """Project the installed source revision without exposing durable-state paths."""

        if generation["index_state"] != "staging_reserved":
            return {
                "generation": generation["generation"],
                "manifest_sha256": generation["manifest_sha256"],
                "index_state": generation["index_state"],
            }
        operation_id = generation.get("active_operation_id")
        if not isinstance(operation_id, str):
            raise SourceSyncRecoveryRequiredError(
                "source generation reservation is incomplete",
                details={"reason": "source_generation_state_invalid"},
            )
        operation_path = corpus_root / operation_id
        operation_descriptor = _open_operation_directory(
            corpus_descriptor,
            corpus_root,
            operation_id,
            create=False,
        )
        try:
            _manifest, state = self._load_operation(
                operation_descriptor,
                operation_path,
                operation_id,
            )
        finally:
            os.close(operation_descriptor)
        if (
            state["status"] not in {"staging", "ready", "cancelling"}
            or state["base_generation"] != generation["generation"]
            or state["manifest_sha256"] != generation["manifest_sha256"]
        ):
            raise SourceSyncRecoveryRequiredError(
                "source generation reservation does not match its operation",
                details={"reason": "source_generation_state_invalid"},
            )
        return {
            "generation": state["base_generation"],
            "manifest_sha256": state["base_manifest_sha256"],
            "index_state": generation["index_state"],
        }

    def head(self, *, corpus_id: str) -> dict[str, Any]:
        """Return the principal-bound source revision used for begin CAS."""

        corpus_id = self._require_request_corpus_id(corpus_id)
        require_no_remote_delete_intent(self.service.data_root, corpus_id)
        slot = self._require_slot(corpus_id)
        self._registration(corpus_id, slot)
        try:
            with _corpus_sync_lock(
                self.service.data_root,
                corpus_id,
                create=False,
            ) as (corpus_descriptor, corpus_root):
                generation = self._generation(corpus_descriptor, corpus_root)
                return self._head_from_generation(
                    corpus_descriptor=corpus_descriptor,
                    corpus_root=corpus_root,
                    generation=generation,
                )
        except CorpusError as exc:
            if exc.details.get("reason") not in {"missing", "missing_parent"}:
                raise
        epoch = _read_source_sync_epoch(self.service.data_root, corpus_id)
        return {
            "generation": epoch["generation"],
            "manifest_sha256": epoch["manifest_sha256"],
            "index_state": "deleted" if epoch["generation"] else "legacy_or_absent",
        }

    @staticmethod
    def _require_source_precondition(
        generation: dict[str, Any],
        *,
        expected_generation: int,
        expected_manifest_sha256: str | None,
    ) -> None:
        if generation.get("active_operation_id") is not None or generation["index_state"] not in {
            "deleted",
            "indexed",
            "legacy_or_absent",
        }:
            raise SourceSyncRecoveryRequiredError(
                "the current source generation must settle before a new upload begins",
                details={
                    "status": "recovery_required",
                    "reason": "active_source_operation",
                    "index_state": generation["index_state"],
                },
            )
        if (
            generation["generation"] != expected_generation
            or generation["manifest_sha256"] != expected_manifest_sha256
        ):
            raise SourceSyncConflictError(
                "source generation precondition failed",
                details={
                    "reason": "source_generation_precondition_failed",
                    "current_generation": generation["generation"],
                    "current_manifest_sha256": generation["manifest_sha256"],
                },
            )

    def begin(
        self,
        *,
        manifest: object,
        idempotency_key: str,
        expected_generation: int,
        expected_manifest_sha256: str | None,
    ) -> dict[str, Any]:
        canonical, manifest_sha256 = canonical_source_sync_manifest(manifest)
        self._validate_expected_head(
            expected_generation=expected_generation,
            expected_manifest_sha256=expected_manifest_sha256,
        )
        corpus_id = canonical["corpus_id"]
        require_no_remote_delete_intent(self.service.data_root, corpus_id)
        slot = self._require_slot(corpus_id)
        operation_id, idempotency_key_sha256 = _operation_id(idempotency_key)

        with _source_sync_begin_lock(
            self.service.data_root,
            corpus_id,
            operation_id=operation_id,
            requested_bytes=canonical["total_bytes"],
        ) as (
            corpus_descriptor,
            corpus_root,
        ):
            require_no_remote_delete_intent(self.service.data_root, corpus_id)
            registration = self._registration(corpus_id, slot)
            generation = self._generation(corpus_descriptor, corpus_root)
            active_operation = generation.get("active_operation_id")
            if (
                active_operation == operation_id
                and generation.get("manifest_sha256") != manifest_sha256
            ):
                raise SourceSyncConflictError(
                    "idempotency key was already used with a different manifest",
                    details={"reason": "active_reservation_mismatch"},
                )
            operation_path = corpus_root / operation_id
            try:
                existing_descriptor = _open_operation_directory(
                    corpus_descriptor,
                    corpus_root,
                    operation_id,
                    create=False,
                )
                existing = True
            except SourceSyncConflictError as exc:
                if exc.details.get("reason") != "unknown_idempotency_key":
                    raise
                self._require_source_precondition(
                    generation,
                    expected_generation=expected_generation,
                    expected_manifest_sha256=expected_manifest_sha256,
                )
                existing_descriptor = _open_operation_directory(
                    corpus_descriptor,
                    corpus_root,
                    operation_id,
                    create=True,
                )
                os.fsync(corpus_descriptor)
                existing = False
            with _descriptor_context(existing_descriptor) as operation_descriptor:
                if existing:
                    try:
                        stored_manifest, state = self._load_operation(
                            operation_descriptor,
                            operation_path,
                            operation_id,
                        )
                    except CorpusError:
                        if active_operation == operation_id:
                            raise SourceSyncRecoveryRequiredError(
                                "active source sync durable state needs operator repair",
                                details={"reason": "active_operation_state_invalid"},
                            ) from None
                        state = self._recover_incomplete_begin(
                            operation_descriptor=operation_descriptor,
                            operation_path=operation_path,
                            canonical=canonical,
                            manifest_sha256=manifest_sha256,
                            operation_id=operation_id,
                            idempotency_key_sha256=idempotency_key_sha256,
                            slot=slot,
                            generation=generation,
                            registration=registration,
                        )
                        self._ensure_staging_reservation(
                            corpus_descriptor,
                            generation,
                            state,
                        )
                        return self._public_state(state)
                    if (
                        state.get("idempotency_key_sha256") != idempotency_key_sha256
                        or state["manifest_sha256"] != manifest_sha256
                        or stored_manifest != canonical
                    ):
                        raise SourceSyncConflictError(
                            "idempotency key was already used with a different manifest",
                            details={"reason": "idempotency_payload_mismatch"},
                        )
                    if state["status"] in {"applied", "applied_cleanup_required"}:
                        if state["status"] == "applied":
                            self._finalize_applied_operation(
                                corpus_descriptor=corpus_descriptor,
                                corpus_root=corpus_root,
                                state=state,
                            )
                        else:
                            self._require_terminal_generation(generation, state)
                        return self._public_state({**state, "idempotent_replay": True})
                    if state["status"] == "cancelled":
                        return self._public_state({**state, "idempotent_replay": True})
                    self._ensure_staging_reservation(
                        corpus_descriptor,
                        generation,
                        state,
                    )
                    state = {**state, "idempotent_replay": True}
                    return self._public_state(state)

                incoming_name = self._incoming_name(corpus_id, operation_id)
                with _opened_vault_parent(slot) as parent_descriptor:
                    if _slot_metadata(parent_descriptor, incoming_name) is not None:
                        raise SourceSyncConflictError(
                            "source sync staging slot already exists without durable state",
                            details={"reason": "orphan_incoming_slot"},
                        )
                    incoming_descriptor = _open_private_child_directory(
                        parent_descriptor,
                        incoming_name,
                        relative_path="__incoming__",
                        create=True,
                    )
                    os.close(incoming_descriptor)
                    os.fsync(parent_descriptor)

                state = self._initial_state(
                    canonical=canonical,
                    manifest_sha256=manifest_sha256,
                    operation_id=operation_id,
                    idempotency_key_sha256=idempotency_key_sha256,
                    incoming_name=incoming_name,
                    generation=generation,
                    registration=registration,
                )
                try:
                    _atomic_json_write(operation_descriptor, "manifest.json", canonical)
                    _atomic_json_write(operation_descriptor, "state.json", state)
                    self._ensure_staging_reservation(
                        corpus_descriptor,
                        generation,
                        state,
                    )
                except Exception:
                    _restore_generation(
                        corpus_descriptor,
                        state["base_generation_receipt"],
                    )
                    with _opened_vault_parent(slot) as parent_descriptor:
                        try:
                            _safe_remove_tree(slot.parent / incoming_name)
                            os.fsync(parent_descriptor)
                        except FileNotFoundError:
                            pass
                    with suppress(FileNotFoundError):
                        _safe_remove_tree(operation_path)
                    raise
                return self._public_state(state)

    def _operation(
        self,
        *,
        idempotency_key: str,
    ) -> tuple[str, str]:
        return _operation_id(idempotency_key)

    def _require_request_corpus_id(self, corpus_id: str) -> str:
        try:
            normalized = normalize_corpus_id(corpus_id)
        except CorpusError as exc:
            raise SourceSyncValidationError("source sync corpus_id is invalid") from exc
        if corpus_id != normalized or self.source_slot.name != normalized:
            raise SourceSyncConflictError(
                "source sync request does not match its principal-owned corpus slot",
                details={"reason": "corpus_slot_mismatch"},
            )
        return normalized

    def stage_file(
        self,
        *,
        corpus_id: str,
        idempotency_key: str,
        manifest_sha256: str,
        relative_path: str,
        chunks: Iterable[bytes],
    ) -> dict[str, Any]:
        corpus_id = self._require_request_corpus_id(corpus_id)
        require_no_remote_delete_intent(self.service.data_root, corpus_id)
        slot = self._require_slot(corpus_id)
        operation_id, idempotency_key_sha256 = self._operation(idempotency_key=idempotency_key)
        relative_path = _canonical_relative_path(relative_path)
        with _corpus_sync_lock(
            self.service.data_root,
            corpus_id,
            create=False,
        ) as (
            corpus_descriptor,
            corpus_root,
        ):
            require_no_remote_delete_intent(self.service.data_root, corpus_id)
            operation_path = corpus_root / operation_id
            operation_descriptor = _open_operation_directory(
                corpus_descriptor,
                corpus_root,
                operation_id,
                create=False,
            )
            with _descriptor_context(operation_descriptor) as operation_descriptor:
                manifest, state = self._load_operation(
                    operation_descriptor,
                    operation_path,
                    operation_id,
                )
                if state["corpus_id"] != corpus_id:
                    raise SourceSyncConflictError(
                        "source sync operation belongs to a different corpus",
                        details={"reason": "operation_corpus_mismatch"},
                    )
                if state.get("idempotency_key_sha256") != idempotency_key_sha256:
                    raise SourceSyncConflictError(
                        "source sync idempotency key does not match durable state"
                    )
                self._require_expected_digest(state, manifest_sha256)
                if state["status"] == "cancelled":
                    raise SourceSyncConflictError(
                        "source sync operation was cancelled",
                        details={"reason": "source_sync_cancelled"},
                    )
                if state["status"] in {"applied", "applied_cleanup_required"}:
                    generation = self._generation(corpus_descriptor, corpus_root)
                    if state["status"] == "applied":
                        self._finalize_applied_operation(
                            corpus_descriptor=corpus_descriptor,
                            corpus_root=corpus_root,
                            state=state,
                        )
                    else:
                        self._require_terminal_generation(generation, state)
                    return {
                        **self._public_state({**state, "idempotent_replay": True}),
                        "relative_path": relative_path,
                    }
                if state["status"] not in {"staging", "ready"}:
                    raise SourceSyncRecoveryRequiredError(
                        "source sync is not accepting file bytes in its current state",
                        details={"status": state["status"]},
                    )
                files = {item["relative_path"]: item for item in manifest["files"]}
                expected = files.get(relative_path)
                if expected is None:
                    raise SourceSyncValidationError(
                        "staged path is not present in the canonical manifest",
                        details={"relative_path": relative_path},
                    )
                with _opened_vault_parent(slot) as parent_descriptor:
                    incoming_descriptor = _open_private_child_directory(
                        parent_descriptor,
                        state["incoming_name"],
                        relative_path="__incoming__",
                        create=False,
                    )
                    try:
                        file_parent, file_name = _open_file_parent(
                            incoming_descriptor,
                            relative_path,
                            create=True,
                        )
                        try:
                            if relative_path in state["staged_paths"]:
                                size, digest = _read_and_hash_regular_file(
                                    file_parent,
                                    file_name,
                                    relative_path=relative_path,
                                )
                                if size != expected["size"] or digest != expected["sha256"]:
                                    raise SourceSyncConflictError(
                                        "an already-staged file no longer matches its manifest",
                                        details={"relative_path": relative_path},
                                    )
                                return {
                                    "operation_id": operation_id,
                                    "manifest_sha256": manifest_sha256,
                                    "status": state["status"],
                                    "relative_path": relative_path,
                                    "staged_file_count": len(state["staged_paths"]),
                                    "file_count": state["file_count"],
                                    "idempotent_replay": True,
                                }
                            part_digest = hashlib.sha256(relative_path.encode()).hexdigest()
                            part_name = f".upload-{part_digest[:32]}.part"
                            try:
                                existing_part = os.stat(
                                    part_name,
                                    dir_fd=file_parent,
                                    follow_symlinks=False,
                                )
                            except FileNotFoundError:
                                pass
                            else:
                                _require_private_file_stat(
                                    existing_part,
                                    relative_path=relative_path,
                                )
                                os.unlink(part_name, dir_fd=file_parent)
                            descriptor = os.open(
                                part_name,
                                os.O_WRONLY
                                | os.O_CREAT
                                | os.O_EXCL
                                | getattr(os, "O_CLOEXEC", 0)
                                | getattr(os, "O_NOFOLLOW", 0),
                                0o600,
                                dir_fd=file_parent,
                            )
                            digest = hashlib.sha256()
                            observed = 0
                            try:
                                os.fchmod(descriptor, 0o600)
                                for chunk in chunks:
                                    if not isinstance(chunk, bytes):
                                        raise SourceSyncValidationError(
                                            "source sync chunks must be bytes",
                                            details={"relative_path": relative_path},
                                        )
                                    observed += len(chunk)
                                    if (
                                        observed > expected["size"]
                                        or observed > SOURCE_SYNC_MAX_FILE_BYTES
                                    ):
                                        raise SourceSyncValidationError(
                                            "staged file exceeds its declared byte size",
                                            details={"relative_path": relative_path},
                                        )
                                    _write_all(descriptor, chunk)
                                    digest.update(chunk)
                                os.fsync(descriptor)
                                staged_stat = os.fstat(descriptor)
                                _require_private_file_stat(
                                    staged_stat,
                                    relative_path=relative_path,
                                )
                                if (
                                    observed != expected["size"]
                                    or digest.hexdigest() != expected["sha256"]
                                ):
                                    raise SourceSyncConflictError(
                                        "staged file does not match its manifest size and digest",
                                        details={"relative_path": relative_path},
                                    )
                            except Exception:
                                os.close(descriptor)
                                descriptor = -1
                                with suppress(FileNotFoundError):
                                    os.unlink(part_name, dir_fd=file_parent)
                                raise
                            finally:
                                if descriptor >= 0:
                                    os.close(descriptor)
                            try:
                                final_metadata = os.stat(
                                    file_name,
                                    dir_fd=file_parent,
                                    follow_symlinks=False,
                                )
                            except FileNotFoundError:
                                final_metadata = None
                            if final_metadata is not None:
                                _require_private_file_stat(
                                    final_metadata,
                                    relative_path=relative_path,
                                )
                                size, final_digest = _read_and_hash_regular_file(
                                    file_parent,
                                    file_name,
                                    relative_path=relative_path,
                                )
                                if size != expected["size"] or final_digest != expected["sha256"]:
                                    raise SourceSyncConflictError(
                                        "staged destination already contains different bytes",
                                        details={"relative_path": relative_path},
                                    )
                                os.unlink(part_name, dir_fd=file_parent)
                            else:
                                os.rename(
                                    part_name,
                                    file_name,
                                    src_dir_fd=file_parent,
                                    dst_dir_fd=file_parent,
                                )
                            os.fsync(file_parent)
                        finally:
                            os.close(file_parent)
                    finally:
                        os.close(incoming_descriptor)
                state["staged_paths"] = sorted(
                    {*state["staged_paths"], relative_path},
                    key=lambda value: value.encode("utf-8"),
                )
                state["status"] = (
                    "ready" if len(state["staged_paths"]) == state["file_count"] else "staging"
                )
                state["recovery"] = None
                self._save_state(operation_descriptor, state)
                return {
                    "operation_id": operation_id,
                    "manifest_sha256": manifest_sha256,
                    "status": state["status"],
                    "relative_path": relative_path,
                    "staged_file_count": len(state["staged_paths"]),
                    "file_count": state["file_count"],
                    "idempotent_replay": False,
                }

    @staticmethod
    def _remaining_index_timeout(deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SourceSyncUnavailableError(
                "source sync server reindex exceeded its total deadline",
                details={"reason": "indexing_timeout"},
            )
        return min(remaining, SOURCE_SYNC_TIMEOUT_SECONDS)

    def _complete_inventory(self, corpus_id: str) -> dict[str, Any]:
        documents: list[dict[str, Any]] = []
        observation: dict[str, Any] | None = None
        total_matching: int | None = None
        offset = 0
        while True:
            page = self.service.inventory(
                corpus_id,
                eligibility_state="all",
                residency_state="resident",
                index_state="all",
                limit=SOURCE_SYNC_INVENTORY_PAGE_FILES,
                offset=offset,
            )
            if (
                page.get("offset") != offset
                or page.get("returned_count") != len(page.get("documents", []))
                or type(page.get("total_matching")) is not int
                or not 0 <= page["total_matching"] <= SOURCE_SYNC_MAX_FILES
            ):
                raise SourceSyncUnavailableError(
                    "server inventory page is not a bounded source snapshot",
                    details={"reason": "inventory_page_invalid"},
                )
            if total_matching is None:
                total_matching = page["total_matching"]
                observation = page.get("observation")
            elif (
                total_matching != page["total_matching"]
                or observation != page.get("observation")
            ):
                raise SourceSyncUnavailableError(
                    "server inventory changed while source indexing was verified",
                    details={"reason": "inventory_changed_during_verification"},
                )
            documents.extend(page["documents"])
            if len(documents) > SOURCE_SYNC_MAX_FILES:
                raise SourceSyncUnavailableError(
                    "server inventory exceeds the source generation bound",
                    details={"reason": "inventory_manifest_mismatch"},
                )
            if not page.get("has_more"):
                if page.get("next_offset") is not None:
                    raise SourceSyncUnavailableError(
                        "server inventory terminal page is inconsistent",
                        details={"reason": "inventory_page_invalid"},
                    )
                break
            next_offset = page.get("next_offset")
            if type(next_offset) is not int or next_offset != offset + len(
                page["documents"]
            ):
                raise SourceSyncUnavailableError(
                    "server inventory cursor is inconsistent",
                    details={"reason": "inventory_page_invalid"},
                )
            if next_offset <= offset:
                raise SourceSyncUnavailableError(
                    "server inventory pagination made no progress",
                    details={"reason": "inventory_no_progress"},
                )
            offset = next_offset
        if total_matching != len(documents):
            raise SourceSyncUnavailableError(
                "server inventory count is inconsistent",
                details={"reason": "inventory_page_invalid"},
            )
        return {
            "observation": observation,
            "total_matching": total_matching,
            "documents": documents,
        }

    @staticmethod
    def _manifest_inventory(
        manifest: dict[str, Any],
        inventory: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if inventory["total_matching"] != len(manifest["files"]):
            raise SourceSyncUnavailableError(
                "server inventory does not match the applied source manifest",
                details={"reason": "inventory_manifest_mismatch"},
            )
        by_path: dict[str, dict[str, Any]] = {}
        document_ids: set[str] = set()
        for document in inventory["documents"]:
            relative_path = document.get("relative_path")
            document_id = document.get("document_id")
            if (
                not isinstance(relative_path, str)
                or relative_path in by_path
                or not isinstance(document_id, str)
                or not document_id
                or document_id in document_ids
            ):
                raise SourceSyncUnavailableError(
                    "server inventory identity is ambiguous",
                    details={"reason": "inventory_manifest_mismatch"},
                )
            by_path[relative_path] = document
            document_ids.add(document_id)

        ordered = []
        for expected in manifest["files"]:
            document = by_path.get(expected["relative_path"])
            if (
                document is None
                or document.get("logical_size") != expected["size"]
                or document.get("eligibility_state") != "supported"
                or document.get("residency_state") != "resident"
                or document.get("index_state")
                not in {"current", "refresh_required", "unindexed"}
            ):
                raise SourceSyncUnavailableError(
                    "server inventory does not match the applied source manifest",
                    details={"reason": "inventory_manifest_mismatch"},
                )
            ordered.append(document)
        if set(by_path) != {item["relative_path"] for item in manifest["files"]}:
            raise SourceSyncUnavailableError(
                "server inventory contains a source outside the applied manifest",
                details={"reason": "inventory_manifest_mismatch"},
            )
        return ordered

    @staticmethod
    def _empty_ingest_aggregate() -> dict[str, Any]:
        return {
            "selected_files": 0,
            "selected_logical_bytes": 0,
            "skipped": {},
            "summary": {
                "indexed": 0,
                "already_indexed": 0,
                "failed": 0,
                "source_copy_cleanup_failed": 0,
            },
        }

    @staticmethod
    def _add_ingest_result(
        aggregate: dict[str, Any],
        ingest: dict[str, Any],
    ) -> None:
        aggregate["selected_files"] += ingest["selected_files"]
        aggregate["selected_logical_bytes"] += ingest["selected_logical_bytes"]
        for key, count in ingest["skipped"].items():
            aggregate["skipped"][key] = aggregate["skipped"].get(key, 0) + count
        for key, count in ingest["summary"].items():
            aggregate["summary"][key] = aggregate["summary"].get(key, 0) + count

    def _index_manifest_batches(
        self,
        *,
        manifest: dict[str, Any],
        state: dict[str, Any],
        operation_descriptor: int,
        deadline: float,
    ) -> tuple[dict[str, Any], dict[str, Any], bool]:
        batches = _source_sync_index_batches(manifest)
        receipts = _source_sync_index_batch_receipts(manifest)
        plan_sha256 = _source_sync_index_plan_sha256(manifest)
        if state["index_batch_plan_sha256"] is None:
            state["index_batch_plan_sha256"] = plan_sha256
            state["index_batch_cursor"] = 0
            self._save_state(operation_descriptor, state)
        elif state["index_batch_plan_sha256"] != plan_sha256:
            raise SourceSyncConflictError(
                "source sync index batch plan changed across a retry",
                details={"reason": "index_batch_plan_changed"},
            )

        aggregate = self._empty_ingest_aggregate()
        inventory = self._complete_inventory(state["corpus_id"])
        ordered_documents = self._manifest_inventory(manifest, inventory)
        first_incomplete = len(batches)
        for batch_index, batch in enumerate(batches):
            offset = batch_index * SOURCE_SYNC_INDEX_BATCH_FILES
            documents = ordered_documents[offset : offset + len(batch)]
            if any(document["index_state"] != "current" for document in documents):
                first_incomplete = batch_index
                break
        if state["index_batch_cursor"] > first_incomplete:
            state["index_batch_cursor"] = first_incomplete
            self._save_state(operation_descriptor, state)

        indexed_batch_this_call = False
        for batch_index in range(state["index_batch_cursor"], len(batches)):
            batch = batches[batch_index]
            offset = batch_index * SOURCE_SYNC_INDEX_BATCH_FILES
            documents = ordered_documents[offset : offset + len(batch)]
            pending = [
                document for document in documents if document["index_state"] != "current"
            ]
            if pending:
                remaining_seconds = self._remaining_index_timeout(deadline)
                ingest = self.service.ingest(
                    state["corpus_id"],
                    document_ids=[document["document_id"] for document in pending],
                    max_files=len(pending),
                    max_bytes=max(
                        1,
                        sum(document["logical_size"] for document in pending),
                    ),
                    max_file_bytes=max(
                        1,
                        max(document["logical_size"] for document in pending),
                    ),
                    include_remote=False,
                    timeout_seconds=remaining_seconds / len(pending),
                )
                successful = (
                    ingest["summary"].get("indexed", 0)
                    + ingest["summary"].get("already_indexed", 0)
                )
                rejected = sum(ingest["skipped"].values())
                if (
                    ingest["selected_files"] != len(pending)
                    or successful != len(pending)
                    or ingest["summary"].get("failed", 0)
                    or ingest["summary"].get("source_copy_cleanup_failed", 0)
                    or rejected
                ):
                    raise SourceSyncUnavailableError(
                        "a source sync index batch did not complete exactly",
                        details={"reason": "index_batch_incomplete"},
                    )
                self._add_ingest_result(aggregate, ingest)
                indexed_batch_this_call = True

            self._remaining_index_timeout(deadline)
            inventory = self._complete_inventory(state["corpus_id"])
            ordered_documents = self._manifest_inventory(manifest, inventory)
            documents = ordered_documents[offset : offset + len(batch)]
            if not documents and batch:
                raise SourceSyncUnavailableError(
                    "source sync index batch made no progress",
                    details={"reason": "index_batch_no_progress"},
                )
            if any(document["index_state"] != "current" for document in documents):
                raise SourceSyncUnavailableError(
                    "source sync index batch remained incomplete",
                    details={"reason": "index_batch_incomplete"},
                )
            state["index_batch_cursor"] = batch_index + 1
            self._save_state(operation_descriptor, state)
            if indexed_batch_this_call and state["index_batch_cursor"] < len(batches):
                return (
                    {
                        **inventory,
                        "documents": ordered_documents,
                    },
                    {
                        **aggregate,
                        "batch_count": len(batches),
                        "batch_plan_sha256": plan_sha256,
                        "batches": receipts,
                    },
                    False,
                )

        self._remaining_index_timeout(deadline)
        final_document_ids = (
            [ordered_documents[0]["document_id"]] if ordered_documents else None
        )
        publication = self.service.ingest(
            state["corpus_id"],
            document_ids=final_document_ids,
            max_files=1,
            max_bytes=1,
            max_file_bytes=1,
            include_remote=False,
            timeout_seconds=self._remaining_index_timeout(deadline),
        )
        if publication["selected_files"] or publication["summary"].get("failed", 0):
            raise SourceSyncUnavailableError(
                "final source snapshot was not published from a settled inventory",
                details={"reason": "final_snapshot_unsettled"},
            )
        inventory = self._complete_inventory(state["corpus_id"])
        ordered_documents = self._manifest_inventory(manifest, inventory)
        observation = inventory.get("observation") or {}
        if (
            any(document["index_state"] != "current" for document in ordered_documents)
            or not observation.get("inventory_complete")
            or not observation.get("current_snapshot_id")
            or observation.get("snapshot_coverage_state") != "complete"
        ):
            raise SourceSyncUnavailableError(
                "final source snapshot does not exactly cover the manifest",
                details={"reason": "final_snapshot_incomplete"},
            )
        state["index_batch_cursor"] = len(batches)
        self._save_state(operation_descriptor, state)
        return (
            {
                **inventory,
                "documents": ordered_documents,
            },
            {
                **aggregate,
                "batch_count": len(batches),
                "batch_plan_sha256": plan_sha256,
                "batches": receipts,
            },
            True,
        )

    @staticmethod
    def _sanitized_index_result(
        manifest: dict[str, Any],
        scan: dict[str, Any],
        ingest: dict[str, Any],
        inventory: dict[str, Any],
    ) -> dict[str, Any]:
        documents = [
            {
                "document_id": document["document_id"],
                "relative_path": document["relative_path"],
                "logical_size": document["logical_size"],
                "eligibility_state": document["eligibility_state"],
                "residency_state": document["residency_state"],
                "index_state": document["index_state"],
            }
            for document in inventory["documents"][:SOURCE_SYNC_RESULT_DOCUMENTS]
        ]
        return {
            "manifest": {
                "file_count": len(manifest["files"]),
                "total_bytes": manifest["total_bytes"],
            },
            "inventory": {
                "scan_id": scan["scan_id"],
                "observation_complete": scan["observation_complete"],
                "files": scan["files"],
                "logical_bytes": scan["logical_bytes"],
                "eligibility_counts": scan["eligibility_counts"],
                "change_counts": scan["change_counts"],
                "total_matching": inventory["total_matching"],
                "returned_document_count": len(documents),
                "documents_truncated": len(inventory["documents"]) > len(documents),
                "documents": documents,
            },
            "index": {
                "settled_file_count": len(manifest["files"]),
                "settled_logical_bytes": manifest["total_bytes"],
                "indexed_batch_count": ingest["batch_count"],
                "batch_count": ingest["batch_count"],
                "batch_plan_sha256": ingest["batch_plan_sha256"],
                "batches": ingest["batches"],
                "final_call": {
                    "selected_files": ingest["selected_files"],
                    "selected_logical_bytes": ingest["selected_logical_bytes"],
                    "skipped": ingest["skipped"],
                    "summary": ingest["summary"],
                },
            },
        }

    def _mark_recovery_required(
        self,
        operation_descriptor: int,
        state: dict[str, Any],
        *,
        reason: str,
        next_action: str,
    ) -> None:
        state["status"] = "recovery_required"
        state["recovery"] = {
            "required": True,
            "reason": reason,
            "next_action": next_action,
            "source_slot_state": (
                "new_manifest_installed" if state.get("source_installed") else "unchanged"
            ),
            "registration_state": state.get("registration_state"),
        }
        self._save_state(operation_descriptor, state)

    def _rollback_unregistered_install(
        self,
        *,
        slot: Path,
        parent_descriptor: int,
        state: dict[str, Any],
    ) -> None:
        incoming_name = state["incoming_name"]
        if state.get("previous_slot_preserved"):
            _atomic_exchange_at(parent_descriptor, slot.name, incoming_name)
        else:
            os.rename(
                slot.name,
                incoming_name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
            )
            os.fsync(parent_descriptor)
        state["source_installed"] = False
        state["previous_slot_preserved"] = False
        state["status"] = "ready"
        state["recovery"] = {
            "required": False,
            "reason": "registration_failed_source_rolled_back",
            "next_action": "retry_apply",
            "source_slot_state": "previous_state_restored",
            "registration_state": "absent",
        }

    def cancel(
        self,
        *,
        corpus_id: str,
        idempotency_key: str,
        manifest_sha256: str,
    ) -> dict[str, Any]:
        """Cancel an exact upload only while no source generation was installed."""

        corpus_id = self._require_request_corpus_id(corpus_id)
        require_no_remote_delete_intent(self.service.data_root, corpus_id)
        slot = self._require_slot(corpus_id)
        operation_id, idempotency_key_sha256 = self._operation(idempotency_key=idempotency_key)
        with _source_sync_cancel_lock(self.service.data_root, corpus_id) as (
            corpus_descriptor,
            corpus_root,
        ):
            require_no_remote_delete_intent(self.service.data_root, corpus_id)
            operation_path = corpus_root / operation_id
            operation_descriptor = _open_operation_directory(
                corpus_descriptor,
                corpus_root,
                operation_id,
                create=False,
            )
            with _descriptor_context(operation_descriptor) as operation_descriptor:
                _manifest, state = self._load_operation(
                    operation_descriptor,
                    operation_path,
                    operation_id,
                )
                if state["corpus_id"] != corpus_id:
                    raise SourceSyncConflictError(
                        "source sync operation belongs to a different corpus",
                        details={"reason": "operation_corpus_mismatch"},
                    )
                if state.get("idempotency_key_sha256") != idempotency_key_sha256:
                    raise SourceSyncConflictError(
                        "source sync idempotency key does not match durable state"
                    )
                self._require_expected_digest(state, manifest_sha256)
                if state["status"] == "cancelled":
                    _prune_completed_operations(
                        corpus_descriptor,
                        corpus_root,
                        keep_operation_id=operation_id,
                    )
                    return self._public_state({**state, "idempotent_replay": True})
                if (
                    state["status"] not in {"staging", "ready", "cancelling"}
                    or state["source_installed"]
                    or state["previous_slot_preserved"]
                    or state["result"] is not None
                ):
                    raise SourceSyncConflictError(
                        "source sync can be cancelled only before source installation",
                        details={"reason": "source_sync_not_cancellable"},
                    )

                generation = self._generation(corpus_descriptor, corpus_root)
                active_operation = generation.get("active_operation_id")
                if active_operation == operation_id:
                    if (
                        generation.get("manifest_sha256") != manifest_sha256
                        or generation.get("index_state") != "staging_reserved"
                    ):
                        raise SourceSyncConflictError(
                            "active source reservation does not match this cancellation",
                            details={"reason": "active_reservation_mismatch"},
                        )
                elif active_operation is not None or (
                    generation["generation"] != state["base_generation"]
                    or generation["manifest_sha256"] != state["base_manifest_sha256"]
                ):
                    raise SourceSyncConflictError(
                        "source generation changed before cancellation",
                        details={"reason": "source_generation_changed"},
                    )

                if state["status"] != "cancelling":
                    state["status"] = "cancelling"
                    state["recovery"] = {
                        "required": False,
                        "reason": "source_upload_cancel_pending",
                        "next_action": "retry_cancel",
                        "source_slot_state": "unchanged",
                        "registration_state": state["registration_state"],
                    }
                    self._save_state(operation_descriptor, state)

                with _opened_vault_parent(slot) as parent_descriptor:
                    if _slot_metadata(parent_descriptor, state["incoming_name"]) is not None:
                        _safe_remove_tree(slot.parent / state["incoming_name"])
                        os.fsync(parent_descriptor)
                if active_operation == operation_id:
                    _restore_generation(
                        corpus_descriptor,
                        state["base_generation_receipt"],
                    )
                state["status"] = "cancelled"
                state["staged_paths"] = []
                state["recovery"] = None
                self._save_state(operation_descriptor, state)
                _prune_completed_operations(
                    corpus_descriptor,
                    corpus_root,
                    keep_operation_id=operation_id,
                )
                return self._public_state(state)

    def apply(
        self,
        *,
        corpus_id: str,
        idempotency_key: str,
        manifest_sha256: str,
        timeout_seconds: float = SOURCE_SYNC_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        if not 0 < timeout_seconds <= SOURCE_SYNC_TIMEOUT_SECONDS:
            raise SourceSyncValidationError(
                "source sync indexing timeout exceeds its bound",
                details={"maximum_seconds": SOURCE_SYNC_TIMEOUT_SECONDS},
            )
        deadline = time.monotonic() + timeout_seconds
        corpus_id = self._require_request_corpus_id(corpus_id)
        require_no_remote_delete_intent(self.service.data_root, corpus_id)
        slot = self._require_slot(corpus_id)
        operation_id, idempotency_key_sha256 = self._operation(idempotency_key=idempotency_key)
        with _corpus_sync_lock(
            self.service.data_root,
            corpus_id,
            create=False,
        ) as (
            corpus_descriptor,
            corpus_root,
        ):
            require_no_remote_delete_intent(self.service.data_root, corpus_id)
            operation_path = corpus_root / operation_id
            operation_descriptor = _open_operation_directory(
                corpus_descriptor,
                corpus_root,
                operation_id,
                create=False,
            )
            with (
                _descriptor_context(operation_descriptor) as operation_descriptor,
                context_writer_lock(self.service.data_root),
            ):
                manifest, state = self._load_operation(
                    operation_descriptor,
                    operation_path,
                    operation_id,
                )
                if state["corpus_id"] != corpus_id:
                    raise SourceSyncConflictError(
                        "source sync operation belongs to a different corpus",
                        details={"reason": "operation_corpus_mismatch"},
                    )
                if state.get("idempotency_key_sha256") != idempotency_key_sha256:
                    raise SourceSyncConflictError(
                        "source sync idempotency key does not match durable state"
                    )
                self._require_expected_digest(state, manifest_sha256)
                if state["status"] == "cancelled":
                    raise SourceSyncConflictError(
                        "source sync operation was cancelled",
                        details={"reason": "source_sync_cancelled"},
                    )
                if state["status"] == "applied":
                    self._finalize_applied_operation(
                        corpus_descriptor=corpus_descriptor,
                        corpus_root=corpus_root,
                        state=state,
                    )
                    return self._public_state({**state, "idempotent_replay": True})
                if state["status"] == "applied_cleanup_required":
                    generation = self._generation(corpus_descriptor, corpus_root)
                    self._require_terminal_generation(generation, state)
                    cleanup_path = slot.parent / state["incoming_name"]
                    try:
                        _safe_remove_tree(cleanup_path)
                    except FileNotFoundError:
                        pass
                    except Exception:
                        return self._public_state({**state, "idempotent_replay": True})
                    state["status"] = "applied"
                    state["recovery"] = None
                    self._save_state(operation_descriptor, state)
                    self._finalize_applied_operation(
                        corpus_descriptor=corpus_descriptor,
                        corpus_root=corpus_root,
                        state=state,
                    )
                    return self._public_state({**state, "idempotent_replay": True})

                generation = self._generation(corpus_descriptor, corpus_root)
                active_operation = generation.get("active_operation_id")
                committed_for_operation = (
                    generation.get("operation_id") == operation_id
                    and generation.get("manifest_sha256") == manifest_sha256
                    and generation.get("index_state")
                    in {"index_pending", "index_failed", "indexed"}
                )
                if active_operation not in {None, operation_id}:
                    raise SourceSyncConflictError(
                        "another source generation became active after this upload began",
                        details={"reason": "source_generation_changed"},
                    )
                if state.get("source_installed"):
                    if not committed_for_operation and active_operation != operation_id:
                        raise SourceSyncConflictError(
                            "installed source generation no longer matches this operation",
                            details={"reason": "source_generation_changed"},
                        )
                elif active_operation != operation_id and (
                    generation["generation"] != state["base_generation"]
                    or generation["manifest_sha256"] != state["base_manifest_sha256"]
                ):
                    raise SourceSyncConflictError(
                        "source generation changed after this upload began",
                        details={"reason": "source_generation_changed"},
                    )

                registration = self._registration(state["corpus_id"], slot)
                if registration is None and state["registration_state"] in {
                    "verified",
                    "registered",
                }:
                    raise SourceSyncConflictError(
                        "corpus registration was removed after this upload began",
                        details={"reason": "registration_removed"},
                    )
                if (
                    registration is not None
                    and state["registration_state"] == "absent"
                    and not state.get("source_installed")
                ):
                    raise SourceSyncConflictError(
                        "corpus registration was created by another operation",
                        details={"reason": "registration_created_concurrently"},
                    )
                if len(state["staged_paths"]) != state["file_count"]:
                    raise SourceSyncConflictError(
                        "source sync apply requires every manifest file",
                        details={
                            "reason": "files_missing",
                            "staged_file_count": len(state["staged_paths"]),
                            "file_count": state["file_count"],
                        },
                    )

                incoming_name = state["incoming_name"]
                with _opened_vault_parent(slot) as parent_descriptor:
                    slot_metadata = _slot_metadata(parent_descriptor, slot.name)
                    incoming_metadata = _slot_metadata(parent_descriptor, incoming_name)
                    slot_matches = False
                    incoming_matches = False
                    if slot_metadata is not None:
                        slot_descriptor = _open_private_child_directory(
                            parent_descriptor,
                            slot.name,
                            relative_path="__slot__",
                            create=False,
                        )
                        try:
                            slot_matches, _slot_observation = _tree_observation(
                                slot_descriptor,
                                manifest,
                            )
                        finally:
                            os.close(slot_descriptor)
                    if incoming_metadata is not None:
                        incoming_descriptor = _open_private_child_directory(
                            parent_descriptor,
                            incoming_name,
                            relative_path="__incoming__",
                            create=False,
                        )
                        try:
                            incoming_matches, incoming_observation = _tree_observation(
                                incoming_descriptor,
                                manifest,
                            )
                        finally:
                            os.close(incoming_descriptor)
                    else:
                        incoming_observation = {"reason": "incoming_missing"}

                    if not slot_matches:
                        if not incoming_matches:
                            self._mark_recovery_required(
                                operation_descriptor,
                                state,
                                reason=incoming_observation["reason"],
                                next_action="restage_or_operator_repair",
                            )
                            raise SourceSyncRecoveryRequiredError(
                                "source sync staging tree no longer matches its manifest",
                                details={"status": "recovery_required"},
                            )
                        if (
                            active_operation != operation_id
                            or generation.get("index_state") != "swap_pending"
                        ):
                            generation = {
                                "format": SOURCE_SYNC_GENERATION_FORMAT,
                                "generation": state["base_generation"],
                                "manifest_sha256": manifest_sha256,
                                "operation_id": operation_id,
                                "active_operation_id": operation_id,
                                "index_state": "swap_pending",
                                "reserved_bytes": state["total_bytes"],
                                "committed_at": None,
                                "updated_at": utc_now(),
                            }
                            _write_generation(corpus_descriptor, generation)
                            active_operation = operation_id
                        state["status"] = "applying"
                        state["recovery"] = {
                            "required": False,
                            "reason": "source_exchange_pending",
                            "next_action": "retry_apply",
                            "source_slot_state": "unchanged",
                            "registration_state": state["registration_state"],
                        }
                        self._save_state(operation_descriptor, state)
                        try:
                            if slot_metadata is None:
                                os.rename(
                                    incoming_name,
                                    slot.name,
                                    src_dir_fd=parent_descriptor,
                                    dst_dir_fd=parent_descriptor,
                                )
                                os.fsync(parent_descriptor)
                                state["previous_slot_preserved"] = False
                            else:
                                if not shutil.rmtree.avoids_symlink_attacks:
                                    raise SourceSyncUnavailableError(
                                        "this host cannot safely retire the previous source slot",
                                        details={"reason": "safe_tree_removal_unavailable"},
                                    )
                                _atomic_exchange_at(
                                    parent_descriptor,
                                    slot.name,
                                    incoming_name,
                                )
                                state["previous_slot_preserved"] = True
                        except Exception as exc:
                            self._mark_recovery_required(
                                operation_descriptor,
                                state,
                                reason="source_commit_uncertain",
                                next_action="retry_apply",
                            )
                            raise SourceSyncRecoveryRequiredError(
                                "source slot commit must be reconciled by retrying apply",
                                details={"status": "recovery_required"},
                            ) from exc
                        state["source_installed"] = True
                        state["status"] = "source_installed"
                        state["recovery"] = {
                            "required": False,
                            "reason": "server_reindex_pending",
                            "next_action": "retry_apply",
                            "source_slot_state": "new_manifest_installed",
                            "registration_state": state["registration_state"],
                        }
                        self._save_state(operation_descriptor, state)
                    else:
                        state["source_installed"] = True
                        if incoming_metadata is not None and not incoming_matches:
                            state["previous_slot_preserved"] = True
                        state["status"] = "source_installed"
                        self._save_state(operation_descriptor, state)

                    if not committed_for_operation:
                        generation = {
                            "format": SOURCE_SYNC_GENERATION_FORMAT,
                            "generation": state["base_generation"] + 1,
                            "manifest_sha256": manifest_sha256,
                            "operation_id": operation_id,
                            "active_operation_id": operation_id,
                            "index_state": "index_pending",
                            "reserved_bytes": state["total_bytes"],
                            "committed_at": utc_now(),
                            "updated_at": utc_now(),
                        }
                        _write_generation(corpus_descriptor, generation)
                        committed_for_operation = True

                    registration = self._registration(state["corpus_id"], slot)
                    if registration is None:
                        try:
                            registration = self.service.register(
                                corpus_id=state["corpus_id"],
                                source_root=slot,
                                execution_policy="external_host_allowed",
                                provider_kind="filesystem",
                            )
                        except Exception as exc:
                            try:
                                registration = self._registration(state["corpus_id"], slot)
                            except CorpusError:
                                registration = None
                            if registration is None:
                                self._rollback_unregistered_install(
                                    slot=slot,
                                    parent_descriptor=parent_descriptor,
                                    state=state,
                                )
                                _restore_generation(
                                    corpus_descriptor,
                                    state["base_generation_receipt"],
                                )
                                self._save_state(operation_descriptor, state)
                                raise SourceSyncUnavailableError(
                                    "source sync registration failed and the source "
                                    "slot was restored",
                                    details={"recovery_state": "rolled_back"},
                                ) from exc
                            state["registration_state"] = "registered"
                            self._mark_recovery_required(
                                operation_descriptor,
                                state,
                                reason="registration_completion_uncertain",
                                next_action="retry_apply",
                            )
                            raise SourceSyncRecoveryRequiredError(
                                "source registration needs a converging apply retry",
                                details={"status": "recovery_required"},
                            ) from exc
                        state["registration_state"] = "registered"
                    else:
                        state["registration_state"] = "verified"
                    self._save_state(operation_descriptor, state)

                try:
                    self._remaining_index_timeout(deadline)
                    scan = self.service.scan(state["corpus_id"])
                    if not scan["observation_complete"]:
                        raise SourceSyncUnavailableError(
                            "server inventory scan was incomplete",
                            details={"reason": "incomplete_scan"},
                        )
                    inventory, ingest, indexing_complete = self._index_manifest_batches(
                        manifest=manifest,
                        state=state,
                        operation_descriptor=operation_descriptor,
                        deadline=deadline,
                    )
                    if not indexing_complete:
                        state["status"] = "indexing"
                        state["recovery"] = {
                            "required": False,
                            "reason": "server_reindex_in_progress",
                            "next_action": "retry_apply",
                            "source_slot_state": "new_manifest_installed",
                            "registration_state": state["registration_state"],
                        }
                        self._save_state(operation_descriptor, state)
                        return self._public_state(state)
                    result = self._sanitized_index_result(
                        manifest,
                        scan,
                        ingest,
                        inventory,
                    )
                except Exception as exc:
                    generation = self._generation(corpus_descriptor, corpus_root)
                    if (
                        generation.get("operation_id") == operation_id
                        and generation.get("manifest_sha256") == manifest_sha256
                    ):
                        generation["active_operation_id"] = operation_id
                        generation["index_state"] = "index_failed"
                        _write_generation(corpus_descriptor, generation)
                    self._mark_recovery_required(
                        operation_descriptor,
                        state,
                        reason=(
                            exc.details.get("reason", exc.code)
                            if isinstance(exc, CorpusError)
                            else "server_reindex_failed"
                        ),
                        next_action="retry_apply",
                    )
                    raise SourceSyncRecoveryRequiredError(
                        "source manifest is installed but server reindexing must be retried",
                        details={"status": "recovery_required"},
                    ) from exc

                generation = self._generation(corpus_descriptor, corpus_root)
                if (
                    generation.get("operation_id") != operation_id
                    or generation.get("manifest_sha256") != manifest_sha256
                ):
                    raise SourceSyncConflictError(
                        "source generation changed before indexing completed",
                        details={"reason": "source_generation_changed"},
                    )
                result["source_revision"] = {
                    "generation": generation["generation"],
                    "manifest_sha256": manifest_sha256,
                    "index_state": "indexed",
                }
                state["result"] = result
                cleanup_path = slot.parent / incoming_name
                try:
                    if cleanup_path.exists():
                        _safe_remove_tree(cleanup_path)
                        with _opened_vault_parent(slot) as parent_descriptor:
                            os.fsync(parent_descriptor)
                except Exception:
                    state["status"] = "applied_cleanup_required"
                    state["recovery"] = {
                        "required": True,
                        "reason": "previous_slot_cleanup_failed",
                        "next_action": "retry_apply",
                        "source_slot_state": "new_manifest_installed_and_indexed",
                        "registration_state": state["registration_state"],
                    }
                    self._save_state(operation_descriptor, state)
                    return self._public_state(state)
                state["status"] = "applied"
                state["recovery"] = None
                self._save_state(operation_descriptor, state)
                self._finalize_applied_operation(
                    corpus_descriptor=corpus_descriptor,
                    corpus_root=corpus_root,
                    state=state,
                )
                return self._public_state(state)


@contextmanager
def _descriptor_context(descriptor: int):
    try:
        yield descriptor
    finally:
        os.close(descriptor)


__all__ = [
    "RemoteSourceSyncService",
    "SOURCE_SYNC_FORMAT",
    "SOURCE_SYNC_INDEX_BATCH_FILES",
    "SOURCE_SYNC_MAX_FILES",
    "SOURCE_SYNC_MAX_FILE_BYTES",
    "SOURCE_SYNC_MAX_INDEX_BATCHES",
    "SOURCE_SYNC_MAX_MANIFEST_BYTES",
    "SOURCE_SYNC_MAX_TOTAL_BYTES",
    "SourceSlotGuard",
    "SourceSyncConflictError",
    "SourceSyncRecoveryRequiredError",
    "SourceSyncUnavailableError",
    "SourceSyncValidationError",
    "build_source_sync_manifest",
    "canonical_source_sync_manifest",
    "iter_source_sync_file",
    "next_source_sync_deletion_epoch",
    "persist_source_sync_deletion_epoch",
    "read_coordinated_source_sync_head",
    "remote_source_sync_deletion_lock",
    "require_source_sync_readable",
    "source_sync_delete_blocker",
]
