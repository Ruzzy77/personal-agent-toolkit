"""Staged, resumable file transfers owned by the Host."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import mimetypes
import os
import secrets
import stat
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from threading import RLock
from typing import Concatenate, ParamSpec, TypeVar

from personal_agent_host.config import HostConfig, RootPolicy
from personal_agent_host.file_locks import root_lock
from personal_agent_host.files import (
    ABSENT,
    ToolError,
    _keep_previous,
    relative_path,
)

MAX_FILE_BYTES = 1024 * 1024 * 1024
CHUNK_BYTES = 8 * 1024 * 1024
TTL_S = 24 * 60 * 60
PREFIX = ".host-transfer-"
STATE_DIR = "host-transfers"
STATE_FILE = "transfers.json"
Fingerprint = tuple[int, int, int, int, int]
P = ParamSpec("P")
R = TypeVar("R")


def _serialized(
    method: Callable[Concatenate[Transfers, P], R],
) -> Callable[Concatenate[Transfers, P], R]:
    @wraps(method)
    def guarded(
        self: Transfers, /, *args: P.args, **kwargs: P.kwargs
    ) -> R:
        with self._lock:
            return method(self, *args, **kwargs)

    return guarded


@dataclass
class Transfer:
    id: str
    token_hash: bytes
    expires_at: float
    policy: RootPolicy
    target: Path
    path: str
    staging: Path | None
    size: int
    expected_version: str | None
    sha256: str | None
    offset: int = 0
    download_version: str | None = None
    download_fingerprint: Fingerprint | None = None
    staging_identity: tuple[int, int] | None = None


def _token() -> tuple[str, bytes]:
    raw = secrets.token_bytes(32)
    return (
        base64.urlsafe_b64encode(raw).decode().rstrip("="),
        hashlib.sha256(raw).digest(),
    )


def _hash(token: str) -> bytes:
    if not isinstance(token, str) or len(token) != 43:
        raise ToolError("not_found", "transfer is unavailable")
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    except (ValueError, binascii.Error) as exc:
        raise ToolError("not_found", "transfer is unavailable") from exc
    if len(raw) != 32:
        raise ToolError("not_found", "transfer is unavailable")
    return hashlib.sha256(raw).digest()


def _fingerprint(value: os.stat_result) -> Fingerprint:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _version_and_snapshot(target: Path) -> tuple[str, int, Fingerprint]:
    digest = hashlib.sha256()
    try:
        with target.open("rb") as handle:
            before = _fingerprint(os.fstat(handle.fileno()))
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
            after = _fingerprint(os.fstat(handle.fileno()))
    except FileNotFoundError as exc:
        raise ToolError("not_found", "file does not exist") from exc
    except OSError as exc:
        raise ToolError("read_failed", "file could not be read") from exc
    if before != after:
        raise ToolError("version_conflict", "file changed while being read")
    return "sha256:" + digest.hexdigest(), before[2], before


def _valid_transfer_id(value: object) -> str:
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
    if (
        not isinstance(value, str)
        or not 12 <= len(value) <= 32
        or any(character not in alphabet for character in value)
    ):
        raise ValueError("invalid transfer id")
    return value


class Transfers:
    def __init__(self, config: HostConfig) -> None:
        self.config = config
        self._lock = RLock()
        self.items: dict[str, Transfer] = {}
        self.state_dir = config.sync.data_root / STATE_DIR
        self.state_path = self.state_dir / STATE_FILE
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self._lock:
            self._load()
            self._expire()

    def _policy(self, root: RootPolicy) -> None:
        if root.permission == "read_only":
            raise ToolError("policy_denied", f"{root.id} is read-only")

    def _target(self, root: RootPolicy, path: str, *, write: bool = False) -> Path:
        target = relative_path(root.root, path)
        if target == root.root.resolve():
            raise ToolError("invalid_path", "path must name a file")
        if write and self.config.protects(target):
            raise ToolError("policy_denied", "path is protected")
        return target

    @staticmethod
    def _regular_or_absent(target: Path) -> bool:
        try:
            mode = target.lstat().st_mode
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise ToolError("read_failed", "file could not be read") from exc
        if not stat.S_ISREG(mode):
            raise ToolError("invalid_path", "path is not a regular file")
        return True

    def _current_version(self, target: Path) -> str:
        if not self._regular_or_absent(target):
            return ABSENT
        return _version_and_snapshot(target)[0]

    def _get(self, transfer_id: str, token: str) -> Transfer:
        self._expire()
        item = self.items.get(transfer_id)
        if item is None or not secrets.compare_digest(item.token_hash, _hash(token)):
            raise ToolError("not_found", "transfer is unavailable")
        return item

    @staticmethod
    def _valid_sha256(value: str) -> bool:
        return len(value) == 64 and all(
            char in "0123456789abcdef" for char in value.lower()
        )

    def _serialize(self, item: Transfer) -> dict[str, object]:
        return {
            "id": item.id,
            "token_hash": item.token_hash.hex(),
            "expires_at": item.expires_at,
            "root_id": item.policy.id,
            "path": item.path,
            "size": item.size,
            "expected_version": item.expected_version,
            "sha256": item.sha256,
            "offset": item.offset,
            "download_version": item.download_version,
            "download_fingerprint": item.download_fingerprint,
            "staging_identity": item.staging_identity,
        }

    def _save(self) -> None:
        payload = {
            "version": 1,
            "transfers": [self._serialize(item) for item in self.items.values()],
        }
        temporary = self.state_dir / f".host-transfer-state-{secrets.token_hex(12)}.tmp"
        try:
            with temporary.open("x", encoding="utf-8") as handle:
                json.dump(payload, handle, separators=(",", ":"), sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.state_path)
            descriptor = os.open(self.state_dir, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            temporary.unlink(missing_ok=True)

    def _load(self) -> None:
        if not self.state_path.exists():
            return
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("version") != 1:
                raise ValueError("unsupported transfer state")
            records = payload.get("transfers")
            if not isinstance(records, list):
                raise TypeError("invalid transfer state")
            for record in records:
                item = self._deserialize(record)
                if item.id in self.items:
                    raise ValueError("duplicate transfer state")
                self.items[item.id] = item
        except (OSError, TypeError, ValueError, KeyError) as exc:
            raise RuntimeError("host transfer state is invalid") from exc

    @staticmethod
    def _identity(value: object, *, width: int) -> tuple[int, ...]:
        if not isinstance(value, list) or len(value) != width or not all(
            isinstance(number, int) and not isinstance(number, bool) and number >= 0
            for number in value
        ):
            raise ValueError("invalid transfer identity")
        return tuple(value)

    def _deserialize(self, record: object) -> Transfer:
        if not isinstance(record, dict):
            raise TypeError("invalid transfer record")
        transfer_id = _valid_transfer_id(record["id"])
        token_hash = bytes.fromhex(record["token_hash"])
        if len(token_hash) != hashlib.sha256().digest_size:
            raise ValueError("invalid token hash")
        expires_at, size, offset = (
            record["expires_at"],
            record["size"],
            record["offset"],
        )
        if (
            not isinstance(expires_at, (int, float))
            or isinstance(expires_at, bool)
            or not isinstance(size, int)
            or isinstance(size, bool)
            or not 0 <= size <= MAX_FILE_BYTES
            or not isinstance(offset, int)
            or isinstance(offset, bool)
            or not 0 <= offset <= size
        ):
            raise ValueError("invalid transfer metadata")
        root_id, path = record["root_id"], record["path"]
        if not isinstance(root_id, str) or not isinstance(path, str):
            raise TypeError("invalid transfer location")
        expected_version = record["expected_version"]
        sha256 = record["sha256"]
        download_version = record["download_version"]
        if expected_version is not None and (
            not isinstance(expected_version, str) or not expected_version
        ):
            raise ValueError("invalid expected version")
        if sha256 is not None and (
            not isinstance(sha256, str) or not self._valid_sha256(sha256)
        ):
            raise ValueError("invalid upload hash")
        if download_version is not None and not isinstance(download_version, str):
            raise ValueError("invalid download version")
        policy = self.config.root(root_id)
        target = self._target(policy, path, write=download_version is None)
        staging: Path | None = None
        staging_identity: tuple[int, int] | None = None
        download_fingerprint: Fingerprint | None = None
        if download_version is None:
            if expected_version is None:
                raise ValueError("upload has no expected version")
            staging = target.parent / f"{PREFIX}{transfer_id}"
            staging_identity = self._identity(record["staging_identity"], width=2)
            try:
                observed = staging.lstat()
            except FileNotFoundError as exc:
                raise ValueError("upload staging is missing") from exc
            if not stat.S_ISREG(observed.st_mode) or (
                observed.st_dev,
                observed.st_ino,
            ) != staging_identity:
                raise ValueError("upload staging identity changed")
            if observed.st_size != offset:
                raise ValueError("upload offset does not match staging")
        else:
            if expected_version is not None:
                raise ValueError("download expected version is invalid")
            download_fingerprint = self._identity(
                record["download_fingerprint"], width=5
            )
        return Transfer(
            transfer_id,
            token_hash,
            float(expires_at),
            policy,
            target,
            path,
            staging,
            size,
            expected_version,
            sha256,
            offset,
            download_version,
            download_fingerprint,
            staging_identity,
        )

    def _staging(self, item: Transfer) -> Path:
        if item.staging is None or item.staging_identity is None:
            raise ToolError("invalid_request", "transfer is not an upload")
        try:
            observed = item.staging.lstat()
        except FileNotFoundError as exc:
            raise ToolError("not_found", "upload staging is unavailable") from exc
        if not stat.S_ISREG(observed.st_mode) or (
            observed.st_dev,
            observed.st_ino,
        ) != item.staging_identity:
            raise ToolError("version_conflict", "upload staging changed")
        return item.staging

    def _open_staging(self, item: Transfer, flags: int):
        staging = self._staging(item)
        descriptor = os.open(staging, flags | getattr(os, "O_NOFOLLOW", 0))
        try:
            observed = os.fstat(descriptor)
            if (observed.st_dev, observed.st_ino) != item.staging_identity:
                raise ToolError("version_conflict", "upload staging changed")
            return os.fdopen(descriptor, "r+b" if flags & os.O_RDWR else "rb")
        except (OSError, ToolError):
            os.close(descriptor)
            raise

    def _expire(self) -> None:
        now = time.time()
        changed = False
        for transfer_id, item in list(self.items.items()):
            if item.expires_at <= now:
                self._discard(item)
                del self.items[transfer_id]
                changed = True
        if changed:
            self._save()

    @_serialized
    def expire(self) -> None:
        self._expire()

    def _discard(self, item: Transfer) -> None:
        if item.staging is None or item.staging_identity is None:
            return
        try:
            observed = item.staging.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISREG(observed.st_mode) and (
            observed.st_dev,
            observed.st_ino,
        ) == item.staging_identity:
            item.staging.unlink()

    @_serialized
    def begin_upload(
        self,
        root: RootPolicy,
        path: str,
        size: int,
        expected_version: str,
        sha256: str | None = None,
    ) -> dict[str, object]:
        self._expire()
        self._policy(root)
        if (
            not isinstance(size, int)
            or isinstance(size, bool)
            or not 0 <= size <= MAX_FILE_BYTES
        ):
            raise ToolError("too_large", "upload size is invalid")
        if not isinstance(expected_version, str) or not expected_version:
            raise ToolError("invalid_request", "expected_version is required")
        if sha256 is not None and (
            not isinstance(sha256, str) or not self._valid_sha256(sha256)
        ):
            raise ToolError("invalid_request", "sha256 is invalid")
        target = self._target(root, path, write=True)
        version = self._current_version(target)
        if expected_version != version:
            raise ToolError("version_conflict", "target version changed")
        if root.permission == "create_only" and target.exists():
            raise ToolError("policy_denied", "root only allows creating files")
        token, token_hash = _token()
        transfer_id = secrets.token_urlsafe(12)
        staging = target.parent / f"{PREFIX}{transfer_id}"
        while staging.exists():
            transfer_id = secrets.token_urlsafe(12)
            staging = target.parent / f"{PREFIX}{transfer_id}"
        staging.parent.mkdir(parents=True, exist_ok=True)
        with staging.open("xb") as handle:
            handle.flush()
            os.fsync(handle.fileno())
        observed = staging.lstat()
        item = Transfer(
            transfer_id,
            token_hash,
            time.time() + TTL_S,
            root,
            target,
            path,
            staging,
            size,
            expected_version,
            sha256,
            staging_identity=(observed.st_dev, observed.st_ino),
        )
        self.items[transfer_id] = item
        self._save()
        return {
            "transfer_id": transfer_id,
            "token": token,
            "offset": 0,
            "chunk_bytes": CHUNK_BYTES,
            "expires_at": item.expires_at,
        }

    @_serialized
    def write_chunk(
        self, transfer_id: str, token: str, offset: int, data: bytes
    ) -> dict[str, object]:
        item = self._get(transfer_id, token)
        self._staging(item)
        if not isinstance(data, bytes) or len(data) > CHUNK_BYTES:
            raise ToolError("invalid_request", "chunk is invalid")
        if (
            not isinstance(offset, int)
            or isinstance(offset, bool)
            or offset < 0
            or offset + len(data) > item.size
        ):
            raise ToolError("invalid_range", "chunk is outside the upload")
        if offset > item.offset:
            raise ToolError("offset_conflict", "chunk offset is ahead of transfer")
        with self._open_staging(item, os.O_RDWR) as handle:
            if offset < item.offset:
                handle.seek(offset)
                if handle.read(len(data)) != data:
                    raise ToolError("offset_conflict", "chunk retry differs")
            else:
                handle.seek(offset)
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
                item.offset += len(data)
        self._staging(item)
        self._save()
        return {"offset": item.offset, "expires_at": item.expires_at}

    @_serialized
    def commit(self, transfer_id: str, token: str) -> dict[str, object]:
        item = self._get(transfer_id, token)
        staging = self._staging(item)
        if item.offset != item.size:
            raise ToolError("incomplete", "upload is incomplete")
        digest = hashlib.sha256()
        with self._open_staging(item, os.O_RDONLY) as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        self._staging(item)
        if item.sha256 and digest.hexdigest() != item.sha256.lower():
            raise ToolError("hash_mismatch", "upload hash does not match")
        with root_lock(item.policy.root):
            self._policy(item.policy)
            target = self._target(
                item.policy,
                item.path,
                write=True,
            )
            version = self._current_version(target)
            if version != item.expected_version:
                raise ToolError("version_conflict", "target version changed")
            if item.policy.permission == "create_only" and target.exists():
                raise ToolError("policy_denied", "root only allows creating files")
            staging = self._staging(item)
            _keep_previous(self.config, item.policy, target)
            if target.exists():
                os.chmod(staging, stat.S_IMODE(target.stat().st_mode))
            os.replace(staging, target)
        item.staging = None
        self.items.pop(item.id, None)
        self._save()
        return {
            "path": item.path,
            "version": "sha256:" + digest.hexdigest(),
        }

    @_serialized
    def cancel(self, transfer_id: str, token: str) -> None:
        item = self._get(transfer_id, token)
        self._discard(item)
        self.items.pop(item.id, None)
        self._save()

    @_serialized
    def status(self, transfer_id: str, token: str) -> dict[str, object]:
        item = self._get(transfer_id, token)
        return {"offset": item.offset, "size": item.size, "expires_at": item.expires_at}

    @_serialized
    def begin_download(
        self, root: RootPolicy, path: str, expected_version: str | None = None
    ) -> dict[str, object]:
        self._expire()
        if expected_version is not None and (
            not isinstance(expected_version, str) or not expected_version
        ):
            raise ToolError("invalid_request", "expected_version is invalid")
        target = self._target(root, path)
        if not self._regular_or_absent(target):
            raise ToolError("not_found", "file does not exist")
        try:
            if target.stat().st_size > MAX_FILE_BYTES:
                raise ToolError("too_large", "download exceeds the file limit")
        except OSError as exc:
            raise ToolError("read_failed", "file could not be read") from exc
        version, size, fingerprint = _version_and_snapshot(target)
        if size > MAX_FILE_BYTES:
            raise ToolError("too_large", "download exceeds the file limit")
        if expected_version is not None and expected_version != version:
            raise ToolError("version_conflict", "file version changed")
        token, token_hash = _token()
        transfer_id = secrets.token_urlsafe(12)
        item = Transfer(
            transfer_id,
            token_hash,
            time.time() + TTL_S,
            root,
            target,
            path,
            None,
            size,
            None,
            None,
            download_version=version,
            download_fingerprint=fingerprint,
        )
        self.items[transfer_id] = item
        self._save()
        return {
            "transfer_id": transfer_id,
            "token": token,
            "size": size,
            "version": version,
            "mime": mimetypes.guess_type(target.name)[0] or "application/octet-stream",
            "expires_at": item.expires_at,
        }

    def _download_target(self, item: Transfer) -> Path:
        if item.download_fingerprint is None:
            raise ToolError("invalid_request", "transfer is not a download")
        target = self._target(
            item.policy, item.path
        )
        if target != item.target or not self._regular_or_absent(target):
            raise ToolError("version_conflict", "download source changed")
        try:
            observed = _fingerprint(target.stat())
        except OSError as exc:
            raise ToolError("not_found", "file does not exist") from exc
        if observed != item.download_fingerprint:
            raise ToolError("version_conflict", "download source changed")
        return target

    @_serialized
    def download_info(self, transfer_id: str, token: str) -> dict[str, object]:
        item = self._get(transfer_id, token)
        target = self._download_target(item)
        return {
            "path": target,
            "size": item.size,
            "version": item.download_version,
            "mime": mimetypes.guess_type(target.name)[0] or "application/octet-stream",
            "expires_at": item.expires_at,
        }

    @_serialized
    def read_chunk(
        self, transfer_id: str, token: str, offset: int, limit: int
    ) -> bytes:
        item = self._get(transfer_id, token)
        target = self._download_target(item)
        if (
            not isinstance(offset, int)
            or isinstance(offset, bool)
            or not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 0 <= offset <= item.size
            or not 0 < limit <= CHUNK_BYTES
        ):
            raise ToolError("invalid_range", "download range is invalid")
        try:
            with target.open("rb") as handle:
                before = _fingerprint(os.fstat(handle.fileno()))
                if before != item.download_fingerprint:
                    raise ToolError("version_conflict", "download source changed")
                handle.seek(offset)
                data = handle.read(min(limit, item.size - offset))
                after = _fingerprint(os.fstat(handle.fileno()))
        except OSError as exc:
            raise ToolError("read_failed", "file could not be read") from exc
        if after != before or self._download_target(item) != target:
            raise ToolError("version_conflict", "download source changed")
        return data
