#!/usr/bin/env python3
"""Validate the public Personal Agent Toolkit release."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import selectors
import stat
import struct
import subprocess
import tempfile
import time
import tomllib
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAMES = ("sense", "corpus", "hypes")
MCP_PACKAGE_NAMES = PACKAGE_NAMES
CLAUDE_PACKAGE_NAMES = PACKAGE_NAMES
MARKETPLACE_NAME = "personal-agent-toolkit"
PUBLIC_PUBLISHER = "Ruzzy77"
GATEWAY_SOURCE_REPOSITORY = "owners/remote-runtime"
GIT_OBJECT_ID_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
BUILD_IDENTITY_FIELDS = frozenset(
    {
        "PACKAGE_VERSION",
        "BUILD_ID",
        "SOURCE_REPOSITORY",
        "SOURCE_COMMIT",
        "SOURCE_CLEAN",
    }
)
GIT_SAFE_CONFIG = (
    "core.trustctime=true",
    "core.checkStat=default",
    "core.ignoreStat=false",
    "core.fileMode=true",
    "core.fsmonitor=false",
    "core.untrackedCache=false",
    "core.ignoreCase=false",
    "core.precomposeUnicode=false",
    "core.symlinks=true",
    "core.autocrlf=false",
    "core.eol=lf",
    f"core.attributesFile={os.devnull}",
    f"core.excludesFile={os.devnull}",
)
REGULAR_GIT_MODES = frozenset({"100644", "100755"})
REQUIREMENT_NAME_RE = re.compile(r"^\s*([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)")
PACKAGE_DESCRIPTIONS = {
    "sense": "Keep private guidance for important choices available across AI tools.",
    "corpus": "Connect saved questions and relationships to their exact current sources.",
    "hypes": "Give the assistant a private, revisable relationship model of the user.",
}
HOST_MARKER_FILES = {".codex-marketplace-install.json"}
EXPECTED_TOOL_NAMES = {
    "sense": {
        "sense_read",
        "sense_overview",
        "sense_preview_revision",
        "sense_revise",
        "sense_revise_batch",
        "sense_control",
        "sense_status",
    },
    "corpus": {
        "corpus_space_list",
        "corpus_space_get",
        "corpus_space_search",
        "corpus_file_list",
        "corpus_file_read",
        "corpus_file_write",
        "corpus_file_select_current",
        "corpus_file_restore",
    },
    "hypes": {
        "hypes_read",
        "hypes_rewrite",
    },
}
EXPECTED_TOOL_COUNTS = {name: len(tools) for name, tools in EXPECTED_TOOL_NAMES.items()}
EXPECTED_SERVER_NAMES = {"sense": "Sense", "corpus": "Corpus", "hypes": "Hypes"}
EXPECTED_SKILL_NAMES = {
    "sense": {"update-sense", "use-sense"},
    "corpus": {
        "investigate-corpus",
        "show-corpus-overview",
        "work-in-corpus-folder",
    },
    "hypes": {"use-user-model"},
}
PACKAGE_LAUNCHERS = {
    "sense": ("sense", "sense-mcp", "sense-readonly"),
    "corpus": ("corpus", "corpus-mcp", "corpus-readonly"),
    "hypes": ("hypes-mcp",),
}
EXPECTED_BANNER_SIZE = (1536, 768)
PACKAGE_REQUIRED_FILES = {
    "sense": (
        "NOTICE",
        "assets/icon.png",
        "assets/icon.svg",
        "assets/logo.png",
        "launchers/sense-readonly",
        "skills/update-sense/SKILL.md",
        "skills/update-sense/agents/openai.yaml",
        "skills/use-sense/SKILL.md",
        "skills/use-sense/agents/openai.yaml",
    ),
    "corpus": (
        "NOTICE",
        "UPDATE_CONTINUITY.md",
        "assets/icon.png",
        "assets/icon.svg",
        "assets/logo.png",
        "launchers/corpus-readonly",
        "skills/investigate-corpus/SKILL.md",
        "skills/investigate-corpus/agents/openai.yaml",
        "skills/show-corpus-overview/SKILL.md",
        "skills/show-corpus-overview/agents/openai.yaml",
        "skills/show-corpus-overview/references/overview-visual-spec.md",
        "skills/work-in-corpus-folder/SKILL.md",
        "skills/work-in-corpus-folder/agents/openai.yaml",
    ),
    "hypes": (
        "assets/icon.png",
        "assets/icon.svg",
        "assets/logo.png",
        "src/hypes/__init__.py",
        "src/hypes/_build.py",
        "src/hypes/errors.py",
        "src/hypes/model.py",
        "src/hypes/store.py",
        "src/hypes/service.py",
        "src/hypes/mcp_server.py",
        "skills/use-user-model/SKILL.md",
        "skills/use-user-model/agents/openai.yaml",
    ),
}
EXPECTED_TOP_LEVEL = {
    ".agents",
    ".claude-plugin",
    ".gitattributes",
    ".gitignore",
    "LICENSE",
    "NOTICE",
    "PRIVACY.md",
    "README.md",
    "THIRD_PARTY_NOTICES.md",
    "assets",
    "examples",
    "gateway",
    "plugins",
    "ruff.toml",
    "scripts",
}
FORBIDDEN_PARTS = {
    ".corpus-data",
    ".hypes-data",
    ".local",
    ".pytest_cache",
    ".ruff_cache",
    ".runtime",
    ".sense-data",
    ".venv",
    "__pycache__",
    "staging",
}
FORBIDDEN_SUFFIXES = (
    ".db",
    ".db-journal",
    ".db-shm",
    ".db-wal",
    ".sqlite",
    ".sqlite-journal",
    ".sqlite3",
    ".sqlite3-journal",
    ".sqlite-shm",
    ".sqlite-wal",
    ".sqlite3-shm",
    ".sqlite3-wal",
)
SECRET_PATTERNS = {
    "private-key": re.compile(rb"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"),
    "service-token": re.compile(
        rb"\b(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9_-]{20,}|"
        rb"github_pat_[A-Za-z0-9_-]{20,}|xox[baprs]-[A-Za-z0-9-]{20,}|"
        rb"glpat-[A-Za-z0-9_-]{20,}|hf_[A-Za-z0-9]{20,}|"
        rb"npm_[A-Za-z0-9]{20,})\b"
    ),
    "aws-access-key": re.compile(rb"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "google-api-key": re.compile(rb"\bAIza[0-9A-Za-z_-]{25,}\b"),
}
PUBLIC_BLOCKERS = {
    "maintainer-home": b"/" + b"Users" + b"/",
    "maintainer-email": b"hiyaq77" + b"@" + b"gmail.com",
    "retired-owner-name": b"Agent" + b"-Commons",
    "retired-owner-id": b"agent" + b"-commons",
}
PROVIDER_LOCATOR_RE = re.compile(
    rb"(?:thread|codex-session|claude-session)://"
    rb"[0-9a-f]{8}-[0-9a-f-]{27,}",
    re.IGNORECASE,
)


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path.relative_to(ROOT)} must contain one JSON object")
    return value


def _png_size(path: Path) -> tuple[int, int]:
    header = path.read_bytes()[:24]
    if header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        raise ValueError(f"{path.relative_to(ROOT)} is not a PNG")
    return struct.unpack(">II", header[16:24])


def _candidate_files() -> list[Path]:
    if (ROOT / ".git").is_dir():
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(ROOT),
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
            ],
            check=True,
            capture_output=True,
        )
        return [
            ROOT / raw.decode("utf-8")
            for raw in completed.stdout.split(b"\0")
            if raw
            and raw.decode("utf-8") not in HOST_MARKER_FILES
            and (ROOT / raw.decode("utf-8")).exists()
        ]
    return [
        path
        for path in ROOT.rglob("*")
        if path.is_file()
        and ".git" not in path.relative_to(ROOT).parts
        and not FORBIDDEN_PARTS.intersection(path.relative_to(ROOT).parts)
        and path.name != ".DS_Store"
    ]


def validate_structure() -> None:
    ignored_top_level = {
        ".DS_Store",
        ".git",
        ".ruff_cache",
        "__pycache__",
        *HOST_MARKER_FILES,
    }
    actual_top_level = {path.name for path in ROOT.iterdir() if path.name not in ignored_top_level}
    if actual_top_level != EXPECTED_TOP_LEVEL:
        missing = sorted(EXPECTED_TOP_LEVEL - actual_top_level)
        extra = sorted(actual_top_level - EXPECTED_TOP_LEVEL)
        raise ValueError(f"unexpected release root; missing={missing}, extra={extra}")

    required = [
        ROOT / "LICENSE",
        ROOT / "NOTICE",
        ROOT / "README.md",
        ROOT / "PRIVACY.md",
        ROOT / "THIRD_PARTY_NOTICES.md",
        ROOT / "ruff.toml",
        ROOT / "assets/personal-agent-toolkit-banner.png",
        ROOT / "examples/sense-profile.example.json",
        ROOT / ".agents/plugins/marketplace.json",
        ROOT / ".claude-plugin/marketplace.json",
        ROOT / "gateway/.personal-agent-gateway-release.json",
        ROOT / "gateway/GUIDE.md",
        ROOT / "gateway/LICENSE",
        ROOT / "gateway/NOTICE",
        ROOT / "gateway/README.md",
        ROOT / "gateway/pyproject.toml",
        ROOT / "gateway/uv.lock",
    ]
    for package_name in PACKAGE_NAMES:
        package = ROOT / "plugins" / package_name
        required.extend(
            [
                package / "LICENSE",
                package / "NOTICE",
                package / ".codex-plugin/plugin.json",
                package / "src" / package_name / "_build.py",
            ]
        )
        if package_name in CLAUDE_PACKAGE_NAMES:
            required.append(package / ".claude-plugin/plugin.json")
        if package_name in MCP_PACKAGE_NAMES:
            required.extend(
                [
                    package / ".mcp.json",
                    package / "pyproject.toml",
                    package / "uv.lock",
                ]
            )
            required.extend(
                package / "launchers" / launcher_name
                for launcher_name in PACKAGE_LAUNCHERS[package_name]
            )
        required.extend(package / relative for relative in PACKAGE_REQUIRED_FILES[package_name])
    missing = [str(path.relative_to(ROOT)) for path in required if not path.is_file()]
    if missing:
        raise ValueError(f"release is missing required files: {', '.join(missing)}")
    banner = ROOT / "assets/personal-agent-toolkit-banner.png"
    if _png_size(banner) != EXPECTED_BANNER_SIZE:
        raise ValueError(
            f"README banner has the wrong dimensions: {_png_size(banner)} != {EXPECTED_BANNER_SIZE}"
        )
    for package_name in PACKAGE_NAMES:
        package = ROOT / "plugins" / package_name
        if _png_size(package / "assets/icon.png") != (360, 360):
            raise ValueError(f"{package_name} icon has the wrong dimensions")
        if _png_size(package / "assets/logo.png") != (512, 512):
            raise ValueError(f"{package_name} logo has the wrong dimensions")

    for package_name in MCP_PACKAGE_NAMES:
        package = ROOT / "plugins" / package_name
        package_bin = package / "bin"
        if package_bin.exists():
            raise ValueError(
                f"{package_name} contains a top-level bin directory, which "
                "Claude-hosted plugins reject"
            )
        if any(package.rglob(".app.json")):
            raise ValueError(
                f"{package_name} unexpectedly contains a remote ChatGPT registration; "
                "this release is local-only"
            )

    root_license = (ROOT / "LICENSE").read_bytes()
    if (
        b"Apache License" not in root_license
        or b"Version 2.0, January 2004" not in root_license
        or b"END OF TERMS AND CONDITIONS" not in root_license
    ):
        raise ValueError("root LICENSE is not the complete Apache-2.0 text")
    for package_name in PACKAGE_NAMES:
        package_license = (ROOT / "plugins" / package_name / "LICENSE").read_bytes()
        if package_license != root_license:
            raise ValueError(f"{package_name} LICENSE differs from the root license")
        package_notice = (ROOT / "plugins" / package_name / "NOTICE").read_text(encoding="utf-8")
        if "Copyright 2026" not in package_notice:
            raise ValueError(f"{package_name} NOTICE has no copyright line")


def validate_public_boundary() -> None:
    for path in _candidate_files():
        relative = path.relative_to(ROOT)
        parts = relative.parts
        folded = path.name.casefold()
        if folded == ".ds_store":
            raise ValueError(f"Finder metadata is publishable: {relative}")
        if FORBIDDEN_PARTS.intersection(parts):
            raise ValueError(f"private or generated path is publishable: {relative}")
        if folded == ".env" or (folded.startswith(".env.") and folded != ".env.example"):
            raise ValueError(f"environment file is publishable: {relative}")
        if folded.endswith(FORBIDDEN_SUFFIXES):
            raise ValueError(f"runtime database is publishable: {relative}")
        if path.is_symlink():
            raise ValueError(f"symbolic link is publishable: {relative}")

        data = path.read_bytes()
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(data):
                raise ValueError(f"{label} pattern found in {relative}")
        for label, marker in PUBLIC_BLOCKERS.items():
            if marker in data:
                raise ValueError(f"{label} found in {relative}")
        if PROVIDER_LOCATOR_RE.search(data):
            raise ValueError(f"concrete provider locator found in {relative}")

    for package_name in MCP_PACKAGE_NAMES:
        package = ROOT / "plugins" / package_name
        for launcher_name in PACKAGE_LAUNCHERS[package_name]:
            launcher = package / "launchers" / launcher_name
            if not stat.S_IMODE(launcher.stat().st_mode) & 0o100:
                raise ValueError(f"launcher is not executable: {launcher}")
        for path in package.rglob("*"):
            if path.is_symlink():
                raise ValueError(f"package contains a symbolic link: {path}")


def _gateway_content_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        relative_text = path.relative_to(root).as_posix()
        if relative_text == ".personal-agent-gateway-release.json":
            continue
        relative = relative_text.encode("utf-8")
        if path.is_symlink():
            raise ValueError("gateway release contains a symbolic link")
        digest.update(b"D" if path.is_dir() else b"F")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        if path.is_file():
            data = path.read_bytes()
            digest.update(len(data).to_bytes(8, "big"))
            digest.update(data)
            digest.update(stat.S_IMODE(path.stat().st_mode).to_bytes(2, "big"))
    return digest.hexdigest()


def _git_environment() -> dict[str, str]:
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
        }
    )
    return environment


def _git_base_command() -> list[str]:
    command = ["git"]
    for value in GIT_SAFE_CONFIG:
        command.extend(("-c", value))
    return command


def _resolved_source_root(source_root: Path, *, subject: str) -> Path:
    try:
        return source_root.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"{subject} source Git provenance is unavailable") from exc


def _git_command(source_root: Path, *arguments: str) -> list[str]:
    resolved_root = source_root.resolve(strict=True)
    return [
        *_git_base_command(),
        "-C",
        str(resolved_root),
        f"--work-tree={resolved_root}",
        *arguments,
    ]


def _unbound_git_bytes(source_root: Path, *arguments: str) -> bytes:
    try:
        resolved_root = source_root.resolve(strict=True)
        result = subprocess.run(
            [*_git_base_command(), "-C", str(resolved_root), *arguments],
            check=False,
            capture_output=True,
            timeout=10,
            env=_git_environment(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("source Git provenance is unavailable") from exc
    if result.returncode != 0:
        raise ValueError("source Git provenance is unavailable")
    return result.stdout


def _git_bytes(source_root: Path, *arguments: str) -> bytes:
    try:
        result = subprocess.run(
            _git_command(source_root, *arguments),
            check=False,
            capture_output=True,
            timeout=10,
            env=_git_environment(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("source Git provenance is unavailable") from exc
    if result.returncode != 0:
        raise ValueError("source Git provenance is unavailable")
    return result.stdout


def _git_ignore_source(source_root: Path, relative: str, *, subject: str) -> str | None:
    input_bytes = relative.encode("utf-8") + b"\0"
    try:
        result = subprocess.run(
            _git_command(source_root, "check-ignore", "-v", "-z", "--stdin"),
            check=False,
            capture_output=True,
            input=input_bytes,
            timeout=10,
            env=_git_environment(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"{subject} source Git provenance is unavailable") from exc
    if result.returncode == 1 and result.stdout == b"":
        return None
    if result.returncode != 0 or not isinstance(result.stdout, bytes):
        raise ValueError(f"{subject} source Git provenance is unavailable")
    fields = result.stdout.split(b"\0")
    if len(fields) != 5 or fields[-1] != b"" or fields[3] != input_bytes[:-1]:
        raise ValueError(f"{subject} source Git ignore result is invalid")
    if fields[2].startswith(b"!"):
        return None
    try:
        return fields[0].decode("utf-8")
    except UnicodeError as exc:
        raise ValueError(f"{subject} source Git ignore result is invalid") from exc


def _git_output(source_root: Path, *arguments: str) -> str:
    try:
        return _git_bytes(source_root, *arguments).decode("utf-8").strip()
    except UnicodeError as exc:
        raise ValueError("source Git provenance is unavailable") from exc


def _require_exact_repository_root(source_root: Path, *, subject: str) -> Path:
    resolved_root = _resolved_source_root(source_root, subject=subject)
    try:
        raw_repository_root = _unbound_git_bytes(
            resolved_root,
            "rev-parse",
            "--show-toplevel",
        ).decode("utf-8")
        repository_root = Path(raw_repository_root.strip()).resolve(strict=True)
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"{subject} source Git provenance is unavailable") from exc
    if repository_root != resolved_root:
        raise ValueError(f"{subject} source is not its Git repository root")
    return resolved_root


def _validate_source_checkout(
    source_root: Path,
    *,
    expected_commit: str,
    subject: str,
) -> None:
    resolved_root = _require_exact_repository_root(source_root, subject=subject)

    first_tree = _validate_source_snapshot(
        resolved_root,
        expected_commit=expected_commit,
        subject=subject,
    )
    second_tree = _validate_source_snapshot(
        resolved_root,
        expected_commit=expected_commit,
        subject=subject,
    )
    if second_tree != first_tree:
        raise ValueError(f"{subject} source changed during validation")


def _validate_source_snapshot(
    source_root: Path,
    *,
    expected_commit: str,
    subject: str,
) -> dict[str, tuple[str, str]]:
    resolved_root = _require_exact_repository_root(source_root, subject=subject)
    if _git_output(resolved_root, "rev-parse", "--verify", "HEAD^{commit}") != expected_commit:
        raise ValueError(f"{subject} source commit does not match the release")

    index = _git_bytes(source_root, "ls-files", "-v", "-z")
    if index and not index.endswith(b"\0"):
        raise ValueError(f"{subject} source Git index is invalid")
    entries = index[:-1].split(b"\0") if index else []
    if any(
        len(entry) < 3 or entry[1:2] != b" " or not entry.startswith(b"H ") for entry in entries
    ):
        raise ValueError(f"{subject} source Git index uses hidden or unsupported flags")

    commit_tree = _source_commit_tree(resolved_root, expected_commit, subject=subject)
    if _source_index_tree(resolved_root, subject=subject) != commit_tree:
        raise ValueError(f"{subject} source Git tree is not clean")
    for relative, (mode, object_id) in commit_tree.items():
        path, _metadata = _exact_source_file(resolved_root, relative, subject=subject)
        data, metadata = _read_raw_source_file(
            path,
            relative,
            subject=subject,
        )
        if bool(metadata.st_mode & 0o111) != (mode == "100755"):
            raise ValueError(f"{subject} source Git tree is not clean")
        if data != _git_bytes(resolved_root, "cat-file", "blob", object_id):
            raise ValueError(f"{subject} source Git tree is not clean")

    _require_safe_local_excludes(resolved_root, subject=subject)
    _require_tracked_ignore_files(
        resolved_root,
        commit_tree,
        subject=subject,
    )
    if _git_bytes(
        resolved_root,
        "ls-files",
        "--others",
        "--exclude-per-directory=.gitignore",
        "-z",
    ):
        raise ValueError(f"{subject} source Git tree is not clean")
    if _source_index_tree(resolved_root, subject=subject) != commit_tree:
        raise ValueError(f"{subject} source Git tree is not clean")
    if _git_output(resolved_root, "rev-parse", "--verify", "HEAD^{commit}") != expected_commit:
        raise ValueError(f"{subject} source changed during validation")
    _require_exact_repository_root(resolved_root, subject=subject)
    return commit_tree


def _require_safe_local_excludes(source_root: Path, *, subject: str) -> None:
    raw_path = _git_output(source_root, "rev-parse", "--git-path", "info/exclude")
    path = Path(raw_path)
    if not path.is_absolute():
        path = source_root / path
    if not path.exists():
        return
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{subject} source Git tree is not clean")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"{subject} source Git tree is not clean") from exc
    if any(line and not line.startswith("#") for line in lines):
        raise ValueError(f"{subject} source Git tree is not clean")


def _require_tracked_ignore_files(
    source_root: Path,
    commit_tree: dict[str, tuple[str, str]],
    *,
    subject: str,
) -> None:
    tracked_ignore_files = {
        relative for relative in commit_tree if PurePosixPath(relative).name == ".gitignore"
    }
    pending: list[tuple[Path, str]] = [(source_root, "")]
    while pending:
        directory, prefix = pending.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError as exc:
            raise ValueError(f"{subject} source Git tree is not clean") from exc
        for entry in entries:
            if not prefix and entry.name == ".git":
                continue
            relative = f"{prefix}/{entry.name}" if prefix else entry.name
            if entry.name.casefold() == ".gitignore" and (
                entry.name != ".gitignore" or relative not in tracked_ignore_files
            ):
                raise ValueError(f"{subject} source Git tree is not clean")
        for entry in entries:
            if not prefix and entry.name == ".git":
                continue
            try:
                is_directory = entry.is_dir(follow_symlinks=False)
            except OSError as exc:
                raise ValueError(f"{subject} source Git tree is not clean") from exc
            if not is_directory:
                continue
            relative = f"{prefix}/{entry.name}" if prefix else entry.name
            if _git_ignore_source(source_root, relative, subject=subject) in tracked_ignore_files:
                continue
            pending.append((Path(entry.path), relative))


def _source_commit_tree(
    source_root: Path,
    commit: str,
    *,
    subject: str,
) -> dict[str, tuple[str, str]]:
    output = _git_bytes(source_root, "ls-tree", "-r", "-z", "--full-tree", commit)
    if output and not output.endswith(b"\0"):
        raise ValueError(f"{subject} source Git tree is invalid")
    tree: dict[str, tuple[str, str]] = {}
    for raw_entry in output[:-1].split(b"\0") if output else []:
        try:
            metadata, raw_path = raw_entry.split(b"\t", 1)
            mode_bytes, object_type, object_id = metadata.split(b" ", 2)
            relative = raw_path.decode("utf-8")
            mode = mode_bytes.decode("ascii")
            object_id_text = object_id.decode("ascii")
        except (UnicodeError, ValueError) as exc:
            raise ValueError(f"{subject} source Git tree is invalid") from exc
        normalized = PurePosixPath(relative)
        if (
            normalized.as_posix() != relative
            or normalized.is_absolute()
            or any(part in {"", ".", ".."} for part in normalized.parts)
            or unicodedata.normalize("NFC", relative) != relative
            or object_type != b"blob"
            or mode not in REGULAR_GIT_MODES
            or GIT_OBJECT_ID_RE.fullmatch(object_id_text) is None
            or relative in tree
        ):
            raise ValueError(f"{subject} source Git tree is invalid")
        tree[relative] = (mode, object_id_text)
    return tree


def _source_index_tree(
    source_root: Path,
    *,
    subject: str,
) -> dict[str, tuple[str, str]]:
    output = _git_bytes(source_root, "ls-files", "--stage", "-z")
    if output and not output.endswith(b"\0"):
        raise ValueError(f"{subject} source Git index is invalid")
    index: dict[str, tuple[str, str]] = {}
    for raw_entry in output[:-1].split(b"\0") if output else []:
        try:
            metadata, raw_path = raw_entry.split(b"\t", 1)
            mode_bytes, object_id, stage = metadata.split(b" ", 2)
            relative = raw_path.decode("utf-8")
            mode = mode_bytes.decode("ascii")
            object_id_text = object_id.decode("ascii")
        except (UnicodeError, ValueError) as exc:
            raise ValueError(f"{subject} source Git index is invalid") from exc
        if (
            stage != b"0"
            or mode not in REGULAR_GIT_MODES
            or GIT_OBJECT_ID_RE.fullmatch(object_id_text) is None
            or relative in index
        ):
            raise ValueError(f"{subject} source Git index is invalid")
        index[relative] = (mode, object_id_text)
    return index


def _exact_source_file(
    source_root: Path,
    relative: str,
    *,
    subject: str,
) -> tuple[Path, os.stat_result]:
    current = source_root
    parts = PurePosixPath(relative).parts
    for index, part in enumerate(parts):
        try:
            with os.scandir(current) as scanner:
                entry = next(
                    (candidate for candidate in scanner if candidate.name == part),
                    None,
                )
        except OSError as exc:
            raise ValueError(f"{subject} source Git tree is not clean") from exc
        if entry is None:
            raise ValueError(f"{subject} source Git tree is not clean")
        if index != len(parts) - 1:
            if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                raise ValueError(f"{subject} source Git tree is not clean")
            current = Path(entry.path)
            continue
        try:
            return Path(entry.path), entry.stat(follow_symlinks=False)
        except OSError as exc:
            raise ValueError(f"{subject} source Git tree is not clean") from exc
    raise ValueError(f"{subject} source Git tree is not clean")


def _read_raw_source_file(
    path: Path,
    relative: str,
    *,
    subject: str,
) -> tuple[bytes, os.stat_result]:
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"{subject} source Git tree is not clean") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"{subject} source Git tree is not clean")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)

    def identity(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
        return (
            value.st_dev,
            value.st_ino,
            value.st_mode,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
        )

    if identity(before) != identity(after):
        raise ValueError(f"{subject} source Git tree is not clean")
    return b"".join(chunks), before


def _read_package_build_identity(
    package_name: str,
    *,
    base_version: str,
    build_version: str,
) -> dict[str, object]:
    path = ROOT / "plugins" / package_name / "src" / package_name / "_build.py"
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeError) as exc:
        raise ValueError(f"{package_name} build identity is invalid") from exc

    expected_docstring = f"Generated {package_name.title()} provider-package identity."
    if len(tree.body) != len(BUILD_IDENTITY_FIELDS) + 1:
        raise ValueError(f"{package_name} build identity is invalid")
    docstring = tree.body[0]
    if (
        not isinstance(docstring, ast.Expr)
        or not isinstance(docstring.value, ast.Constant)
        or docstring.value.value != expected_docstring
    ):
        raise ValueError(f"{package_name} build identity is invalid")

    values: dict[str, object] = {}
    for statement in tree.body[1:]:
        if (
            not isinstance(statement, ast.Assign)
            or len(statement.targets) != 1
            or not isinstance(statement.targets[0], ast.Name)
            or not isinstance(statement.value, ast.Constant)
        ):
            raise ValueError(f"{package_name} build identity is invalid")
        name = statement.targets[0].id
        if name in values:
            raise ValueError(f"{package_name} build identity is invalid")
        values[name] = statement.value.value
    if set(values) != BUILD_IDENTITY_FIELDS:
        raise ValueError(f"{package_name} build identity is invalid")

    expected_repository = f"owners/{package_name}"
    commit = values.get("SOURCE_COMMIT")
    if (
        values.get("PACKAGE_VERSION") != base_version
        or values.get("BUILD_ID") != build_version
        or values.get("SOURCE_REPOSITORY") != expected_repository
        or not isinstance(commit, str)
        or GIT_OBJECT_ID_RE.fullmatch(commit) is None
        or values.get("SOURCE_CLEAN") is not True
    ):
        raise ValueError(f"{package_name} build identity is invalid")

    if ROOT.name == "public" and ROOT.parent.name == "distribution":
        source_root = ROOT.parent.parent / expected_repository
        if source_root.exists():
            _validate_source_checkout(
                source_root,
                expected_commit=commit,
                subject=package_name,
            )
    return values


def validate_gateway_release() -> None:
    gateway = ROOT / "gateway"
    gateway_metadata = gateway.lstat()
    if not stat.S_ISDIR(gateway_metadata.st_mode):
        raise ValueError("gateway release root is invalid")
    expected_root = {
        ".personal-agent-gateway-release.json",
        "GUIDE.md",
        "LICENSE",
        "NOTICE",
        "README.md",
        "launchers",
        "pyproject.toml",
        "src",
        "uv.lock",
    }
    if {path.name for path in gateway.iterdir()} != expected_root:
        raise ValueError("gateway release root is invalid")
    expected_tree = {
        ".personal-agent-gateway-release.json",
        "GUIDE.md",
        "LICENSE",
        "NOTICE",
        "README.md",
        "launchers",
        "launchers/personal-agent-tunnel",
        "launchers/personal-agent-tunnel-gateway",
        "launchers/personal-agent-tunnel-service",
        "pyproject.toml",
        "src",
        "src/personal_agent_remote",
        "src/personal_agent_remote/__init__.py",
        "src/personal_agent_remote/installed_products.py",
        "src/personal_agent_remote/tunnel.py",
        "src/personal_agent_remote/tunnel_gateway.py",
        "src/personal_agent_remote/tunnel_service.py",
        "uv.lock",
    }
    actual_tree = {path.relative_to(gateway).as_posix() for path in gateway.rglob("*")}
    if actual_tree != expected_tree:
        missing = sorted(expected_tree - actual_tree)
        unexpected = sorted(actual_tree - expected_tree)
        raise ValueError(
            f"gateway release tree is invalid; missing={missing}, unexpected={unexpected}"
        )
    expected_directories = {
        "launchers",
        "src",
        "src/personal_agent_remote",
    }
    for relative in expected_tree:
        metadata = (gateway / relative).lstat()
        if relative in expected_directories:
            valid_type = stat.S_ISDIR(metadata.st_mode)
        else:
            valid_type = stat.S_ISREG(metadata.st_mode)
        if not valid_type:
            raise ValueError(f"gateway release entry has the wrong type: {relative}")
    project = tomllib.loads((gateway / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    if project.get("name") != "personal-agent-tunnel-gateway":
        raise ValueError("gateway package identity is invalid")
    dependencies = project.get("dependencies")
    if not isinstance(dependencies, list) or not all(
        isinstance(value, str) for value in dependencies
    ):
        raise ValueError("gateway package dependencies are invalid")
    dependency_names: set[str] = set()
    for value in dependencies:
        match = REQUIREMENT_NAME_RE.match(value)
        if match is None:
            raise ValueError("gateway package dependencies are invalid")
        dependency_names.add(re.sub(r"[-_.]+", "-", match.group(1)).casefold())
    product_names = {re.sub(r"[-_.]+", "-", name).casefold() for name in PACKAGE_NAMES}
    if dependency_names.intersection(product_names):
        raise ValueError("gateway package must not install product packages")
    expected_modules = {
        "__init__.py",
        "installed_products.py",
        "tunnel.py",
        "tunnel_gateway.py",
        "tunnel_service.py",
    }
    module_root = gateway / "src/personal_agent_remote"
    actual_modules = {path.name for path in module_root.iterdir()}
    if actual_modules != expected_modules:
        missing = sorted(expected_modules - actual_modules)
        unexpected = sorted(actual_modules - expected_modules)
        raise ValueError(
            "gateway package contains the wrong runtime modules; "
            f"missing={missing}, unexpected={unexpected}"
        )
    expected_launchers = {
        "personal-agent-tunnel",
        "personal-agent-tunnel-gateway",
        "personal-agent-tunnel-service",
    }
    launcher_root = gateway / "launchers"
    if {path.name for path in launcher_root.iterdir()} != expected_launchers:
        raise ValueError("gateway package contains the wrong launchers")
    for launcher in expected_launchers:
        path = launcher_root / launcher
        if stat.S_IMODE(path.stat().st_mode) != 0o755:
            raise ValueError(f"gateway launcher is not executable: {launcher}")
        text = path.read_text(encoding="utf-8")
        for marker in ("--frozen", "--no-dev", "--no-install-project", "umask 077"):
            if marker not in text:
                raise ValueError(f"gateway launcher is missing {marker}: {launcher}")
    sentinel = _json(gateway / ".personal-agent-gateway-release.json")
    if set(sentinel) != {
        "content_sha256",
        "format",
        "schema_version",
        "source",
        "version",
    }:
        raise ValueError("gateway release sentinel is invalid")
    source = sentinel.get("source")
    if (
        sentinel.get("format") != "personal-agent-tunnel-gateway-release"
        or sentinel.get("schema_version") != 2
        or sentinel.get("version") != "0.2.0"
        or not isinstance(sentinel.get("content_sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", sentinel["content_sha256"]) is None
        or not isinstance(source, dict)
        or set(source) != {"clean", "commit", "repository"}
        or source.get("repository") != GATEWAY_SOURCE_REPOSITORY
        or not isinstance(source.get("commit"), str)
        or GIT_OBJECT_ID_RE.fullmatch(source["commit"]) is None
        or source.get("clean") is not True
    ):
        raise ValueError("gateway release sentinel is invalid")
    if sentinel["content_sha256"] != _gateway_content_digest(gateway):
        raise ValueError("gateway release content digest is invalid")

    if ROOT.name == "public" and ROOT.parent.name == "distribution":
        source_root = ROOT.parent.parent / GATEWAY_SOURCE_REPOSITORY
        if source_root.exists():
            _validate_source_checkout(
                source_root,
                expected_commit=source["commit"],
                subject="gateway",
            )


def validate_marketplaces() -> None:
    codex = _json(ROOT / ".agents/plugins/marketplace.json")
    claude = _json(ROOT / ".claude-plugin/marketplace.json")
    if codex.get("name") != MARKETPLACE_NAME or claude.get("name") != MARKETPLACE_NAME:
        raise ValueError(f"marketplace identities must both be {MARKETPLACE_NAME}")

    expected_codex_names = list(PACKAGE_NAMES)
    expected_claude_names = list(CLAUDE_PACKAGE_NAMES)
    codex_plugins = codex.get("plugins")
    claude_plugins = claude.get("plugins")
    if not isinstance(codex_plugins, list) or not isinstance(claude_plugins, list):
        raise TypeError("marketplace plugin collections must be arrays")
    if [item.get("name") for item in codex_plugins] != expected_codex_names:
        raise ValueError("Codex marketplace package order is invalid")
    if [item.get("name") for item in claude_plugins] != expected_claude_names:
        raise ValueError("Claude marketplace package order is invalid")
    if claude.get("owner", {}).get("name") != PUBLIC_PUBLISHER:
        raise ValueError("Claude marketplace publisher is invalid")

    for item in codex_plugins:
        name = item["name"]
        if item.get("source") != {
            "source": "local",
            "path": f"./plugins/{name}",
        }:
            raise ValueError(f"Codex {name} source is invalid")
        if item.get("policy") != {
            "installation": "AVAILABLE",
            "authentication": "ON_INSTALL",
        }:
            raise ValueError(f"Codex {name} install policy is invalid")
    for item in claude_plugins:
        name = item["name"]
        if item.get("source") != f"./plugins/{name}":
            raise ValueError(f"Claude {name} source is invalid")
        if item.get("description") != PACKAGE_DESCRIPTIONS[name]:
            raise ValueError(f"Claude {name} description is invalid")


def validate_package_manifests() -> dict[str, str]:
    build_ids: dict[str, str] = {}
    for package_name in MCP_PACKAGE_NAMES:
        package = ROOT / "plugins" / package_name
        codex = _json(package / ".codex-plugin/plugin.json")
        claude = _json(package / ".claude-plugin/plugin.json")
        mcp = _json(package / ".mcp.json")
        project = tomllib.loads((package / "pyproject.toml").read_text(encoding="utf-8"))["project"]

        if codex.get("name") != package_name or claude.get("name") != package_name:
            raise ValueError(f"{package_name} provider identities differ")
        if codex.get("version") != claude.get("version"):
            raise ValueError(f"{package_name} provider versions differ")
        if codex.get("license") != "Apache-2.0":
            raise ValueError(f"{package_name} Codex manifest license is invalid")
        if claude.get("license") != "Apache-2.0":
            raise ValueError(f"{package_name} Claude manifest license is invalid")
        if project.get("license") != "Apache-2.0":
            raise ValueError(f"{package_name} Python package license is invalid")
        if project.get("license-files") != ["LICENSE", "NOTICE"]:
            raise ValueError(f"{package_name} Python package must include LICENSE and NOTICE")
        if codex.get("author", {}).get("name") != PUBLIC_PUBLISHER:
            raise ValueError(f"{package_name} Codex author is not public")
        if claude.get("author", {}).get("name") != PUBLIC_PUBLISHER:
            raise ValueError(f"{package_name} Claude author is not public")
        if codex.get("description") != PACKAGE_DESCRIPTIONS[package_name]:
            raise ValueError(f"{package_name} Codex description is invalid")
        if claude.get("description") != PACKAGE_DESCRIPTIONS[package_name]:
            raise ValueError(f"{package_name} Claude description is invalid")

        base_version = project.get("version")
        build_version = codex.get("version")
        if not isinstance(base_version, str) or not base_version:
            raise ValueError(f"{package_name} base version is invalid")
        prefix = f"{base_version}+codex."
        if not isinstance(build_version, str) or not build_version.startswith(prefix):
            raise ValueError(f"{package_name} build version is invalid")
        _read_package_build_identity(
            package_name,
            base_version=base_version,
            build_version=build_version,
        )
        build_ids[package_name] = build_version.removeprefix(prefix)
        dependencies = project.get("dependencies")
        if not isinstance(dependencies, list) or not any(
            isinstance(value, str) and value.startswith("mcp>=2") for value in dependencies
        ):
            raise ValueError(f"{package_name} does not require MCP SDK 2.x")
        if not any(
            isinstance(value, str) and value.startswith("pydantic>=2") for value in dependencies
        ):
            raise ValueError(f"{package_name} does not declare its Pydantic 2.x dependency")
        lock_text = (package / "uv.lock").read_text(encoding="utf-8")
        if 'name = "mcp"' not in lock_text:
            raise ValueError(f"{package_name} lockfile does not contain MCP")
        if 'name = "pydantic"' not in lock_text:
            raise ValueError(f"{package_name} lockfile does not contain Pydantic")
        scripts = project.get("scripts")
        if not isinstance(scripts, dict) or scripts.get(f"{package_name}-mcp") != (
            f"{package_name}.mcp_server:main"
        ):
            raise ValueError(f"{package_name} Python MCP entry point is invalid")

        codex_servers = codex.get("mcpServers")
        if not isinstance(codex_servers, dict) or set(codex_servers) != {package_name}:
            raise ValueError(f"{package_name} Codex manifest must expose one MCP server")
        codex_server = codex_servers[package_name]
        if codex_server != {
            "command": f"./launchers/{package_name}-mcp",
            "args": [],
            "cwd": ".",
        }:
            raise ValueError(f"{package_name} Codex MCP command is invalid")
        if "apps" in codex:
            raise ValueError(
                f"{package_name} must not include a remote app registration in this local release"
            )
        claude_servers = mcp.get("mcpServers")
        if not isinstance(claude_servers, dict) or set(claude_servers) != {package_name}:
            raise ValueError(f"{package_name} Claude manifest must expose one MCP server")
        server = claude_servers[package_name]
        if server != {
            "command": f"${{CLAUDE_PLUGIN_ROOT}}/launchers/{package_name}-mcp",
            "args": [],
        }:
            raise ValueError(f"{package_name} Claude MCP command is invalid")
        if claude.get("mcpServers") != "./.mcp.json":
            raise ValueError(f"{package_name} Claude manifest does not use .mcp.json")
        if codex.get("skills") != "./skills/":
            raise ValueError(f"{package_name} Codex skills path is invalid")
        skill_names = {
            path.parent.name for path in (package / "skills").glob("*/SKILL.md") if path.is_file()
        }
        if skill_names != EXPECTED_SKILL_NAMES[package_name]:
            raise ValueError(
                f"{package_name} public skill inventory is invalid: "
                f"{', '.join(sorted(skill_names))}"
            )
        interface = codex.get("interface", {})
        if interface.get("composerIcon") != "./assets/icon.png":
            raise ValueError(f"{package_name} Codex composer icon is invalid")
        if interface.get("logo") != "./assets/logo.png":
            raise ValueError(f"{package_name} Codex logo is invalid")

    for package_name, build_id in build_ids.items():
        if any(marker in build_id.casefold() for marker in ("test", "validation", "audit")):
            raise ValueError(f"{package_name} uses a non-release build ID: {build_id}")
    hypes_package = ROOT / "plugins/hypes"
    hypes_skill = " ".join(
        (hypes_package / "skills/use-user-model/SKILL.md").read_text(encoding="utf-8").split()
    )
    hypes_agent = (hypes_package / "skills/use-user-model/agents/openai.yaml").read_text(
        encoding="utf-8"
    )
    required_hypes_contract = {
        "Answer the subject directly": "does not answer the subject directly",
        "Keep the facts that change the answer": ("does not preserve decision-relevant content"),
        "Treat the visible conversation as sufficient by default": (
            "does not use the visible conversation as its default"
        ),
        "existing relation could materially change a": (
            "does not limit ontology reads to material response changes"
        ),
        "directly states the reusable relation to create": (
            "does not notice interactions that change the user model"
        ),
        "Make no Hypes call": ("does not stay out of unrelated conversations"),
        "The user's current message always takes priority": (
            "does not give the current message priority over the model"
        ),
        "Branches 2 and 3 write only": (
            "does not limit writes to changes in the agent's user model"
        ),
        "next_action_if_relationship_required": (
            "does not follow bounded relationship-read recovery"
        ),
        "task-local terms, equivalences, facts": (
            "does not keep task-local premises out of the relationship model"
        ),
        "Do not write merely because a turn or task completed": (
            "writes ordinary conversation completion into the ontology"
        ),
        "Prefer rewriting over accumulation": ("does not prefer model rewriting over accumulation"),
        "never the transcript": ("does not exclude conversation transcripts from the ontology"),
        "Ask at most one focused question": ("does not limit understanding checks"),
        "For a finished artifact, follow its genre, reader, and argument": (
            "does not preserve finished-artifact guidance"
        ),
        "describe only observable effects": ("does not keep Hypes explanations observable"),
    }
    for marker, failure in required_hypes_contract.items():
        if marker not in hypes_skill:
            raise ValueError(f"Hypes skill {failure}")
    if "allow_implicit_invocation: true" not in hypes_agent:
        raise ValueError("Hypes skill does not allow implicit invocation")
    for retired in ("adapt-response", "recommend-help", "run-hypes-task"):
        if (hypes_package / "skills" / retired).exists():
            raise ValueError(f"retired Hypes skill remains: {retired}")

    return {
        name: _json(ROOT / "plugins" / name / ".codex-plugin/plugin.json")["version"]
        for name in PACKAGE_NAMES
    }


def _package_tree_manifest(package: Path) -> dict[str, tuple[Any, ...]]:
    root_metadata = package.lstat()
    if stat.S_ISLNK(root_metadata.st_mode):
        manifest: dict[str, tuple[Any, ...]] = {".": ("symlink", os.readlink(package))}
    elif stat.S_ISDIR(root_metadata.st_mode):
        manifest = {".": ("directory", stat.S_IMODE(root_metadata.st_mode))}
    else:
        manifest = {".": ("other", stat.S_IMODE(root_metadata.st_mode))}
    for path in sorted(
        package.rglob("*"),
        key=lambda candidate: candidate.relative_to(package).as_posix(),
    ):
        relative = path.relative_to(package).as_posix()
        if path.is_symlink():
            manifest[relative] = ("symlink", os.readlink(path))
        elif path.is_dir():
            manifest[relative] = (
                "directory",
                stat.S_IMODE(path.stat().st_mode),
            )
        elif path.is_file():
            data = path.read_bytes()
            manifest[relative] = (
                "file",
                stat.S_IMODE(path.stat().st_mode),
                len(data),
                hashlib.sha256(data).hexdigest(),
            )
        else:
            manifest[relative] = ("other",)
    return manifest


def _read_mcp_response(
    process: subprocess.Popen[str],
    *,
    expected_id: int,
    package_name: str,
    timeout_seconds: float = 60.0,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if process.stdout is None:
        raise TypeError("MCP validation process has no stdout pipe")
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout_seconds
    responses: list[dict[str, Any]] = []
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selector.select(remaining):
                raise TimeoutError(
                    f"{package_name} MCP response {expected_id} timed out; "
                    f"response_ids={[item.get('id') for item in responses]}"
                )
            line = process.stdout.readline()
            if not line:
                raise ValueError(
                    f"{package_name} MCP closed stdout before response {expected_id}; "
                    f"response_ids={[item.get('id') for item in responses]}"
                )
            value = json.loads(line)
            if not isinstance(value, dict):
                raise TypeError(f"{package_name} MCP emitted a non-object response")
            responses.append(value)
            if value.get("id") == expected_id:
                return value, responses
    finally:
        selector.close()


def _mcp_handshake(package_name: str, temporary_root: Path) -> None:
    package = ROOT / "plugins" / package_name
    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {
                    "name": "personal-agent-toolkit-release-validation",
                    "version": "1",
                },
            },
        },
        {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        },
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        },
    ]
    environment = os.environ.copy()
    environment.pop("VIRTUAL_ENV", None)
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONDONTWRITEBYTECODE", None)
    environment.pop("CORPUS_ENABLE_SEMANTIC_CACHE_TOOLS", None)
    for prefix in ("SENSE", "CORPUS", "HYPES"):
        for suffix in ("TRANSPORT", "HOST", "PORT", "PATH"):
            environment.pop(f"{prefix}_MCP_{suffix}", None)
    environment["SENSE_DATA_DIR"] = str(temporary_root / "Sense")
    environment["SENSE_PYTHON_ENV"] = str(temporary_root / "sense-python")
    environment["CORPUS_DATA_DIR"] = str(temporary_root / "Corpus")
    environment["CORPUS_PYTHON_ENV"] = str(temporary_root / "corpus-python")
    environment["HYPES_DATA_ROOT"] = str(temporary_root / "Hypes")
    environment["HYPES_PYTHON_ENV"] = str(temporary_root / "hypes-python")

    process = subprocess.Popen(
        [str(package / "launchers" / f"{package_name}-mcp")],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
        bufsize=1,
    )
    if process.stdin is None:
        process.kill()
        raise TypeError("MCP validation process has no stdin pipe")
    handshake_complete = False
    corpus_space_list_response: dict[str, Any] | None = None
    try:
        process.stdin.write(json.dumps(requests[0]) + "\n")
        process.stdin.flush()
        initialize, _ = _read_mcp_response(process, expected_id=1, package_name=package_name)
        process.stdin.write(json.dumps(requests[1]) + "\n")
        process.stdin.write(json.dumps(requests[2]) + "\n")
        process.stdin.flush()
        tool_response, _ = _read_mcp_response(process, expected_id=2, package_name=package_name)
        try:
            tools = tool_response["result"]["tools"]
        except (KeyError, TypeError) as exc:
            raise ValueError(
                f"{package_name} MCP tools/list response is invalid: {tool_response}"
            ) from exc
        if package_name == "corpus":
            process.stdin.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "tools/call",
                        "params": {
                            "name": "corpus_space_list",
                            "arguments": {},
                        },
                    }
                )
                + "\n"
            )
            process.stdin.flush()
            corpus_space_list_response, _ = _read_mcp_response(
                process,
                expected_id=3,
                package_name=package_name,
            )
        handshake_complete = True
    finally:
        process.stdin.close()
        try:
            return_code = process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            return_code = process.wait(timeout=10)
        stderr = process.stderr.read() if process.stderr is not None else ""
        if handshake_complete and return_code != 0:
            raise ValueError(f"{package_name} MCP launcher exited {return_code}: {stderr.strip()}")
    if initialize["result"]["serverInfo"]["name"] != EXPECTED_SERVER_NAMES[package_name]:
        raise ValueError(f"{package_name} MCP server identity is invalid")
    tool_names = {tool.get("name") for tool in tools}
    if len(tools) != EXPECTED_TOOL_COUNTS[package_name]:
        raise ValueError(f"{package_name} MCP tool count is invalid")
    if tool_names != EXPECTED_TOOL_NAMES[package_name]:
        raise ValueError(
            f"{package_name} MCP tools are invalid: "
            f"expected={sorted(EXPECTED_TOOL_NAMES[package_name])}, "
            f"actual={sorted(str(name) for name in tool_names)}"
        )
    if not all(
        tool.get("inputSchema", {}).get("type") == "object"
        and tool.get("outputSchema", {}).get("type") == "object"
        for tool in tools
    ):
        raise ValueError(f"{package_name} MCP schemas are not object-shaped")
    required_annotations = {"readOnlyHint", "destructiveHint", "openWorldHint"}
    if not all(required_annotations.issubset(tool.get("annotations", {})) for tool in tools):
        raise ValueError(f"{package_name} MCP tool annotations are incomplete")
    if package_name == "corpus":
        try:
            structured = corpus_space_list_response["result"]["structuredContent"]
            surface_revision = structured["result"]["surface_revision"]
        except (KeyError, TypeError) as exc:
            raise ValueError(
                "corpus_space_list did not return a structured surface revision"
            ) from exc
        if structured.get("ok") is not True or surface_revision != "space-v2":
            raise ValueError("Corpus public package must expose surface_revision=space-v2")


def validate_sessionless_server_source(package_name: str) -> None:
    source = (ROOT / "plugins" / package_name / "src" / package_name / "mcp_server.py").read_text(
        encoding="utf-8"
    )
    required = (
        '"stdio"',
        '"streamable-http"',
        "stateless_http=True",
        "json_response=True",
        "127.0.0.1",
        "is_loopback",
    )
    missing = [marker for marker in required if marker not in source]
    if missing:
        raise ValueError(
            f"{package_name} MCP server is missing sessionless local HTTP guards: "
            + ", ".join(missing)
        )


def _run_json(
    command: list[str],
    *,
    environment: dict[str, str] | None = None,
    expected_exit: int = 0,
) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        text=True,
        capture_output=True,
        env=environment,
        timeout=180,
        check=False,
    )
    if completed.returncode != expected_exit:
        raise ValueError(
            f"command exited {completed.returncode}, expected {expected_exit}: "
            f"{' '.join(command)}\n{completed.stderr}"
        )
    value = json.loads(completed.stdout)
    if not isinstance(value, dict):
        raise TypeError(f"command did not return one JSON object: {' '.join(command)}")
    return value


def validate_sense_first_run(temporary_root: Path) -> None:
    package = ROOT / "plugins/sense"
    launcher = str(package / "launchers/sense")
    data_root = temporary_root / "Sense"
    environment = os.environ.copy()
    environment.pop("VIRTUAL_ENV", None)
    environment.pop("PYTHONDONTWRITEBYTECODE", None)
    environment["SENSE_PYTHON_ENV"] = str(temporary_root / "sense-python")

    empty = _run_json(
        [launcher, "--data-root", str(data_root), "status"],
        environment=environment,
        expected_exit=1,
    )
    if empty.get("error", {}).get("code") != "profile_not_found":
        raise ValueError("Sense empty state did not return profile_not_found")
    if data_root.exists():
        raise ValueError("Sense empty read created private runtime state")

    imported = _run_json(
        [
            launcher,
            "--data-root",
            str(data_root),
            "import-profile",
            "--input",
            str(ROOT / "examples/sense-profile.example.json"),
        ],
        environment=environment,
    )
    preview = imported["result"]
    if preview.get("lifecycle") != "preview" or preview.get("revision") != 1:
        raise ValueError("Sense example did not import as revision 1 preview")

    _run_json(
        [launcher, "--data-root", str(data_root), "read", "--view", "full"],
        environment=environment,
    )
    status_result = _run_json(
        [launcher, "--data-root", str(data_root), "status"],
        environment=environment,
    )["result"]
    activated = _run_json(
        [
            launcher,
            "--data-root",
            str(data_root),
            "activate",
            "--expected-revision",
            str(status_result["revision"]),
            "--confirm-profile-digest",
            status_result["profile_sha256"],
            "--confirm-reviewed-profile",
        ],
        environment=environment,
    )
    if activated["result"].get("lifecycle") != "active":
        raise ValueError("Sense preview did not activate")
    if stat.S_IMODE(data_root.stat().st_mode) != 0o700:
        raise ValueError("Sense data directory is not private")
    if stat.S_IMODE((data_root / "sense.sqlite3").stat().st_mode) != 0o600:
        raise ValueError("Sense database is not private")


def validate_corpus_first_run(temporary_root: Path) -> None:
    package = ROOT / "plugins/corpus"
    launcher = str(package / "launchers/corpus")
    data_root = temporary_root / "Corpus"
    source_root = temporary_root / "example-source"
    temporary_root.mkdir(parents=True, mode=0o700)
    source_root.mkdir(mode=0o700)
    source = source_root / "note.md"
    source.write_text("# Example\n\nA synthetic first-run document.\n", encoding="utf-8")
    source_digest = hashlib.sha256(source.read_bytes()).hexdigest()

    environment = os.environ.copy()
    environment.pop("VIRTUAL_ENV", None)
    environment.pop("PYTHONDONTWRITEBYTECODE", None)
    environment["CORPUS_PYTHON_ENV"] = str(temporary_root / "corpus-python")

    empty = _run_json(
        [launcher, "--data-root", str(data_root), "corpus", "list"],
        environment=environment,
    )
    if empty != {"ok": True, "result": {"corpora": []}}:
        raise ValueError("Corpus did not start with an empty corpus list")
    if data_root.exists():
        raise ValueError("Corpus empty list created private runtime state")

    added = _run_json(
        [
            launcher,
            "--data-root",
            str(data_root),
            "corpus",
            "add",
            "--id",
            "example",
            "--root",
            str(source_root),
            "--execution-policy",
            "local_only",
            "--exclude-directory-name",
            ".git",
        ],
        environment=environment,
    )
    if added.get("ok") is not True:
        raise ValueError("Corpus first registration failed")
    scanned = _run_json(
        [
            launcher,
            "--data-root",
            str(data_root),
            "scan",
            "--corpus",
            "example",
        ],
        environment=environment,
    )
    if scanned.get("ok") is not True:
        raise ValueError("Corpus first metadata scan failed")
    if hashlib.sha256(source.read_bytes()).hexdigest() != source_digest:
        raise ValueError("Corpus changed the registered source")
    if stat.S_IMODE(data_root.stat().st_mode) != 0o700:
        raise ValueError("Corpus data directory is not private")


def validate_hypes_first_run(temporary_root: Path) -> None:
    package = ROOT / "plugins/hypes"
    environment = os.environ.copy()
    environment.pop("VIRTUAL_ENV", None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONPATH"] = str(package / "src")
    environment["HYPES_DATA_ROOT"] = str(temporary_root / "Hypes")
    read_call = """
import asyncio
import json
import os
from pathlib import Path

from hypes.mcp_server import create_server

server = create_server(Path(os.environ["HYPES_DATA_ROOT"]))
response = asyncio.run(server.call_tool("hypes_read", {}))
print(json.dumps(response.structured_content))
"""
    response = _run_json(
        [str(temporary_root / "hypes-python/bin/python"), "-c", read_call],
        environment=environment,
    )
    if response.get("ok") is not True:
        raise ValueError("Hypes MCP read call failed")
    graph = response.get("result")
    if graph != {
        "nodes": [],
        "predicates": [],
        "edges": [],
        "continuation": None,
        "read_state": {
            "read_mode": "outline",
            "slice_state": "empty",
            "next_action_if_relationship_required": "stop_without_widening",
            "continuation_action": "none",
        },
    }:
        raise ValueError("Hypes first read did not return an empty ontology")

    data_root = temporary_root / "Hypes"
    database = data_root / "hypes-ontology.sqlite3"
    if not data_root.is_dir() or not database.is_file():
        raise ValueError("Hypes read did not create its isolated private store")
    if stat.S_IMODE(data_root.stat().st_mode) != 0o700:
        raise ValueError("Hypes data directory is not private")
    if stat.S_IMODE(database.stat().st_mode) != 0o600:
        raise ValueError("Hypes database is not private")


def validate_runtime_smoke() -> None:
    packages_before = {
        package_name: _package_tree_manifest(ROOT / "plugins" / package_name)
        for package_name in PACKAGE_NAMES
    }
    with tempfile.TemporaryDirectory(
        prefix="personal-agent-toolkit-release-validation-"
    ) as temporary:
        root = Path(temporary)
        for package_name in MCP_PACKAGE_NAMES:
            temporary_root = root / f"{package_name}-mcp"
            _mcp_handshake(package_name, temporary_root)
            validate_sessionless_server_source(package_name)
            if package_name == "hypes":
                validate_hypes_first_run(temporary_root)
        validate_sense_first_run(root / "sense-first-run")
        validate_corpus_first_run(root / "corpus-first-run")
    packages_after = {
        package_name: _package_tree_manifest(ROOT / "plugins" / package_name)
        for package_name in PACKAGE_NAMES
    }
    if packages_after != packages_before:
        changed: list[str] = []
        for package_name in PACKAGE_NAMES:
            before = packages_before[package_name]
            after = packages_after[package_name]
            changed.extend(
                f"{package_name}/{relative}"
                for relative in sorted(set(before) | set(after))
                if before.get(relative) != after.get(relative)
            )
        raise ValueError(
            "runtime smoke changed the public package projection: " + ", ".join(changed[:12])
        )


def validate_git_history() -> None:
    if not (ROOT / ".git").is_dir():
        return
    history = subprocess.run(
        [
            "git",
            "-C",
            str(ROOT),
            "log",
            "-p",
            "--full-history",
            "--no-ext-diff",
            "--text",
            "--format=",
            "HEAD",
        ],
        check=True,
        capture_output=True,
    ).stdout
    for label, pattern in SECRET_PATTERNS.items():
        if pattern.search(history):
            raise ValueError(f"{label} pattern found in reachable Git history")
    for label, marker in PUBLIC_BLOCKERS.items():
        if marker in history:
            raise ValueError(f"{label} found in reachable Git history")
    if PROVIDER_LOCATOR_RE.search(history):
        raise ValueError("concrete provider locator found in reachable Git history")


def main() -> None:
    validate_structure()
    validate_public_boundary()
    validate_gateway_release()
    validate_marketplaces()
    versions = validate_package_manifests()
    validate_runtime_smoke()
    validate_git_history()
    print(
        json.dumps(
            {
                "ok": True,
                "license": "Apache-2.0",
                "packages": versions,
                "user_data_included": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
