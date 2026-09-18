"""Root-relative file access: search, read, and version-checked writes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import tempfile
import unicodedata
from pathlib import Path
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError as McpToolError

from personal_agent_host.config import LIMITS, HostConfig, RootPolicy

MAX_PATH_BYTES = 4096
MAX_MARKER_BYTES = 4096
SEARCH_TIMEOUT_S = 10
SEARCH_EXCERPT_BYTES = 256 * 1024
ABSENT = "absent"


class ToolError(McpToolError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


def relative_path(root: Path, value: str) -> Path:
    """Resolve ``value`` inside ``root`` without following symlinks outside it."""

    if not value or len(value.encode("utf-8")) > MAX_PATH_BYTES:
        raise ToolError("invalid_path", "path is empty or too long")
    normalized = unicodedata.normalize("NFC", value).replace("\\", "/")
    if normalized.startswith(("/", "~")):
        raise ToolError("invalid_path", "path must be relative to the root")
    parts = [part for part in normalized.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise ToolError("invalid_path", "path cannot leave the root")
    candidate = root.joinpath(*parts) if parts else root
    try:
        resolved = candidate.resolve(strict=False)
        base = root.resolve(strict=True)
    except OSError as exc:
        raise ToolError("root_unavailable", "root is unavailable") from exc
    if resolved != base and base not in resolved.parents:
        raise ToolError("invalid_path", "path resolves outside the root")
    return resolved


def display_path(root: Path, target: Path) -> str:
    return target.relative_to(root.resolve()).as_posix()


def file_version(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _read_bytes(target: Path, label: str) -> bytes:
    try:
        return target.read_bytes()
    except FileNotFoundError as exc:
        raise ToolError("not_found", f"{label} does not exist") from exc
    except IsADirectoryError as exc:
        raise ToolError("invalid_path", f"{label} is a directory") from exc
    except OSError as exc:
        raise ToolError("read_failed", f"{label} could not be read") from exc


def _decode(data: bytes, label: str) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ToolError("not_text", f"{label} is not UTF-8 text") from exc


def read_files(
    policy: RootPolicy, files: list[dict[str, Any]], max_bytes: int
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    budget = max_bytes
    truncated = False
    for request in files:
        label = request["path"]
        target = relative_path(policy.root, label)
        data = _read_bytes(target, label)
        lines = _decode(data, label).splitlines(keepends=True)
        start = int(request.get("start_line") or 1)
        end = int(request.get("end_line") or len(lines))
        if end < start:
            raise ToolError("invalid_range", "end_line is before start_line")
        selected: list[str] = []
        last = start - 1
        for index in range(start - 1, min(end, len(lines))):
            line = lines[index]
            size = len(line.encode("utf-8"))
            if size > budget:
                if not selected and size > max_bytes:
                    raise ToolError(
                        "line_too_long", f"{label}:{index + 1} exceeds max_bytes"
                    )
                truncated = True
                break
            selected.append(line)
            budget -= size
            last = index + 1
        results.append(
            {
                "path": display_path(policy.root, target),
                "content": "".join(selected),
                "version": file_version(data),
                "start_line": start,
                "end_line": last,
            }
        )
        if truncated:
            break
    return {"files": results, "truncated": truncated}


def _recovery_path(config: HostConfig, policy: RootPolicy, target: Path) -> Path:
    digest = hashlib.sha256(
        f"{policy.id}:{display_path(policy.root, target)}".encode()
    ).hexdigest()[:24]
    return config.sync.data_root / "host-recovery" / f"{digest}.prev"


def _keep_previous(config: HostConfig, policy: RootPolicy, target: Path) -> None:
    if not target.is_file():
        return
    recovery = _recovery_path(config, policy, target)
    recovery.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target, recovery)


def _atomic_write(target: Path, payload: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".host-", dir=target.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if target.exists():
            shutil.copymode(target, temporary)
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def write_file(
    config: HostConfig,
    policy: RootPolicy,
    path: str,
    *,
    content: str | None,
    replace: dict[str, str] | None,
    delete: bool,
    expected_version: str | None,
) -> dict[str, Any]:
    if (
        sum(1 for flag in (content is not None, replace is not None, delete) if flag)
        != 1
    ):
        raise ToolError(
            "invalid_request", "give exactly one of content, replace, or delete"
        )
    if policy.permission == "read_only":
        raise ToolError("policy_denied", f"{policy.id} is read-only")
    target = relative_path(policy.root, path)
    if target == policy.root.resolve():
        raise ToolError("invalid_path", "path must name a file")
    exists = target.exists()
    if exists and not target.is_file():
        raise ToolError("invalid_path", f"{path} is not a regular file")
    current = _read_bytes(target, path) if exists else None
    current_version = file_version(current) if current is not None else ABSENT

    if expected_version is not None and expected_version != current_version:
        raise ToolError(
            "version_conflict",
            f"{path} is {current_version}, not {expected_version}",
        )
    if policy.permission == "create_only" and exists:
        raise ToolError("policy_denied", f"{policy.id} only allows creating files")

    if delete:
        if not exists:
            raise ToolError("not_found", f"{path} does not exist")
        _keep_previous(config, policy, target)
        target.unlink()
        return {"path": display_path(policy.root, target), "version": ABSENT}

    if replace is not None:
        if not exists:
            raise ToolError("not_found", f"{path} does not exist")
        start, end, body = (
            replace["start_marker"],
            replace["end_marker"],
            replace["content"],
        )
        for marker in (start, end):
            if not marker or len(marker.encode("utf-8")) > MAX_MARKER_BYTES:
                raise ToolError("invalid_request", "marker is empty or too long")
        text = _decode(current or b"", path)
        if text.count(start) != 1 or text.count(end) != 1:
            raise ToolError("marker_ambiguous", "each marker must appear exactly once")
        head = text.index(start) + len(start)
        tail = text.index(end)
        if tail < head:
            raise ToolError("marker_order", "start_marker must precede end_marker")
        payload = (text[:head] + body + text[tail:]).encode("utf-8")
    else:
        payload = (content or "").encode("utf-8")

    if len(payload) > LIMITS["write_bytes"]:
        raise ToolError("too_large", "the resulting file exceeds write_bytes")
    _keep_previous(config, policy, target)
    _atomic_write(target, payload)
    return {"path": display_path(policy.root, target), "version": file_version(payload)}


async def search(
    policy: RootPolicy,
    *,
    paths: list[str],
    pattern: str,
    max_results: int,
    context: int,
    ignore_vcs: bool,
) -> dict[str, Any]:
    if not pattern or len(pattern.encode("utf-8")) > MAX_PATH_BYTES:
        raise ToolError("invalid_request", "pattern is empty or too long")
    root = policy.root.resolve()
    command = [
        "rg",
        "--json",
        "--no-messages",
        "--max-columns",
        "4096",
        "-C",
        str(context),
        "-e",
        pattern,
    ]
    if not ignore_vcs:
        command.append("--no-ignore")
    for glob in paths:
        if not glob or glob.startswith("/") or ".." in glob.split("/"):
            raise ToolError("invalid_path", "search paths must be relative globs")
        command += ["--glob", glob]
    command.append(str(root))
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=str(root),
        )
    except FileNotFoundError as exc:
        raise ToolError("search_unavailable", "ripgrep is not installed") from exc

    matches: list[dict[str, Any]] = []
    truncated = False
    budget = SEARCH_EXCERPT_BYTES
    pending_before: list[str] = []
    after_target: dict[str, Any] | None = None

    def rel(value: str) -> str:
        return Path(value).resolve().relative_to(root).as_posix()

    async def consume() -> None:
        nonlocal truncated, budget, pending_before, after_target
        assert process.stdout is not None
        async for raw in process.stdout:
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            kind = event.get("type")
            data = event.get("data", {})
            if kind == "begin":
                pending_before = []
                after_target = None
                continue
            text = (data.get("lines") or {}).get("text", "").rstrip("\n")
            if kind == "context":
                if after_target is not None and len(after_target["after"]) < context:
                    after_target["after"].append(text)
                else:
                    after_target = None
                    pending_before = (
                        (pending_before + [text])[-context:] if context else []
                    )
                continue
            if kind != "match":
                continue
            if len(matches) >= max_results:
                truncated = True
                break
            size = len(text.encode("utf-8"))
            if size > budget:
                truncated = True
                break
            budget -= size
            entry = {
                "path": rel(data["path"]["text"]),
                "line": data.get("line_number"),
                "text": text,
                "before": list(pending_before),
                "after": [],
            }
            matches.append(entry)
            pending_before = []
            after_target = entry

    try:
        await asyncio.wait_for(consume(), timeout=SEARCH_TIMEOUT_S)
    except TimeoutError:
        truncated = True
    finally:
        if process.returncode is None:
            process.kill()
        await process.wait()
    return {"matches": matches, "truncated": truncated}
