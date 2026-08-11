"""Durable coordination state for exact remote Corpus deletion."""

from __future__ import annotations

import json
import os
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any

from .config import (
    ensure_private_directory_at,
    normalize_corpus_id,
    open_private_file_at,
    private_directory,
)
from .database import encode_json, utc_now
from .errors import CorpusError

REMOTE_DELETE_INTENT_FORMAT = "corpus-remote-delete-intent-v1"
REMOTE_DELETE_INTENT_MAX_BYTES = 256 * 1024
REMOTE_DELETE_INTENT_PHASES = frozenset(
    {"prepared", "index_quarantined", "catalog_deleted", "cleanup_pending"}
)
REMOTE_DELETE_INTENT_FIELDS = frozenset(
    {
        "corpus_id",
        "created_at",
        "format",
        "managed_state",
        "operation_id",
        "phase",
        "quarantine_name",
        "registration_sha256",
        "resource_sha256",
        "source_sync_quarantine_name",
        "source_generation_tombstone",
        "source_manifest_tombstone_sha256",
        "state_digest",
        "tenant_ref",
        "updated_at",
    }
)


class RemoteDeletionRecoveryRequiredError(CorpusError):
    code = "remote_deletion_recovery_required"


def _is_hex(value: object, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(character in "0123456789abcdef" for character in value)
    )


def validate_remote_delete_intent(
    value: object,
    *,
    expected_corpus_id: str | None = None,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != REMOTE_DELETE_INTENT_FIELDS:
        raise RemoteDeletionRecoveryRequiredError(
            "remote deletion intent is invalid",
            details={"reason": "deletion_intent_invalid"},
        )
    try:
        corpus_id = normalize_corpus_id(value.get("corpus_id"))
    except CorpusError as exc:
        raise RemoteDeletionRecoveryRequiredError(
            "remote deletion intent is invalid",
            details={"reason": "deletion_intent_invalid"},
        ) from exc
    if (
        corpus_id != value["corpus_id"]
        or (expected_corpus_id is not None and corpus_id != expected_corpus_id)
        or value.get("format") != REMOTE_DELETE_INTENT_FORMAT
        or value.get("phase") not in REMOTE_DELETE_INTENT_PHASES
        or not _is_hex(value.get("operation_id"), 32)
        or not _is_hex(value.get("state_digest"), 64)
        or not _is_hex(value.get("registration_sha256"), 64)
        or type(value.get("source_generation_tombstone")) is not int
        or value["source_generation_tombstone"] <= 0
        or not _is_hex(value.get("source_manifest_tombstone_sha256"), 64)
        or not _is_hex(value.get("resource_sha256"), 64)
        or not _is_hex(value.get("tenant_ref"), 64)
        or value.get("quarantine_name") != f".deleting-{value['operation_id']}"
        or value.get("source_sync_quarantine_name") != f".deleting-sync-{value['operation_id']}"
        or not isinstance(value.get("managed_state"), dict)
        or not isinstance(value.get("created_at"), str)
        or not isinstance(value.get("updated_at"), str)
    ):
        raise RemoteDeletionRecoveryRequiredError(
            "remote deletion intent is invalid",
            details={"reason": "deletion_intent_invalid"},
        )
    return value


def _atomic_json_write(directory_descriptor: int, name: str, value: object) -> None:
    payload = encode_json(value).encode()
    if len(payload) > REMOTE_DELETE_INTENT_MAX_BYTES:
        raise RemoteDeletionRecoveryRequiredError(
            "remote deletion intent exceeds its durable-state bound",
            details={"reason": "deletion_intent_too_large"},
        )
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
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError("remote deletion intent write made no progress")
            offset += written
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


def _intent_directory(data_root: Path, *, create: bool):
    root = data_root / "remote-deletions"
    return private_directory(root, create=create)


def read_remote_delete_intent(
    data_root: Path,
    corpus_id: str,
) -> dict[str, Any] | None:
    corpus_id = normalize_corpus_id(corpus_id)
    root = data_root / "remote-deletions"
    try:
        directory_context = _intent_directory(data_root, create=False)
        directory_descriptor = directory_context.__enter__()
    except CorpusError as exc:
        if exc.details.get("reason") in {"missing", "missing_parent"}:
            return None
        raise RemoteDeletionRecoveryRequiredError(
            "remote deletion intent storage is unavailable",
            details={"reason": "deletion_intent_unavailable"},
        ) from exc
    try:
        try:
            descriptor, _created = open_private_file_at(
                directory_descriptor,
                f"{corpus_id}.json",
                path=root / f"{corpus_id}.json",
            )
        except CorpusError as exc:
            if exc.details.get("reason") == "missing":
                return None
            raise RemoteDeletionRecoveryRequiredError(
                "remote deletion intent is unavailable",
                details={"reason": "deletion_intent_unavailable"},
            ) from exc
        try:
            payload = b""
            while len(payload) <= REMOTE_DELETE_INTENT_MAX_BYTES:
                chunk = os.read(
                    descriptor,
                    min(65536, REMOTE_DELETE_INTENT_MAX_BYTES + 1 - len(payload)),
                )
                if not chunk:
                    break
                payload += chunk
        finally:
            os.close(descriptor)
        if len(payload) > REMOTE_DELETE_INTENT_MAX_BYTES:
            raise RemoteDeletionRecoveryRequiredError(
                "remote deletion intent exceeds its durable-state bound",
                details={"reason": "deletion_intent_too_large"},
            )
        try:
            value = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RemoteDeletionRecoveryRequiredError(
                "remote deletion intent is invalid",
                details={"reason": "deletion_intent_invalid"},
            ) from exc
        return validate_remote_delete_intent(
            value,
            expected_corpus_id=corpus_id,
        )
    finally:
        directory_context.__exit__(None, None, None)


def write_remote_delete_intent(data_root: Path, intent: dict[str, Any]) -> None:
    value = dict(validate_remote_delete_intent(intent))
    value["updated_at"] = utc_now()
    corpus_id = value["corpus_id"]
    root = data_root / "remote-deletions"
    with private_directory(data_root, create=True) as data_descriptor:
        try:
            os.stat("remote-deletions", dir_fd=data_descriptor, follow_symlinks=False)
            created = False
        except FileNotFoundError:
            created = True
        directory_descriptor = ensure_private_directory_at(
            data_descriptor,
            "remote-deletions",
            path=root,
        )
        if created:
            os.fsync(data_descriptor)
        try:
            _atomic_json_write(directory_descriptor, f"{corpus_id}.json", value)
        finally:
            os.close(directory_descriptor)
    intent.clear()
    intent.update(value)


def remove_remote_delete_intent(data_root: Path, corpus_id: str) -> None:
    corpus_id = normalize_corpus_id(corpus_id)
    try:
        with _intent_directory(data_root, create=False) as directory_descriptor:
            try:
                os.unlink(f"{corpus_id}.json", dir_fd=directory_descriptor)
            except FileNotFoundError:
                os.fsync(directory_descriptor)
                return
            os.fsync(directory_descriptor)
    except CorpusError as exc:
        if exc.details.get("reason") not in {"missing", "missing_parent"}:
            raise


def require_no_remote_delete_intent(data_root: Path, corpus_id: str) -> None:
    intent = read_remote_delete_intent(data_root, corpus_id)
    if intent is not None:
        raise RemoteDeletionRecoveryRequiredError(
            "remote Corpus deletion must finish before this operation can continue",
            details={
                "reason": "remote_deletion_recovery_required",
                "phase": intent["phase"],
            },
        )


__all__ = [
    "REMOTE_DELETE_INTENT_FORMAT",
    "RemoteDeletionRecoveryRequiredError",
    "read_remote_delete_intent",
    "remove_remote_delete_intent",
    "require_no_remote_delete_intent",
    "validate_remote_delete_intent",
    "write_remote_delete_intent",
]
