#!/usr/bin/env python3
"""Project a caller-verified complete Corpus guidance document into one Codex home."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
import tomllib
from pathlib import Path


class ProjectionError(Exception):
    """An expected validation or concurrency failure, safe to report without data."""


def digest(data: bytes | None) -> str:
    return "absent" if data is None else hashlib.sha256(data).hexdigest()


def read_file(path: Path) -> bytes | None:
    if path.is_symlink():
        raise ProjectionError("a managed target must not be a symbolic link")
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def verify_hash(data: bytes | None, expected: str | None, label: str) -> None:
    if expected is not None and digest(data) != expected:
        raise ProjectionError(f"{label} changed; read it again before applying")


def statements(text: str):
    """Locate TOML statements without treating quoted newlines or tables as syntax."""
    start = i = depth = 0
    quote = ""
    while i < len(text):
        if quote:
            if quote[0] == '"' and text[i] == "\\":
                i += 2
                continue
            if text.startswith(quote, i):
                i += len(quote)
                quote = ""
                continue
        elif text[i] in "\"'":
            quote = text[i] * (3 if text.startswith(text[i] * 3, i) else 1)
            i += len(quote)
            continue
        elif text[i] == "#":
            end = text.find("\n", i)
            i = len(text) if end < 0 else end
            continue
        elif text[i] in "[{":
            depth += 1
        elif text[i] in "]}":
            depth -= 1
        if not quote and depth == 0 and text[i] == "\n":
            yield start, i + 1, text[start : i + 1]
            start = i + 1
        i += 1
    if start < len(text):
        yield start, len(text), text[start:]


def patched_config(original: bytes | None, target: Path) -> bytes:
    try:
        text = (original or b"").decode("utf-8")
        parsed = tomllib.loads(text)
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ProjectionError("config.toml must be valid UTF-8 TOML") from exc
    current = parsed.get("model_instructions_file")
    if current is not None and not isinstance(current, str):
        raise ProjectionError("model_instructions_file must be a string")
    value = str(target)
    if current == value:
        return original or b""
    newline = "\r\n" if "\r\n" in text else "\n"
    setting = f"model_instructions_file = {json.dumps(value, ensure_ascii=False)}"
    replacement = None
    for start, end, statement in statements(text):
        stripped = statement.lstrip()
        if stripped.startswith("["):
            break
        if not stripped or stripped.startswith("#"):
            continue
        try:
            item = tomllib.loads(statement)
        except tomllib.TOMLDecodeError as exc:
            raise ProjectionError(
                "could not isolate the root configuration setting"
            ) from exc
        if "model_instructions_file" not in item:
            continue
        match = re.match(
            r"\s*(?:model_instructions_file|\"model_instructions_file\"|'model_instructions_file')\s*=",
            statement,
        )
        if not match or len(item) != 1:
            raise ProjectionError("the existing setting needs a supported manual edit")
        # Keep a trailing comment without interpreting '#' inside the old value.
        tail = ""
        for offset in range(match.end(), len(statement)):
            if statement[offset] != "#":
                continue
            try:
                tomllib.loads(statement[:offset])
            except tomllib.TOMLDecodeError:
                continue
            tail = " " + statement[offset:].rstrip("\r\n")
            break
        ending = newline if statement.endswith("\n") else ""
        replacement = text[:start] + setting + tail + ending + text[end:]
        break
    if replacement is None:
        if current is not None:
            raise ProjectionError("could not locate the root configuration setting")
        replacement = setting + newline + text
    expected = {**parsed, "model_instructions_file": value}
    if tomllib.loads(replacement) != expected:
        raise ProjectionError("configuration validation detected an unrelated change")
    return replacement.encode("utf-8")


def canonical_body(payload: object, args: argparse.Namespace) -> tuple[dict, bytes]:
    if not isinstance(payload, dict):
        raise ProjectionError("the canonical payload must be a JSON object")
    expected = {
        "space_id": args.expected_space_id,
        "document_id": args.expected_document_id,
        "kind": "guidance",
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        raise ProjectionError("canonical document identity or kind does not match")
    version = payload.get("version")
    if (
        isinstance(version, bool)
        or not isinstance(version, (str, int))
        or not str(version)
        or str(version) != args.expected_version
    ):
        raise ProjectionError("canonical document version does not match")
    if (
        payload.get("complete") is not True
        or payload.get("has_more") is not False
        or type(payload.get("start_char")) is not int
        or payload["start_char"] != 0
    ):
        raise ProjectionError("a verified complete current document body is required")
    body = payload.get("body_markdown")
    if not isinstance(body, str) or not body.strip() or "\x00" in body:
        raise ProjectionError("the canonical body must be nonempty UTF-8 text")
    try:
        data = body.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ProjectionError("the canonical body is not valid UTF-8 text") from exc
    if digest(data) != payload.get("body_sha256"):
        raise ProjectionError("canonical body hash does not match the payload")
    verify_hash(data, args.expected_body_sha256, "canonical body")
    return payload, data


def safe_parents(home: Path, parent: Path) -> None:
    for path in (home, *reversed(parent.relative_to(home).parents)):
        candidate = path if path.is_absolute() else home / path
        if candidate.is_symlink():
            raise ProjectionError("a managed directory must not be a symbolic link")
    if parent.is_symlink():
        raise ProjectionError("a managed directory must not be a symbolic link")


def stage(path: Path, data: bytes, mode: int) -> Path:
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    staged = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if staged.read_bytes() != data:
            raise ProjectionError("staged file did not retain the exact bytes")
        return staged
    except BaseException:
        staged.unlink(missing_ok=True)
        raise


def replace_file(path: Path, staged: Path) -> None:
    os.replace(staged, path)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def apply(
    home: Path,
    target: Path,
    config: Path,
    body: bytes,
    config_body: bytes,
    old_body: bytes | None,
    old_config: bytes | None,
) -> None:
    safe_parents(home, target.parent)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = target.parent / ".projection.lock"
    try:
        lock_path.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise ProjectionError(
            "an update lock exists; check for an active or interrupted projection update"
        ) from exc
    staged: list[Path] = []
    retained: set[Path] = set()
    wrote_body = False
    wrote_config = False
    try:
        verify_hash(read_file(target), digest(old_body), "managed projection")
        verify_hash(read_file(config), digest(old_config), "config.toml")
        new_projection = stage(target, body, 0o600)
        staged.append(new_projection)
        mode = stat.S_IMODE(config.stat().st_mode) if old_config is not None else 0o600
        new_config = stage(config, config_body, mode)
        staged.append(new_config)
        previous_projection = (
            stage(target, old_body, 0o600) if old_body is not None else None
        )
        if previous_projection is not None:
            staged.append(previous_projection)
        previous_config = (
            stage(config, old_config, mode) if old_config is not None else None
        )
        if previous_config is not None:
            staged.append(previous_config)
        verify_hash(read_file(target), digest(old_body), "managed projection")
        verify_hash(read_file(config), digest(old_config), "config.toml")
        if body != old_body:
            wrote_body = True
            replace_file(target, new_projection)
        verify_hash(read_file(config), digest(old_config), "config.toml")
        if config_body != old_config:
            wrote_config = True
            replace_file(config, new_config)
        if read_file(target) != body or read_file(config) != config_body:
            raise ProjectionError(
                "post-write verification failed; inspect the current files"
            )
    except BaseException as error:
        # Roll back only bytes this update wrote; preserve concurrent external edits.
        recovery_errors = []
        for wrote, path, data, previous in (
            (wrote_config, config, config_body, locals().get("previous_config")),
            (wrote_body, target, body, locals().get("previous_projection")),
        ):
            if not wrote:
                continue
            try:
                if read_file(path) != data:
                    continue
                if previous is None:
                    path.unlink()
                else:
                    replace_file(path, previous)
            except (OSError, ProjectionError):
                if previous is not None and previous.exists():
                    retained.add(previous)
                recovery_errors.append(str(previous or path))
        if recovery_errors:
            raise ProjectionError(
                "recovery needs inspection; retained copy or target: "
                + ", ".join(recovery_errors)
            ) from error
        raise
    finally:
        for path in staged:
            if path not in retained:
                path.unlink(missing_ok=True)
        lock_path.rmdir()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode", choices=("check", "dry-run", "apply"), default="check"
    )
    for name in ("space-id", "document-id", "version", "body-sha256"):
        parser.add_argument(f"--expected-{name}", required=True)
    parser.add_argument("--expected-old-sha256")
    parser.add_argument("--expected-config-sha256")
    args = parser.parse_args()
    if args.mode == "apply" and (
        args.expected_old_sha256 is None or args.expected_config_sha256 is None
    ):
        parser.error(
            "apply requires the expected old projection and configuration hashes"
        )
    for value in (
        args.expected_body_sha256,
        args.expected_old_sha256,
        args.expected_config_sha256,
    ):
        if (
            value is not None
            and value != "absent"
            and not re.fullmatch("[0-9a-f]{64}", value)
        ):
            parser.error("expected hashes must be lowercase SHA-256 or absent")
    try:
        payload, body = canonical_body(json.load(sys.stdin), args)
        home = Path((os.environ.get("CODEX_HOME") or "~/.codex")).expanduser().absolute()
        target = home / "managed" / "personal-agent-toolkit" / "base-instructions.md"
        config = home / "config.toml"
        safe_parents(home, target.parent)
        old_body, old_config = read_file(target), read_file(config)
        verify_hash(old_body, args.expected_old_sha256, "managed projection")
        verify_hash(old_config, args.expected_config_sha256, "config.toml")
        config_body = patched_config(old_config, target)
        changed = body != old_body or config_body != old_config
        if args.mode == "apply" and changed:
            apply(home, target, config, body, config_body, old_body, old_config)
        print(
            json.dumps(
                {
                    "ok": True,
                    "mode": args.mode,
                    "document_id": payload["document_id"],
                    "version": payload["version"],
                    "managed_path": str(target),
                    "body_sha256": digest(body),
                    "projection_changed": body != old_body,
                    "configuration_changed": config_body != old_config,
                    "connected": not changed or args.mode == "apply",
                }
            )
        )
        return 1 if args.mode == "check" and changed else 0
    except (ProjectionError, OSError, ValueError) as exc:
        message = (
            str(exc)
            if isinstance(exc, ProjectionError)
            else "projection could not be completed"
        )
        print(json.dumps({"ok": False, "error": message}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
