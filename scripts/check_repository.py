#!/usr/bin/env python3
"""Check repository-level plugin, package, and documentation contracts."""

from __future__ import annotations

import json
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = ROOT / "plugins"
REMOTE_PLUGINS = {"sense", "corpus", "hypes", "journal", "library"}
LOCAL_MCP_PLUGINS = {"document-files"}
SKILL_ONLY_PLUGINS = {"design"}
REQUIRED_PLUGINS = REMOTE_PLUGINS | LOCAL_MCP_PLUGINS | SKILL_ONLY_PLUGINS
CODEX_SUFFIX = re.compile(r"^(?P<base>\d+\.\d+\.\d+)\+codex\.\d{14}$")
MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
PACKAGE_VERSION = re.compile(r"(?:PACKAGE_VERSION|__version__)\s*=\s*[\"']([^\"']+)")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def tracked_files() -> list[Path]:
    output = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT, text=False)
    return [ROOT / item.decode() for item in output.split(b"\0") if item]


def check_marketplaces(errors: list[str]) -> None:
    claude = read_json(ROOT / ".claude-plugin" / "marketplace.json")
    codex = read_json(ROOT / ".agents" / "plugins" / "marketplace.json")
    claude_entries = {item["name"]: item for item in claude["plugins"]}
    codex_entries = {item["name"]: item for item in codex["plugins"]}

    for label, entries in (("Claude", claude_entries), ("Codex", codex_entries)):
        if set(entries) != REQUIRED_PLUGINS:
            errors.append(
                f"{label} marketplace plugins differ: {sorted(set(entries) ^ REQUIRED_PLUGINS)}"
            )

    if "local-first" in claude.get("description", "").casefold():
        errors.append(
            "Claude marketplace still describes the remote service set as local-first"
        )

    for name in sorted(REQUIRED_PLUGINS):
        expected = f"./plugins/{name}"
        if claude_entries.get(name, {}).get("source") != expected:
            errors.append(f"Claude marketplace source for {name} must be {expected}")
        codex_source = codex_entries.get(name, {}).get("source", {})
        if (
            codex_source.get("source") != "local"
            or codex_source.get("path") != expected
        ):
            errors.append(
                f"Codex marketplace source for {name} must be local {expected}"
            )

    if claude_entries["design"].get("displayName") != "Personal Design":
        errors.append(
            "Claude Design listing must remain distinct from Anthropic Design"
        )


def check_plugin(name: str, errors: list[str]) -> None:
    root = PLUGIN_ROOT / name
    claude_path = root / ".claude-plugin" / "plugin.json"
    codex_path = root / ".codex-plugin" / "plugin.json"
    for required in (
        root / "README.md",
        root / "LICENSE",
        root / "NOTICE",
        claude_path,
        codex_path,
    ):
        if not required.is_file():
            errors.append(f"{relative(required)} is required")
            return

    claude = read_json(claude_path)
    codex = read_json(codex_path)
    if claude.get("name") != name or codex.get("name") != name:
        errors.append(f"{name}: manifest name differs from its directory")

    base = claude.get("version")
    match = CODEX_SUFFIX.fullmatch(str(codex.get("version", "")))
    if match is None or match.group("base") != base:
        errors.append(f"{name}: Claude and Codex base versions differ")

    prompts = codex.get("interface", {}).get("defaultPrompt", [])
    if not isinstance(prompts, list) or not 1 <= len(prompts) <= 3:
        errors.append(f"{name}: Codex defaultPrompt must contain one to three prompts")
    elif not all(isinstance(prompt, str) and prompt.strip() for prompt in prompts):
        errors.append(
            f"{name}: Codex defaultPrompt contains an empty or non-string value"
        )

    if name == "design" and (
        claude.get("displayName") != "Personal Design"
        or codex.get("interface", {}).get("displayName") != "Personal Design"
    ):
        errors.append(
            "design: client display names must avoid the generic Design collision"
        )

    skill_path = codex.get("skills")
    if skill_path and not (root / str(skill_path)).is_dir():
        errors.append(f"{name}: Codex skills path does not exist")

    claude_mcp_path = root / ".mcp.json"
    codex_servers = codex.get("mcpServers", {})
    if name in SKILL_ONLY_PLUGINS:
        if claude_mcp_path.exists() or codex_servers:
            errors.append(f"{name}: Skill-only plugin must not declare an MCP server")
    else:
        if not claude_mcp_path.is_file():
            errors.append(f"{name}: .mcp.json is required")
        else:
            claude_servers = read_json(claude_mcp_path).get("mcpServers", {})
            if set(claude_servers) != {name} or set(codex_servers) != {name}:
                errors.append(f"{name}: MCP server names differ across clients")
            elif name in REMOTE_PLUGINS:
                claude_server = claude_servers[name]
                codex_server = codex_servers[name]
                if (
                    claude_server.get("type") != "http"
                    or codex_server.get("type") != "http"
                    or claude_server.get("url") != codex_server.get("url")
                ):
                    errors.append(f"{name}: remote MCP URLs differ across clients")
            elif name in LOCAL_MCP_PLUGINS and (
                "command" not in claude_servers[name]
                or "command" not in codex_servers[name]
            ):
                errors.append(f"{name}: local MCP commands are missing")

    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        project = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]
        if project.get("version") != base:
            errors.append(
                f"{name}: pyproject version differs from manifest base version"
            )
        lock = root / "uv.lock"
        if lock.is_file():
            packages = tomllib.loads(lock.read_text(encoding="utf-8")).get(
                "package", []
            )
            locked = [
                package.get("version")
                for package in packages
                if package.get("name") == project.get("name")
            ]
            if locked != [base]:
                errors.append(f"{name}: uv.lock project version differs from {base}")
        for init in root.glob("src/*/__init__.py"):
            version = PACKAGE_VERSION.search(init.read_text(encoding="utf-8"))
            if version and version.group(1) != base:
                errors.append(f"{relative(init)} package version differs from {base}")


def check_javascript_locks(errors: list[str]) -> None:
    package_paths = [ROOT / "auth" / "package.json"]
    package_paths.extend(ROOT.glob("services/*/package.json"))
    package_paths.extend(ROOT.glob("sites/*/package.json"))
    for package_path in sorted(package_paths):
        lock_path = package_path.with_name("package-lock.json")
        if not lock_path.is_file():
            continue
        package = read_json(package_path)
        lock = read_json(lock_path)
        root_package = lock.get("packages", {}).get("", {})
        if lock.get("name") != package.get("name") or root_package.get(
            "name"
        ) != package.get("name"):
            errors.append(
                f"{relative(lock_path)} package name differs from package.json"
            )
        if lock.get("version") != package.get("version") or root_package.get(
            "version"
        ) != package.get("version"):
            errors.append(
                f"{relative(lock_path)} root version differs from package.json"
            )


def check_shared_product_versions(errors: list[str]) -> None:
    source_files = {
        "journal": [
            ROOT / "services" / "journal" / "src" / "http.ts",
            ROOT / "services" / "journal" / "src" / "mcp.ts",
        ],
        "library": [ROOT / "services" / "library" / "src" / "worker.ts"],
    }
    for name, files in source_files.items():
        plugin_version = read_json(
            PLUGIN_ROOT / name / ".claude-plugin" / "plugin.json"
        )["version"]
        service_version = read_json(ROOT / "services" / name / "package.json")[
            "version"
        ]
        site_version = read_json(ROOT / "sites" / name / "package.json")["version"]
        if len({plugin_version, service_version, site_version}) != 1:
            errors.append(
                f"{name}: plugin, service, and Site versions must describe one product release"
            )
        for path in files:
            if f'version: "{service_version}"' not in path.read_text(encoding="utf-8"):
                errors.append(
                    f"{relative(path)} does not expose service version {service_version}"
                )


def check_markdown_links(files: list[Path], errors: list[str]) -> None:
    for path in files:
        if path.suffix.casefold() != ".md":
            continue
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
        ):
            for raw_target in MARKDOWN_LINK.findall(line):
                target = raw_target.strip().split(maxsplit=1)[0].strip("<>")
                if not target or target.startswith(("#", "/")):
                    continue
                if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.IGNORECASE):
                    continue
                file_target = target.split("#", 1)[0]
                if file_target and not (path.parent / file_target).resolve().exists():
                    errors.append(
                        f"{relative(path)}:{line_number}: broken relative link {target}"
                    )


def check_tracked_residue(files: list[Path], errors: list[str]) -> None:
    for path in files:
        parts = path.relative_to(ROOT).parts
        if (
            path.name == ".DS_Store"
            or path.suffix == ".pyc"
            or "__pycache__" in parts
            or any(part.endswith(".egg-info") for part in parts)
            or path.name == "wrangler.jsonc"
            or path.name.startswith(".env")
        ):
            errors.append(f"{relative(path)} is generated or private runtime residue")
    if (ROOT / "gateway").exists():
        errors.append(
            "gateway/ is retired and must not return to the active repository"
        )


def main() -> int:
    errors: list[str] = []
    files = tracked_files()
    check_marketplaces(errors)
    for name in sorted(REQUIRED_PLUGINS):
        check_plugin(name, errors)
    check_javascript_locks(errors)
    check_shared_product_versions(errors)
    check_markdown_links(files, errors)
    check_tracked_residue(files, errors)

    if errors:
        print("Repository consistency check failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"Repository consistency check passed for {len(REQUIRED_PLUGINS)} plugins.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
