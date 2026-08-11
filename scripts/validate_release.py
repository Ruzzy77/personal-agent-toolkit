#!/usr/bin/env python3
"""Validate the public Personal Agent Toolkit release."""

from __future__ import annotations

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
from pathlib import Path
from typing import Any

import tomllib

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAMES = ("sense", "corpus", "hypes")
MCP_PACKAGE_NAMES = PACKAGE_NAMES
CLAUDE_PACKAGE_NAMES = PACKAGE_NAMES
MARKETPLACE_NAME = "personal-agent-toolkit"
PUBLIC_PUBLISHER = "Ruzzy77"
PACKAGE_DESCRIPTIONS = {
    "sense": "Keep one private, user-controlled work profile available across AI tools.",
    "corpus": (
        "Find the work context and original sources behind a task, "
        "then carry that context into later work."
    ),
    "hypes": (
        "Adapt responses and carry forward only stable, scoped explanation "
        "clues at natural commitment points."
    ),
}
HOST_MARKER_FILES = {".codex-marketplace-install.json"}
EXPECTED_TOOL_NAMES = {
    "sense": {
        "sense_read",
        "sense_overview",
        "sense_revise",
        "sense_control",
        "sense_status",
    },
    "corpus": {
        "corpus_list",
        "corpus_overview",
        "corpus_status",
        "corpus_inventory",
        "corpus_search_candidates",
        "corpus_read",
        "corpus_source_read",
        "corpus_source_fetch",
        "corpus_source_update",
        "context_read",
        "context_update",
        "corpus_sync",
        "corpus_scan",
        "corpus_refresh",
    },
    "hypes": {
        "hypes_read",
        "hypes_mark_recheck",
        "hypes_revise",
        "hypes_overview",
        "hypes_preview_forget",
        "hypes_forget",
        "hypes_status",
    },
}
EXPECTED_TOOL_COUNTS = {
    name: len(tools) for name, tools in EXPECTED_TOOL_NAMES.items()
}
EXPECTED_SERVER_NAMES = {"sense": "Sense", "corpus": "Corpus", "hypes": "Hypes"}
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
        "skills/adapt-response/SKILL.md",
        "skills/adapt-response/agents/openai.yaml",
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
        required.extend(
            package / relative
            for relative in PACKAGE_REQUIRED_FILES[package_name]
        )
    missing = [str(path.relative_to(ROOT)) for path in required if not path.is_file()]
    if missing:
        raise ValueError(f"release is missing required files: {', '.join(missing)}")
    banner = ROOT / "assets/personal-agent-toolkit-banner.png"
    if _png_size(banner) != EXPECTED_BANNER_SIZE:
        raise ValueError(
            "README banner has the wrong dimensions: "
            f"{_png_size(banner)} != {EXPECTED_BANNER_SIZE}"
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
        package_notice = (ROOT / "plugins" / package_name / "NOTICE").read_text(
            encoding="utf-8"
        )
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


def validate_gateway_release() -> None:
    gateway = ROOT / "gateway"
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
    project = tomllib.loads((gateway / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]
    if project.get("name") != "personal-agent-tunnel-gateway":
        raise ValueError("gateway package identity is invalid")
    dependencies = project.get("dependencies")
    if not isinstance(dependencies, list) or any(
        value.split("=", 1)[0].casefold() in PACKAGE_NAMES
        for value in dependencies
        if isinstance(value, str)
    ):
        raise ValueError("gateway package must not install product packages")
    expected_modules = {
        "__init__.py",
        "installed_products.py",
        "tunnel.py",
        "tunnel_gateway.py",
        "tunnel_service.py",
    }
    module_root = gateway / "src/personal_agent_remote"
    if {path.name for path in module_root.iterdir()} != expected_modules:
        raise ValueError("gateway package contains the wrong runtime modules")
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
    if sentinel != {
        "format": "personal-agent-tunnel-gateway-release",
        "schema_version": 1,
        "version": "0.1.0",
        "content_sha256": sentinel.get("content_sha256"),
    }:
        raise ValueError("gateway release sentinel is invalid")
    if sentinel["content_sha256"] != _gateway_content_digest(gateway):
        raise ValueError("gateway release content digest is invalid")


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
        if project.get("license-files") != ["LICENSE", "NOTICE"]:
            raise ValueError(
                f"{package_name} Python package must include LICENSE and NOTICE"
            )
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
        prefix = f"{base_version}+codex."
        if not isinstance(build_version, str) or not build_version.startswith(prefix):
            raise ValueError(f"{package_name} build version is invalid")
        build_ids[package_name] = build_version.removeprefix(prefix)
        dependencies = project.get("dependencies")
        if not isinstance(dependencies, list) or not any(
            isinstance(value, str) and value.startswith("mcp>=2")
            for value in dependencies
        ):
            raise ValueError(f"{package_name} does not require MCP SDK 2.x")
        if not any(
            isinstance(value, str) and value.startswith("pydantic>=2")
            for value in dependencies
        ):
            raise ValueError(
                f"{package_name} does not declare its Pydantic 2.x dependency"
            )
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
            raise ValueError(
                f"{package_name} Codex manifest must expose one MCP server"
            )
        codex_server = codex_servers[package_name]
        if codex_server != {
            "command": f"./launchers/{package_name}-mcp",
            "args": [],
            "cwd": ".",
        }:
            raise ValueError(f"{package_name} Codex MCP command is invalid")
        if "apps" in codex:
            raise ValueError(
                f"{package_name} must not include a remote app registration "
                "in this local release"
            )
        claude_servers = mcp.get("mcpServers")
        if not isinstance(claude_servers, dict) or set(claude_servers) != {
            package_name
        }:
            raise ValueError(
                f"{package_name} Claude manifest must expose one MCP server"
            )
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
        interface = codex.get("interface", {})
        if interface.get("composerIcon") != "./assets/icon.png":
            raise ValueError(f"{package_name} Codex composer icon is invalid")
        if interface.get("logo") != "./assets/logo.png":
            raise ValueError(f"{package_name} Codex logo is invalid")

    for package_name, build_id in build_ids.items():
        if any(
            marker in build_id.casefold()
            for marker in ("test", "validation", "audit")
        ):
            raise ValueError(
                f"{package_name} uses a non-release build ID: {build_id}"
            )
    hypes_package = ROOT / "plugins/hypes"
    hypes_skill = (hypes_package / "skills/adapt-response/SKILL.md").read_text(
        encoding="utf-8"
    )
    hypes_agent = (
        hypes_package / "skills/adapt-response/agents/openai.yaml"
    ).read_text(encoding="utf-8")
    if "Revise the draft itself" not in hypes_skill:
        raise ValueError("Hypes skill does not revise the actual response")
    if "Make the language natural" not in hypes_skill:
        raise ValueError("Hypes skill does not review language quality")
    if "allow_implicit_invocation: true" not in hypes_agent:
        raise ValueError("Hypes skill does not allow implicit invocation")
    for retired in ("recommend-help", "run-hypes-task"):
        if (hypes_package / "skills" / retired).exists():
            raise ValueError(f"retired Hypes skill remains: {retired}")

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
    try:
        process.stdin.write(json.dumps(requests[0]) + "\n")
        process.stdin.flush()
        initialize, _ = _read_mcp_response(
            process, expected_id=1, package_name=package_name
        )
        process.stdin.write(json.dumps(requests[1]) + "\n")
        process.stdin.write(json.dumps(requests[2]) + "\n")
        process.stdin.flush()
        tool_response, _ = _read_mcp_response(
            process, expected_id=2, package_name=package_name
        )
        try:
            tools = tool_response["result"]["tools"]
        except (KeyError, TypeError) as exc:
            raise ValueError(
                f"{package_name} MCP tools/list response is invalid: {tool_response}"
            ) from exc
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
            raise ValueError(
                f"{package_name} MCP launcher exited {return_code}: {stderr.strip()}"
            )
    if (
        initialize["result"]["serverInfo"]["name"]
        != EXPECTED_SERVER_NAMES[package_name]
    ):
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
    if not all(
        required_annotations.issubset(tool.get("annotations", {})) for tool in tools
    ):
        raise ValueError(f"{package_name} MCP tool annotations are incomplete")


def validate_sessionless_server_source(package_name: str) -> None:
    source = (
        ROOT / "plugins" / package_name / "src" / package_name / "mcp_server.py"
    ).read_text(encoding="utf-8")
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


def validate_hypes_first_run(temporary_root: Path) -> None:
    package = ROOT / "plugins/hypes"
    environment = os.environ.copy()
    environment.pop("VIRTUAL_ENV", None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONPATH"] = str(package / "src")
    environment["HYPES_DATA_ROOT"] = str(temporary_root / "Hypes")
    status_call = """
import asyncio
import json
import os
from pathlib import Path

from hypes.mcp_server import create_server

server = create_server(Path(os.environ["HYPES_DATA_ROOT"]))
response = asyncio.run(server.call_tool("hypes_status", {}))
print(json.dumps(response.structured_content))
"""
    response = _run_json(
        [str(temporary_root / "hypes-python/bin/python"), "-c", status_call],
        environment=environment,
    )
    if response.get("ok") is not True:
        raise ValueError("Hypes MCP status call failed")
    status = response.get("result")
    if not isinstance(status, dict):
        raise TypeError("Hypes MCP status result is invalid")
    if status.get("transport_session_state") is not False:
        raise ValueError("Hypes MCP transport unexpectedly retains session state")
    if status.get("persistent_application_state") is not True:
        raise ValueError(
            "Hypes did not report its explicit persistent application state"
        )
    if status.get("http_publication_ready") is not False:
        raise ValueError(
            "Hypes local package must not report remote publication readiness"
        )

    data_root = temporary_root / "Hypes"
    database = data_root / "hypes.sqlite3"
    if not data_root.is_dir() or not database.is_file():
        raise ValueError("Hypes status did not create its isolated private store")
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
