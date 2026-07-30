#!/usr/bin/env python3
"""Validate the public Sense & Corpus release and its empty first-run paths."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import struct
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import tomllib

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAMES = ("sense", "corpus")
EXPECTED_TOOL_COUNTS = {"sense": 5, "corpus": 14}
EXPECTED_SERVER_NAMES = {"sense": "Sense", "corpus": "Corpus"}
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
        "skills/work-with-user/SKILL.md",
        "skills/work-with-user/agents/openai.yaml",
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
    "plugins",
    "scripts",
}
FORBIDDEN_PARTS = {
    ".corpus-data",
    ".local",
    ".pytest_cache",
    ".ruff_cache",
    ".runtime",
    ".venv",
    "__pycache__",
    "staging",
}
FORBIDDEN_SUFFIXES = (
    ".sqlite",
    ".sqlite3",
    ".sqlite-shm",
    ".sqlite-wal",
)
SECRET_PATTERNS = {
    "private-key": re.compile(rb"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"),
    "service-token": re.compile(
        rb"(?<![A-Za-z0-9])(?:sk-(?:proj-|ant-)?|ghp_|github_pat_)[A-Za-z0-9_-]{20,}"
    ),
    "aws-access-key": re.compile(rb"(?<![A-Z0-9])AKIA[0-9A-Z]{16}(?![A-Z0-9])"),
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
            if raw and (ROOT / raw.decode("utf-8")).exists()
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
    ignored_top_level = {".DS_Store", ".git", ".ruff_cache", "__pycache__"}
    actual_top_level = {
        path.name for path in ROOT.iterdir() if path.name not in ignored_top_level
    }
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
        ROOT / "assets/sense-corpus-banner.png",
        ROOT / "examples/sense-profile.example.json",
        ROOT / ".agents/plugins/marketplace.json",
        ROOT / ".claude-plugin/marketplace.json",
    ]
    for package_name in PACKAGE_NAMES:
        package = ROOT / "plugins" / package_name
        required.extend(
            [
                package / "LICENSE",
                package / "NOTICE",
                package / ".codex-plugin/plugin.json",
                package / ".claude-plugin/plugin.json",
                package / ".mcp.json",
                package / "pyproject.toml",
                package / "uv.lock",
                package / "launchers" / package_name,
                package / "launchers" / f"{package_name}-mcp",
            ]
        )
        required.extend(
            package / relative
            for relative in PACKAGE_REQUIRED_FILES[package_name]
        )
    missing = [str(path.relative_to(ROOT)) for path in required if not path.is_file()]
    if missing:
        raise ValueError(f"release is missing required files: {', '.join(missing)}")
    banner = ROOT / "assets/sense-corpus-banner.png"
    if _png_size(banner) != EXPECTED_BANNER_SIZE:
        raise ValueError(
            "README banner has the wrong dimensions: "
            f"{_png_size(banner)} != {EXPECTED_BANNER_SIZE}"
        )
    for package_name in PACKAGE_NAMES:
        package_bin = ROOT / "plugins" / package_name / "bin"
        if package_bin.exists():
            raise ValueError(
                f"{package_name} contains a top-level bin directory, which "
                "Claude-hosted plugins reject"
            )
        package = ROOT / "plugins" / package_name
        if _png_size(package / "assets/icon.png") != (360, 360):
            raise ValueError(f"{package_name} icon has the wrong dimensions")
        if _png_size(package / "assets/logo.png") != (512, 512):
            raise ValueError(f"{package_name} logo has the wrong dimensions")

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
        package_notice = (ROOT / "plugins" / package_name / "NOTICE").read_bytes()
        if package_notice != (ROOT / "NOTICE").read_bytes():
            raise ValueError(f"{package_name} NOTICE differs from the root notice")


def validate_public_boundary() -> None:
    for path in _candidate_files():
        relative = path.relative_to(ROOT)
        parts = relative.parts
        folded = path.name.casefold()
        if folded == ".ds_store":
            raise ValueError(f"Finder metadata is publishable: {relative}")
        if FORBIDDEN_PARTS.intersection(parts):
            raise ValueError(f"private or generated path is publishable: {relative}")
        if folded == ".env" or (
            folded.startswith(".env.") and folded != ".env.example"
        ):
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

    for package_name in PACKAGE_NAMES:
        package = ROOT / "plugins" / package_name
        for launcher_name in (
            package_name,
            f"{package_name}-mcp",
            f"{package_name}-readonly",
        ):
            launcher = package / "launchers" / launcher_name
            if not stat.S_IMODE(launcher.stat().st_mode) & 0o100:
                raise ValueError(f"launcher is not executable: {launcher}")
        for path in package.rglob("*"):
            if path.is_symlink():
                raise ValueError(f"package contains a symbolic link: {path}")


def validate_marketplaces() -> None:
    codex = _json(ROOT / ".agents/plugins/marketplace.json")
    claude = _json(ROOT / ".claude-plugin/marketplace.json")
    if codex.get("name") != "sense-corpus" or claude.get("name") != "sense-corpus":
        raise ValueError("marketplace identities must both be sense-corpus")

    expected_names = list(PACKAGE_NAMES)
    codex_plugins = codex.get("plugins")
    claude_plugins = claude.get("plugins")
    if not isinstance(codex_plugins, list) or not isinstance(claude_plugins, list):
        raise TypeError("marketplace plugin collections must be arrays")
    if [item.get("name") for item in codex_plugins] != expected_names:
        raise ValueError("Codex marketplace package order is invalid")
    if [item.get("name") for item in claude_plugins] != expected_names:
        raise ValueError("Claude marketplace package order is invalid")

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


def validate_package_manifests() -> dict[str, str]:
    build_ids: dict[str, str] = {}
    for package_name in PACKAGE_NAMES:
        package = ROOT / "plugins" / package_name
        codex = _json(package / ".codex-plugin/plugin.json")
        claude = _json(package / ".claude-plugin/plugin.json")
        mcp = _json(package / ".mcp.json")
        project = tomllib.loads(
            (package / "pyproject.toml").read_text(encoding="utf-8")
        )["project"]

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
        if codex.get("author", {}).get("name") != "Sense & Corpus contributors":
            raise ValueError(f"{package_name} Codex author is not public")
        if claude.get("author", {}).get("name") != "Sense & Corpus contributors":
            raise ValueError(f"{package_name} Claude author is not public")

        base_version = project.get("version")
        build_version = codex.get("version")
        prefix = f"{base_version}+codex."
        if not isinstance(build_version, str) or not build_version.startswith(prefix):
            raise ValueError(f"{package_name} build version is invalid")
        build_ids[package_name] = build_version.removeprefix(prefix)
        server = mcp.get("mcpServers", {}).get(package_name, {})
        if server.get("command") != (
            f"${{CLAUDE_PLUGIN_ROOT}}/launchers/{package_name}-mcp"
        ):
            raise ValueError(f"{package_name} Claude MCP command is invalid")
        if claude.get("mcpServers") != "./.mcp.json":
            raise ValueError(f"{package_name} Claude manifest does not use .mcp.json")
        interface = codex.get("interface", {})
        if interface.get("composerIcon") != "./assets/icon.png":
            raise ValueError(f"{package_name} Codex composer icon is invalid")
        if interface.get("logo") != "./assets/logo.png":
            raise ValueError(f"{package_name} Codex logo is invalid")

    if len(set(build_ids.values())) != 1:
        raise ValueError(f"packages do not share one build ID: {build_ids}")
    build_id = next(iter(build_ids.values()))
    if any(marker in build_id.casefold() for marker in ("test", "validation", "audit")):
        raise ValueError(f"release uses a non-release build ID: {build_id}")
    return {
        name: _json(ROOT / "plugins" / name / ".codex-plugin/plugin.json")["version"]
        for name in PACKAGE_NAMES
    }


def _package_tree_manifest(package: Path) -> dict[str, tuple[Any, ...]]:
    manifest: dict[str, tuple[Any, ...]] = {}
    for path in sorted(
        package.rglob("*"),
        key=lambda candidate: candidate.relative_to(package).as_posix(),
    ):
        relative = path.relative_to(package).as_posix()
        if path.is_symlink():
            manifest[relative] = ("symlink", os.readlink(path))
        elif path.is_dir():
            manifest[relative] = ("directory",)
        elif path.is_file():
            data = path.read_bytes()
            manifest[relative] = (
                "file",
                len(data),
                hashlib.sha256(data).hexdigest(),
            )
        else:
            manifest[relative] = ("other",)
    return manifest


def _mcp_handshake(package_name: str, temporary_root: Path) -> None:
    package = ROOT / "plugins" / package_name
    requests = (
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {
                    "name": "sense-corpus-release-validation",
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
    )
    environment = os.environ.copy()
    environment.pop("VIRTUAL_ENV", None)
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONDONTWRITEBYTECODE", None)
    environment.pop("CORPUS_ENABLE_SEMANTIC_CACHE_TOOLS", None)
    environment["SENSE_DATA_DIR"] = str(temporary_root / "Sense")
    environment["SENSE_PYTHON_ENV"] = str(temporary_root / "sense-python")
    environment["CORPUS_DATA_DIR"] = str(temporary_root / "Corpus")
    environment["CORPUS_PYTHON_ENV"] = str(temporary_root / "corpus-python")

    completed = subprocess.run(
        [str(package / "launchers" / f"{package_name}-mcp")],
        input="".join(json.dumps(request) + "\n" for request in requests),
        text=True,
        capture_output=True,
        env=environment,
        timeout=180,
        check=True,
    )
    responses = [
        json.loads(line) for line in completed.stdout.splitlines() if line.strip()
    ]
    initialize = next(item for item in responses if item.get("id") == 1)
    tools = next(item for item in responses if item.get("id") == 2)["result"]["tools"]
    if (
        initialize["result"]["serverInfo"]["name"]
        != EXPECTED_SERVER_NAMES[package_name]
    ):
        raise ValueError(f"{package_name} MCP server identity is invalid")
    if len(tools) != EXPECTED_TOOL_COUNTS[package_name]:
        raise ValueError(f"{package_name} MCP tool count is invalid")
    if not all(
        tool.get("inputSchema", {}).get("type") == "object"
        and tool.get("outputSchema", {}).get("type") == "object"
        for tool in tools
    ):
        raise ValueError(f"{package_name} MCP schemas are not object-shaped")


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
    source.write_text(
        "# Example\n\nA synthetic first-run document.\n", encoding="utf-8"
    )
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


def validate_runtime_smoke() -> None:
    packages_before = {
        package_name: _package_tree_manifest(ROOT / "plugins" / package_name)
        for package_name in PACKAGE_NAMES
    }
    with tempfile.TemporaryDirectory(
        prefix="sense-corpus-release-validation-"
    ) as temporary:
        root = Path(temporary)
        for package_name in PACKAGE_NAMES:
            _mcp_handshake(package_name, root / f"{package_name}-mcp")
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
            "runtime smoke changed the public package projection: "
            + ", ".join(changed[:12])
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
