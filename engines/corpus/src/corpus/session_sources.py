"""Read-only linked-source adapters for completed Codex and Claude turns.

The durable database receives only neutral turn metadata, an exact provider
locator, and a digest of the visible turn transcript. Message text is returned
only by an explicit request-time fetch and is never part of an observation
record.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .errors import BudgetExceededError, ContextValidationError
from .source_access import resolve_source_root_identity_path

SESSION_SOURCE_PROVIDERS = frozenset({"codex", "claude"})
SESSION_SOURCE_ACTORS = frozenset({"all", "user_task", "subagent_task"})
SESSION_SOURCE_MAX_LOOKBACK_DAYS = 3650
SESSION_SOURCE_DEFAULT_LOOKBACK_DAYS = 30
SESSION_SOURCE_MAX_FILES = 10_000
SESSION_SOURCE_MAX_RECORDS = 10_000
SESSION_SOURCE_FETCH_MIN_CHARS = 1_000
SESSION_SOURCE_FETCH_DEFAULT_CHARS = 30_000
SESSION_SOURCE_FETCH_MAX_CHARS = 200_000

_TERMINAL_CLAUDE_STOP_REASONS = frozenset({"end_turn", "refusal", "stop_sequence"})
_FRESHNESS_PREFIX = "sha256:"


@dataclass
class _Transcript:
    capture_messages: bool
    _hasher: Any = field(default_factory=hashlib.sha256)
    messages: list[dict[str, str]] = field(default_factory=list)
    visible_message_count: int = 0

    def add(self, role: str, text: str, *, phase: str | None = None) -> None:
        if not text:
            return
        canonical = json.dumps(
            {"role": role, "phase": phase, "text": text},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        self._hasher.update(len(canonical).to_bytes(8, "big"))
        self._hasher.update(canonical)
        self.visible_message_count += 1
        if self.capture_messages:
            message = {"role": role, "text": text}
            if phase is not None:
                message["phase"] = phase
            self.messages.append(message)

    @property
    def freshness_identity(self) -> str:
        return f"{_FRESHNESS_PREFIX}{self._hasher.hexdigest()}"


def _normalize_timestamp(value: object) -> str | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = float(value)
        if abs(seconds) >= 100_000_000_000:
            seconds /= 1_000
        try:
            parsed = datetime.fromtimestamp(seconds, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip())
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _timestamp_value(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _cwd_in_scope(cwd: str, prefix: str) -> bool:
    resolved = os.path.realpath(cwd)
    base = os.path.realpath(prefix)
    return resolved == base or resolved.startswith(f"{base}{os.sep}")


def _stable_external_id(
    provider: str,
    session_id: str,
    turn_id: str,
) -> str:
    digest = hashlib.sha256(f"{provider}\0{session_id}\0{turn_id}".encode()).hexdigest()
    return f"turn_{digest[:40]}"


def _record_from_turn(
    *,
    provider: str,
    actor: str,
    session_id: str,
    turn_id: str,
    completed_at: str,
    root_ref: str,
    relative_path: str,
    transcript: _Transcript,
) -> dict[str, Any]:
    return {
        "external_id": _stable_external_id(provider, session_id, turn_id),
        "parent_external_id": session_id,
        "occurred_at": completed_at,
        "provider_metadata": {
            "session_id": session_id,
            "turn_id": turn_id,
            "actor": actor,
            "task_kind": f"{provider}_turn",
        },
        "locator": {
            "root_ref": root_ref,
            "relative_path": relative_path,
            "session_id": session_id,
            "turn_id": turn_id,
        },
        "freshness_identity": transcript.freshness_identity,
        "_messages": transcript.messages,
        "_visible_message_count": transcript.visible_message_count,
    }


def normalize_session_selector(
    provider: str,
    selector: object,
) -> dict[str, Any]:
    if provider not in SESSION_SOURCE_PROVIDERS:
        raise ContextValidationError(
            "unsupported session source provider",
            details={
                "provider_kind": provider,
                "allowed": sorted(SESSION_SOURCE_PROVIDERS),
            },
        )
    if not isinstance(selector, dict):
        raise ContextValidationError("session source selector must be an object")
    allowed = {
        "cwd_prefix",
        "cwd_root_device",
        "cwd_root_inode",
        "actor",
        "lookback_days",
    }
    if provider == "codex":
        allowed.add("include_archived")
    if "cwd_prefix" not in selector or set(selector) - allowed:
        raise ContextValidationError(
            f"{provider} selector fields are invalid",
            details={
                "required": ["cwd_prefix"],
                "allowed": sorted(allowed),
            },
        )
    cwd_prefix = selector["cwd_prefix"]
    if (
        not isinstance(cwd_prefix, str)
        or not cwd_prefix.strip()
        or "\x00" in cwd_prefix
        or len(cwd_prefix) > 2_000
    ):
        raise ContextValidationError(
            "session source cwd_prefix is invalid",
            details={"field": "selector.cwd_prefix"},
        )
    actor = selector.get("actor", "all")
    if actor not in SESSION_SOURCE_ACTORS:
        raise ContextValidationError(
            "session source actor selector is invalid",
            details={"allowed": sorted(SESSION_SOURCE_ACTORS)},
        )
    lookback_days = selector.get(
        "lookback_days",
        SESSION_SOURCE_DEFAULT_LOOKBACK_DAYS,
    )
    if (
        not isinstance(lookback_days, int)
        or isinstance(lookback_days, bool)
        or not 1 <= lookback_days <= SESSION_SOURCE_MAX_LOOKBACK_DAYS
    ):
        raise ContextValidationError(
            "session source lookback_days is invalid",
            details={"maximum": SESSION_SOURCE_MAX_LOOKBACK_DAYS},
        )
    resolved_prefix = os.path.realpath(os.path.expanduser(cwd_prefix.strip()))
    raw_device = selector.get("cwd_root_device")
    raw_inode = selector.get("cwd_root_inode")
    if (raw_device is None) != (raw_inode is None) or any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in (raw_device, raw_inode)
        if value is not None
    ):
        raise ContextValidationError(
            "session source cwd identity is invalid",
            details={"fields": ["selector.cwd_root_device", "selector.cwd_root_inode"]},
        )
    if raw_device is not None and raw_inode is not None:
        recovered = resolve_source_root_identity_path(raw_device, raw_inode)
        if recovered is not None:
            resolved_prefix = os.path.realpath(recovered)
        else:
            try:
                metadata = os.stat(resolved_prefix)
            except OSError:
                metadata = None
            if (
                metadata is not None
                and stat.S_ISDIR(metadata.st_mode)
                and int(metadata.st_ino) == raw_inode
            ):
                raw_device = int(metadata.st_dev)
    else:
        try:
            metadata = os.stat(resolved_prefix)
        except OSError:
            metadata = None
        if metadata is not None and stat.S_ISDIR(metadata.st_mode):
            raw_device = int(metadata.st_dev)
            raw_inode = int(metadata.st_ino)

    normalized: dict[str, Any] = {
        "cwd_prefix": resolved_prefix,
        "actor": actor,
        "lookback_days": lookback_days,
    }
    if raw_device is not None and raw_inode is not None:
        normalized["cwd_root_device"] = raw_device
        normalized["cwd_root_inode"] = raw_inode
    if provider == "codex":
        include_archived = selector.get("include_archived", True)
        if not isinstance(include_archived, bool):
            raise ContextValidationError(
                "codex include_archived selector must be a boolean"
            )
        normalized["include_archived"] = include_archived
    return normalized


def _provider_roots(
    provider: str,
    selector: dict[str, Any],
) -> list[tuple[str, Path]]:
    home = Path.home()
    if provider == "codex":
        roots = [
            (
                "codex_live",
                Path(
                    os.environ.get(
                        "CORPUS_CODEX_SESSIONS_ROOT",
                        home / ".codex" / "sessions",
                    )
                ).expanduser(),
            )
        ]
        if selector.get("include_archived", True):
            roots.append(
                (
                    "codex_archive",
                    Path(
                        os.environ.get(
                            "CORPUS_CODEX_ARCHIVED_SESSIONS_ROOT",
                            home / ".codex" / "archived_sessions",
                        )
                    ).expanduser(),
                )
            )
        return roots
    if provider == "claude":
        return [
            (
                "claude_projects",
                Path(
                    os.environ.get(
                        "CORPUS_CLAUDE_PROJECTS_ROOT",
                        home / ".claude" / "projects",
                    )
                ).expanduser(),
            )
        ]
    raise ContextValidationError(
        "unsupported session source provider",
        details={"provider_kind": provider},
    )


def _safe_provider_file(root: Path, candidate: Path) -> Path | None:
    try:
        root_resolved = root.resolve(strict=True)
        metadata = candidate.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            return None
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root_resolved)
    except (FileNotFoundError, OSError, ValueError):
        return None
    return resolved


def _iter_session_files(
    provider: str,
    selector: dict[str, Any],
    *,
    cutoff: datetime,
) -> tuple[list[tuple[str, Path, Path]], list[dict[str, str]], bool]:
    files: list[tuple[str, Path, Path]] = []
    issues: list[dict[str, str]] = []
    complete = True
    for root_ref, root in _provider_roots(provider, selector):
        if not root.exists():
            issues.append({"root_ref": root_ref, "reason": "root_unavailable"})
            complete = False
            continue
        pattern = "rollout-*.jsonl" if provider == "codex" else "*.jsonl"
        try:
            candidates = root.rglob(pattern)
            for candidate in candidates:
                if len(files) >= SESSION_SOURCE_MAX_FILES:
                    issues.append(
                        {
                            "root_ref": root_ref,
                            "reason": "file_limit_reached",
                        }
                    )
                    return files, issues, False
                safe = _safe_provider_file(root, candidate)
                if safe is None:
                    continue
                try:
                    modified = datetime.fromtimestamp(
                        safe.stat().st_mtime,
                        tz=UTC,
                    )
                except OSError:
                    complete = False
                    continue
                if modified < cutoff:
                    continue
                files.append((root_ref, root.resolve(strict=True), safe))
        except OSError:
            issues.append({"root_ref": root_ref, "reason": "enumeration_failed"})
            complete = False
    files.sort(key=lambda value: (value[0], str(value[2])))
    return files, issues, complete


def _peek_cwd(provider: str, path: Path) -> str | None:
    try:
        with path.open(encoding="utf-8", errors="strict") as handle:
            for index, line in enumerate(handle):
                if index >= 100:
                    return None
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if provider == "codex":
                    payload = record.get("payload")
                    if isinstance(payload, dict) and isinstance(
                        payload.get("cwd"),
                        str,
                    ):
                        return payload["cwd"]
                elif isinstance(record.get("cwd"), str):
                    return record["cwd"]
    except (OSError, UnicodeError):
        return None
    return None


def _codex_records(
    path: Path,
    *,
    root_ref: str,
    relative_path: str,
    selector: dict[str, Any],
    cutoff: datetime | None,
    capture_target: tuple[str, str] | None = None,
) -> list[dict[str, Any]]:
    session_id: str | None = None
    actor = "user_task"
    cwd: str | None = None
    active_turn: str | None = None
    pending_users: list[str] = []
    transcript: _Transcript | None = None
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8", errors="strict") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            record_type = record.get("type")
            payload = record.get("payload")
            if not isinstance(payload, dict):
                payload = {}
            if record_type == "session_meta":
                if session_id is None:
                    value = payload.get("id") or payload.get("session_id")
                    if value:
                        session_id = str(value)
                    source = payload.get("source")
                    if payload.get("thread_source") == "subagent" or (
                        isinstance(source, dict) and bool(source.get("subagent"))
                    ):
                        actor = "subagent_task"
                if isinstance(payload.get("cwd"), str):
                    cwd = payload["cwd"]
                continue
            if record_type == "turn_context":
                if isinstance(payload.get("cwd"), str):
                    cwd = payload["cwd"]
                continue
            if record_type != "event_msg":
                continue
            event_type = payload.get("type")
            if event_type == "user_message":
                value = payload.get("message")
                if isinstance(value, str) and value:
                    if active_turn and transcript is not None:
                        transcript.add("user", value)
                    else:
                        pending_users.append(value)
                continue
            if event_type == "task_started":
                turn = payload.get("turn_id")
                active_turn = str(turn) if turn else None
                capture = bool(
                    capture_target
                    and session_id
                    and active_turn
                    and capture_target == (session_id, active_turn)
                )
                transcript = _Transcript(capture_messages=capture)
                for value in pending_users:
                    transcript.add("user", value)
                pending_users = []
                continue
            if event_type == "agent_message" and active_turn and transcript:
                value = payload.get("message")
                if isinstance(value, str) and value:
                    transcript.add(
                        "assistant",
                        value,
                        phase=str(payload.get("phase") or "unknown"),
                    )
                continue
            if event_type != "task_complete":
                continue
            turn_value = payload.get("turn_id") or active_turn
            if not turn_value or not session_id or not cwd or transcript is None:
                active_turn = None
                transcript = None
                continue
            completed_at = _normalize_timestamp(
                payload.get("completed_at") or record.get("timestamp")
            )
            if completed_at is None:
                active_turn = None
                transcript = None
                continue
            turn_id = str(turn_value)
            if (
                _cwd_in_scope(cwd, selector["cwd_prefix"])
                and selector["actor"] in {"all", actor}
                and (cutoff is None or _timestamp_value(completed_at) >= cutoff)
            ):
                records.append(
                    _record_from_turn(
                        provider="codex",
                        actor=actor,
                        session_id=session_id,
                        turn_id=turn_id,
                        completed_at=completed_at,
                        root_ref=root_ref,
                        relative_path=relative_path,
                        transcript=transcript,
                    )
                )
                if capture_target == (session_id, turn_id):
                    break
            active_turn = None
            transcript = None
    return records


def _message_blocks(message: object) -> list[dict[str, Any]]:
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if not isinstance(content, list):
        return []
    return [block for block in content if isinstance(block, dict)]


def _visible_message_text(message: object) -> str:
    return "\n".join(
        str(block.get("text") or "")
        for block in _message_blocks(message)
        if block.get("type") == "text" and block.get("text")
    )


def _claude_records(
    path: Path,
    *,
    root_ref: str,
    relative_path: str,
    selector: dict[str, Any],
    cutoff: datetime | None,
    capture_target: tuple[str, str] | None = None,
) -> list[dict[str, Any]]:
    file_actor = (
        "subagent_task"
        if "subagents" in path.parts or path.name.startswith("agent-")
        else "user_task"
    )
    session_id: str | None = None
    cwd: str | None = None
    active_turn: str | None = None
    active_actor = file_actor
    transcript: _Transcript | None = None
    last_assistant_timestamp: str | None = None
    records: list[dict[str, Any]] = []

    def finish(completed_value: object) -> bool:
        nonlocal active_turn, active_actor, transcript
        nonlocal last_assistant_timestamp
        if not active_turn or not session_id or not cwd or transcript is None:
            return False
        completed_at = _normalize_timestamp(completed_value or last_assistant_timestamp)
        matched_target = capture_target == (session_id, active_turn)
        if (
            completed_at is not None
            and transcript.visible_message_count > 0
            and _cwd_in_scope(cwd, selector["cwd_prefix"])
            and selector["actor"] in {"all", active_actor}
            and (cutoff is None or _timestamp_value(completed_at) >= cutoff)
        ):
            records.append(
                _record_from_turn(
                    provider="claude",
                    actor=active_actor,
                    session_id=session_id,
                    turn_id=active_turn,
                    completed_at=completed_at,
                    root_ref=root_ref,
                    relative_path=relative_path,
                    transcript=transcript,
                )
            )
        active_turn = None
        active_actor = file_actor
        transcript = None
        last_assistant_timestamp = None
        return matched_target

    with path.open(encoding="utf-8", errors="strict") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            base_session_id = record.get("sessionId") or record.get("session_id")
            agent_id = record.get("agentId")
            if session_id is None and base_session_id:
                session_id = (
                    f"{base_session_id}:agent-{agent_id}"
                    if agent_id
                    else str(base_session_id)
                )
            if isinstance(record.get("cwd"), str):
                cwd = record["cwd"]
            timestamp = record.get("timestamp")
            record_type = record.get("type")
            message = record.get("message")
            blocks = _message_blocks(message)
            if record_type == "user":
                message_dict = message if isinstance(message, dict) else {}
                if (
                    record.get("isMeta") is True
                    or record.get("isCompactSummary") is True
                    or message_dict.get("isMeta") is True
                    or message_dict.get("isCompactSummary") is True
                ):
                    continue
                if any(block.get("type") == "tool_result" for block in blocks):
                    continue
                user_text = _visible_message_text(message)
                if not user_text:
                    continue
                if (
                    active_turn
                    and transcript
                    and transcript.visible_message_count
                    and finish(last_assistant_timestamp or timestamp)
                ):
                    break
                turn_value = record.get("uuid") or record.get("promptId")
                if not turn_value:
                    continue
                active_turn = str(turn_value)
                active_actor = (
                    "subagent_task"
                    if (
                        file_actor == "subagent_task"
                        or record.get("isSidechain") is True
                        or bool(agent_id)
                    )
                    else "user_task"
                )
                capture = bool(
                    capture_target
                    and session_id
                    and capture_target == (session_id, active_turn)
                )
                transcript = _Transcript(capture_messages=capture)
                transcript.add("user", user_text)
                continue
            if record_type == "assistant" and active_turn and transcript:
                assistant_text = _visible_message_text(message)
                if assistant_text:
                    transcript.add(
                        "assistant",
                        assistant_text,
                        phase=(
                            "final_answer"
                            if isinstance(message, dict)
                            and message.get("stop_reason")
                            in _TERMINAL_CLAUDE_STOP_REASONS
                            else "commentary"
                        ),
                    )
                    if isinstance(timestamp, str):
                        last_assistant_timestamp = timestamp
                if (
                    isinstance(message, dict)
                    and message.get("stop_reason") in _TERMINAL_CLAUDE_STOP_REASONS
                    and assistant_text
                    and finish(timestamp)
                ):
                    break
                continue
            if (
                record_type == "system"
                and record.get("subtype") == "turn_duration"
                and active_turn
                and finish(last_assistant_timestamp or timestamp)
            ):
                break
    return records


def _parse_file(
    provider: str,
    path: Path,
    *,
    root_ref: str,
    relative_path: str,
    selector: dict[str, Any],
    cutoff: datetime | None,
    capture_target: tuple[str, str] | None = None,
) -> list[dict[str, Any]]:
    if provider == "codex":
        return _codex_records(
            path,
            root_ref=root_ref,
            relative_path=relative_path,
            selector=selector,
            cutoff=cutoff,
            capture_target=capture_target,
        )
    if provider == "claude":
        return _claude_records(
            path,
            root_ref=root_ref,
            relative_path=relative_path,
            selector=selector,
            cutoff=cutoff,
            capture_target=capture_target,
        )
    raise ContextValidationError(
        "unsupported session source provider",
        details={"provider_kind": provider},
    )


def _strip_transient(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if not key.startswith("_")}


def discover_session_records(
    provider: str,
    selector: dict[str, Any],
) -> dict[str, Any]:
    selector = normalize_session_selector(provider, selector)
    cutoff = datetime.now(UTC) - timedelta(days=selector["lookback_days"])
    files, issues, complete = _iter_session_files(
        provider,
        selector,
        cutoff=cutoff,
    )
    observations: dict[str, dict[str, Any]] = {}
    scanned_files = 0
    for root_ref, root, path in files:
        cwd = _peek_cwd(provider, path)
        if cwd is not None and not _cwd_in_scope(cwd, selector["cwd_prefix"]):
            continue
        try:
            relative_path = path.relative_to(root).as_posix()
            records = _parse_file(
                provider,
                path,
                root_ref=root_ref,
                relative_path=relative_path,
                selector=selector,
                cutoff=cutoff,
            )
        except (OSError, UnicodeError):
            issues.append(
                {
                    "root_ref": root_ref,
                    "relative_path": path.name,
                    "reason": "record_read_failed",
                }
            )
            complete = False
            continue
        scanned_files += 1
        for record in records:
            previous = observations.get(record["external_id"])
            if previous is None or (
                record["provider_metadata"]["actor"] == "user_task"
                and previous["provider_metadata"]["actor"] != "user_task"
            ):
                observations[record["external_id"]] = _strip_transient(record)
    ordered = sorted(
        observations.values(),
        key=lambda record: (record["occurred_at"], record["external_id"]),
    )
    if len(ordered) > SESSION_SOURCE_MAX_RECORDS:
        ordered = ordered[-SESSION_SOURCE_MAX_RECORDS:]
        complete = False
        issues.append({"reason": "record_limit_reached"})
    return {
        "provider_kind": provider,
        "selector": selector,
        "records": ordered,
        "complete": complete,
        "scanned_file_count": scanned_files,
        "issue_count": len(issues),
        "issues": issues[:20],
        "issues_truncated": len(issues) > 20,
    }


def _resolve_locator(
    provider: str,
    selector: dict[str, Any],
    locator: dict[str, Any],
) -> tuple[str, Path, Path] | None:
    roots = _provider_roots(provider, selector)
    by_ref = {root_ref: root for root_ref, root in roots}
    root_ref = locator.get("root_ref")
    relative_path = locator.get("relative_path")
    if isinstance(root_ref, str) and isinstance(relative_path, str):
        root = by_ref.get(root_ref)
        if root is not None:
            safe = _safe_provider_file(root, root / relative_path)
            if safe is not None:
                return root_ref, root.resolve(strict=True), safe
    session_id = str(locator.get("session_id") or "")
    filename = Path(str(relative_path or "")).name
    for candidate_ref, root in roots:
        if not root.exists():
            continue
        patterns: Iterable[str]
        if filename:
            patterns = (filename,)
        elif provider == "codex" and session_id:
            patterns = (f"*{session_id}*.jsonl",)
        elif session_id:
            patterns = (f"{session_id.split(':agent-', maxsplit=1)[0]}.jsonl",)
        else:
            patterns = ()
        for pattern in patterns:
            for candidate in root.rglob(pattern):
                safe = _safe_provider_file(root, candidate)
                if safe is not None:
                    return candidate_ref, root.resolve(strict=True), safe
    return None


def fetch_session_record(
    provider: str,
    selector: dict[str, Any],
    *,
    external_id: str,
    provider_metadata: dict[str, Any],
    locator: dict[str, Any],
    expected_freshness_identity: str | None,
    max_chars: int = SESSION_SOURCE_FETCH_DEFAULT_CHARS,
) -> dict[str, Any]:
    if (
        not SESSION_SOURCE_FETCH_MIN_CHARS
        <= max_chars
        <= SESSION_SOURCE_FETCH_MAX_CHARS
    ):
        raise BudgetExceededError(
            "linked session source fetch character budget is invalid",
            details={
                "minimum": SESSION_SOURCE_FETCH_MIN_CHARS,
                "maximum": SESSION_SOURCE_FETCH_MAX_CHARS,
            },
        )
    selector = normalize_session_selector(provider, selector)
    session_id = str(provider_metadata.get("session_id") or "")
    turn_id = str(provider_metadata.get("turn_id") or "")
    if not session_id or not turn_id:
        return {
            "external_id": external_id,
            "freshness_state": "source_unavailable",
            "messages": [],
        }
    resolved = _resolve_locator(provider, selector, locator)
    if resolved is None:
        return {
            "external_id": external_id,
            "provider_kind": provider,
            "freshness_state": "source_unavailable",
            "session_id": session_id,
            "turn_id": turn_id,
            "messages": [],
        }
    root_ref, root, path = resolved
    relative_path = path.relative_to(root).as_posix()
    try:
        records = _parse_file(
            provider,
            path,
            root_ref=root_ref,
            relative_path=relative_path,
            selector=selector,
            cutoff=None,
            capture_target=(session_id, turn_id),
        )
    except (OSError, UnicodeError):
        return {
            "external_id": external_id,
            "provider_kind": provider,
            "freshness_state": "source_unavailable",
            "session_id": session_id,
            "turn_id": turn_id,
            "messages": [],
        }
    record = next(
        (
            candidate
            for candidate in records
            if candidate["provider_metadata"]["session_id"] == session_id
            and candidate["provider_metadata"]["turn_id"] == turn_id
        ),
        None,
    )
    if record is None:
        return {
            "external_id": external_id,
            "provider_kind": provider,
            "freshness_state": "record_not_found",
            "session_id": session_id,
            "turn_id": turn_id,
            "messages": [],
        }
    freshness_state = (
        "valid"
        if expected_freshness_identity == record["freshness_identity"]
        else "source_changed"
    )
    messages = []
    returned_chars = 0
    truncated = False
    for message in record["_messages"]:
        text = message["text"]
        remaining = max_chars - returned_chars
        if remaining <= 0:
            truncated = True
            break
        item = dict(message)
        if len(text) > remaining:
            item["text"] = text[:remaining]
            truncated = True
        messages.append(item)
        returned_chars += len(item["text"])
        if truncated:
            break
    return {
        "external_id": external_id,
        "provider_kind": provider,
        "freshness_state": freshness_state,
        "expected_freshness_identity": expected_freshness_identity,
        "current_freshness_identity": record["freshness_identity"],
        "provider_metadata": record["provider_metadata"],
        "locator": record["locator"],
        "returned_chars": returned_chars,
        "visible_message_count": record["_visible_message_count"],
        "returned_message_count": len(messages),
        "truncated": truncated,
        "messages": messages,
        "untrusted_provider_content": True,
        "tool_records_included": False,
        "reasoning_records_included": False,
    }


def probe_session_record(
    provider: str,
    selector: dict[str, Any],
    *,
    external_id: str,
    provider_metadata: dict[str, Any],
    locator: dict[str, Any],
    expected_freshness_identity: str | None,
) -> str:
    result = fetch_session_record(
        provider,
        selector,
        external_id=external_id,
        provider_metadata=provider_metadata,
        locator=locator,
        expected_freshness_identity=expected_freshness_identity,
        max_chars=SESSION_SOURCE_FETCH_MIN_CHARS,
    )
    return str(result["freshness_state"])
