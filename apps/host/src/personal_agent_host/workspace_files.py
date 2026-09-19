"""Safe root-relative workspace directory operations."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import shutil
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Any

from personal_agent_host.config import HostConfig, RootPolicy
from personal_agent_host.file_locks import root_lock
from personal_agent_host.files import (
    ABSENT,
    ToolError,
    _atomic_write,
    display_path,
    relative_path,
)

_TRASH_SECONDS = 30 * 24 * 60 * 60
_MAX_LIST = 200


class WorkspaceFiles:
    """Mutation-safe filesystem operations scoped to registered Host roots."""

    def __init__(self, config: HostConfig) -> None:
        self.config = config

    def dispatch(
        self, root: str, operation: str, *, path: str = ".", destination: str | None = None,
        expected_version: str | None = None, trash_id: str | None = None,
        limit: int = _MAX_LIST, cursor: str | None = None,
    ) -> dict[str, Any]:
        try:
            policy = self.config.root(root)
        except Exception as exc:
            raise ToolError("root_not_found", "root is not registered on this host") from exc
        if operation == "list":
            return self._list(policy, path, limit, cursor)
        if operation == "stat":
            return self._stat(policy, path)
        if operation == "trash_list":
            return self._trash_list(policy, limit, cursor)
        if operation == "mkdir":
            return self._mkdir(policy, path)
        if operation == "move":
            if destination is None:
                raise ToolError("invalid_request", "destination is required")
            return self._move(policy, path, destination, expected_version)
        if operation == "trash":
            return self._trash(policy, path, expected_version)
        if operation == "restore":
            if not trash_id:
                raise ToolError("invalid_request", "trash_id is required")
            return self._restore(policy, trash_id)
        raise ToolError("invalid_request", "unknown workspace operation")

    def _target(self, policy: RootPolicy, path: str) -> Path:
        # Reject lexical symlink components before relative_path resolves them.
        normalized = unicodedata.normalize("NFC", path).replace("\\", "/")
        parts = [part for part in normalized.split("/") if part not in ("", ".")]
        current = policy.root.resolve()
        for part in parts:
            current = current / part
            if current.is_symlink():
                raise ToolError("invalid_path", "symlink paths are not supported")
        return relative_path(policy.root, path)

    def _write_allowed(self, policy: RootPolicy, target: Path, *, create: bool = False) -> None:
        if policy.permission == "read_only":
            raise ToolError("policy_denied", f"{policy.id} is read-only")
        if policy.permission == "create_only" and not create:
            raise ToolError("policy_denied", f"{policy.id} only allows new directories")
        if target == policy.root.resolve():
            raise ToolError("policy_denied", "the registered root cannot be changed")
        if any(part.startswith(".host-transfer-") for part in target.relative_to(policy.root.resolve()).parts):
            raise ToolError("policy_denied", "transfer staging paths cannot be changed")
        if self.config.protects(target):
            raise ToolError("policy_denied", "path is inside a protected source")
        for registered in self.config.roots:
            registered_root = registered.root.resolve(strict=False)
            if target == registered_root or target in registered_root.parents:
                raise ToolError("policy_denied", "registered roots cannot be relocated")
        self._reject_symlinks(policy.root.resolve(), target)

    @staticmethod
    def _reject_symlinks(base: Path, target: Path) -> None:
        try:
            relative = target.relative_to(base)
        except ValueError as exc:
            raise ToolError("invalid_path", "path leaves the root") from exc
        current = base
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ToolError("invalid_path", "symlink paths are not supported")

    def _reject_tree(self, policy: RootPolicy, target: Path) -> None:
        self._reject_symlinks(policy.root.resolve(), target)
        if target.is_symlink():
            raise ToolError("invalid_path", "symlink paths are not supported")
        if target.is_dir():
            for child in target.rglob("*"):
                if child.name.startswith(".host-transfer-"):
                    raise ToolError("policy_denied", "directory contains an active upload")
                if child.is_symlink():
                    raise ToolError("invalid_path", "directories containing symlinks cannot change")
                if self.config.protects(child):
                    raise ToolError("policy_denied", "directory contains a protected source")
                for registered in self.config.roots:
                    if child == registered.root.resolve(strict=False):
                        raise ToolError("policy_denied", "directory contains a registered root")

    @staticmethod
    def _require(target: Path, path: str) -> None:
        if not target.exists():
            raise ToolError("not_found", f"{path} does not exist")

    def _version(self, target: Path) -> str:
        if target.is_file():
            digest = hashlib.sha256()
            try:
                with target.open("rb") as stream:
                    while block := stream.read(1024 * 1024):
                        digest.update(block)
            except OSError as exc:
                raise ToolError("read_failed", "file could not be read") from exc
            return "sha256:" + digest.hexdigest()
        if not target.is_dir():
            raise ToolError("invalid_path", "path is not a regular file or directory")
        digest = hashlib.sha256()
        for item in sorted(target.rglob("*"), key=lambda value: value.as_posix()):
            if item.is_symlink():
                raise ToolError("invalid_path", "symlink paths are not supported")
            stat = item.stat()
            relative = item.relative_to(target).as_posix()
            kind = "d" if item.is_dir() else "f"
            digest.update(f"{relative}\0{kind}\0{stat.st_size}\0{stat.st_mtime_ns}\n".encode())
        return "sha256:" + digest.hexdigest()

    def _info(self, policy: RootPolicy, target: Path, *, full_version: bool = True) -> dict[str, Any]:
        self._require(target, display_path(policy.root, target) or ".")
        self._reject_symlinks(policy.root.resolve(), target)
        stat = target.stat()
        kind = "directory" if target.is_dir() else "file" if target.is_file() else "other"
        if kind == "other":
            raise ToolError("invalid_path", "path is not a regular file or directory")
        return {
            "path": display_path(policy.root, target) or ".",
            "type": kind,
            "mime": mimetypes.guess_type(target.name)[0] or "application/octet-stream",
            "bytes": stat.st_size,
            **({"version": self._version(target)} if full_version else {}),
        }

    def _stat(self, policy: RootPolicy, path: str) -> dict[str, Any]:
        return self._info(policy, self._target(policy, path))

    def _list(self, policy: RootPolicy, path: str, limit: int, cursor: str | None) -> dict[str, Any]:
        target = self._target(policy, path)
        self._require(target, path)
        if not target.is_dir():
            raise ToolError("invalid_path", "path is not a directory")
        if not 1 <= int(limit) <= _MAX_LIST:
            raise ToolError("invalid_request", f"limit must be 1..{_MAX_LIST}")
        self._reject_symlinks(policy.root.resolve(), target)
        names = sorted(
            (item for item in target.iterdir() if not item.name.startswith(".host-transfer-")),
            key=lambda item: unicodedata.normalize("NFC", item.name),
        )
        if cursor is not None:
            cursor = unicodedata.normalize("NFC", cursor)
            names = [item for item in names if unicodedata.normalize("NFC", item.name) > cursor]
        page = names[: int(limit)]
        entries: list[dict[str, Any]] = []
        for item in page:
            name = unicodedata.normalize("NFC", item.name)
            if name.startswith(".host-transfer-"):
                continue
            stat = item.lstat()
            if item.is_symlink():
                kind = "symlink"
            elif item.is_dir():
                kind = "directory"
            elif item.is_file():
                kind = "file"
            else:
                continue
            entries.append({"name": name, "path": display_path(policy.root, item), "type": kind,
                            "mime": mimetypes.guess_type(item.name)[0] or "application/octet-stream",
                            "bytes": stat.st_size})
        return {"path": display_path(policy.root, target) or ".", "entries": entries,
                "next_cursor": unicodedata.normalize("NFC", page[-1].name) if len(names) > len(page) and page else None}

    def _mkdir(self, policy: RootPolicy, path: str) -> dict[str, Any]:
        target = self._target(policy, path)
        with root_lock(policy.root):
            self._write_allowed(policy, target, create=True)
            if target.exists():
                raise ToolError("version_conflict", "directory already exists")
            if not target.parent.is_dir() or self.config.protects(target.parent):
                raise ToolError("invalid_path", "parent directory is unavailable or protected")
            target.mkdir()
            return self._info(policy, target)

    def _mutate_target(self, policy: RootPolicy, path: str, expected_version: str | None) -> Path:
        if expected_version is None:
            raise ToolError("invalid_request", "expected_version is required")
        target = self._target(policy, path)
        self._write_allowed(policy, target)
        self._require(target, path)
        self._reject_tree(policy, target)
        current = self._version(target)
        if current != expected_version:
            raise ToolError("version_conflict", f"{path} is {current}, not {expected_version}")
        return target

    def _move(self, policy: RootPolicy, path: str, destination: str, expected_version: str | None) -> dict[str, Any]:
        with root_lock(policy.root):
            target = self._mutate_target(policy, path, expected_version)
            output = self._target(policy, destination)
            self._write_allowed(policy, output)
            if output.exists():
                raise ToolError("version_conflict", "destination already exists")
            if not output.parent.is_dir() or self.config.protects(output.parent):
                raise ToolError("invalid_path", "destination parent is unavailable or protected")
            if target.is_dir() and (output == target or target in output.parents):
                raise ToolError("invalid_path", "cannot move a directory into itself")
            os.replace(target, output)
            return self._info(policy, output)

    def _trash_root(self, policy: RootPolicy) -> Path:
        return self.config.sync.data_root / "host-trash" / policy.id.replace("/", "__")

    def _trash(self, policy: RootPolicy, path: str, expected_version: str | None) -> dict[str, Any]:
        with root_lock(policy.root):
            target = self._mutate_target(policy, path, expected_version)
            trash_id = uuid.uuid4().hex
            container = self._trash_root(policy) / trash_id
            payload = container / "payload"
            container.mkdir(parents=True)
            try:
                if target.stat().st_dev != container.stat().st_dev:
                    raise ToolError("trash_unavailable", "trash is on another filesystem")
                original = display_path(policy.root, target)
                meta = {"id": trash_id, "root": policy.id, "path": original,
                        "created": time.time(), "expires": time.time() + _TRASH_SECONDS}
                _atomic_write(container / "metadata.json", json.dumps(meta).encode("utf-8"))
                os.replace(target, payload)
            except BaseException:
                if container.exists() and not payload.exists():
                    shutil.rmtree(container, ignore_errors=True)
                raise
            return {"trash_id": trash_id, "path": original, "version": ABSENT, "expires": meta["expires"]}

    def _trash_list(self, policy: RootPolicy, limit: int, cursor: str | None) -> dict[str, Any]:
        if not 1 <= int(limit) <= _MAX_LIST:
            raise ToolError("invalid_request", f"limit must be 1..{_MAX_LIST}")
        root = self._trash_root(policy)
        items: list[dict[str, Any]] = []
        if root.exists():
            for container in sorted(root.iterdir(), key=lambda item: item.name):
                meta = container / "metadata.json"
                if not meta.is_file() or not (container / "payload").exists():
                    continue
                try:
                    value = json.loads(meta.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if value.get("root") == policy.id and (cursor is None or container.name > cursor):
                    items.append(value)
        page = items[:int(limit)]
        return {"items": page, "next_cursor": page[-1]["id"] if len(items) > len(page) and page else None}

    def expire(self) -> dict[str, int]:
        """Remove only this backend's expired, metadata-validated trash records."""
        removed = 0
        now = time.time()
        for policy in self.config.roots:
            with root_lock(policy.root):
                root = self._trash_root(policy)
                if not root.is_dir():
                    continue
                for container in root.iterdir():
                    metadata = container / "metadata.json"
                    payload = container / "payload"
                    if not container.is_dir() or not metadata.is_file() or not payload.exists():
                        continue
                    try:
                        value = json.loads(metadata.read_text(encoding="utf-8"))
                        expires = float(value["expires"])
                    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError):
                        continue
                    if (value.get("id") == container.name and value.get("root") == policy.id
                            and expires <= now):
                        shutil.rmtree(container)
                        removed += 1
        return {"expired": removed}

    def _restore(self, policy: RootPolicy, trash_id: str) -> dict[str, Any]:
        if not trash_id.isalnum():
            raise ToolError("invalid_request", "trash_id is invalid")
        with root_lock(policy.root):
            container = self._trash_root(policy) / trash_id
            meta_path, payload = container / "metadata.json", container / "payload"
            if not meta_path.is_file() or not payload.exists():
                raise ToolError("not_found", "trash item does not exist")
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ToolError("trash_unavailable", "trash metadata is invalid") from exc
            if meta.get("root") != policy.id:
                raise ToolError("policy_denied", "trash item belongs to another root")
            target = self._target(policy, str(meta.get("path", "")))
            self._write_allowed(policy, target)
            if target.exists():
                raise ToolError("version_conflict", "restore destination already exists")
            if not target.parent.is_dir() or self.config.protects(target.parent):
                raise ToolError("invalid_path", "restore parent is unavailable or protected")
            if payload.stat().st_dev != target.parent.stat().st_dev:
                raise ToolError("trash_unavailable", "restore crosses filesystems")
            os.replace(payload, target)
            shutil.rmtree(container)
            return self._info(policy, target)
