#!/usr/bin/env python3
"""Check repository-level plugin, package, and documentation contracts."""

from __future__ import annotations

import ast
import json
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = ROOT / "plugins"
PRODUCT_REGISTRY = json.loads((ROOT / "products.json").read_text(encoding="utf-8"))
PRODUCTS: dict[str, dict[str, Any]] = PRODUCT_REGISTRY["products"]
REMOTE_PLUGINS = {
    name
    for name, product in PRODUCTS.items()
    if product["plugin"]["kind"] == "remote_mcp"
}
LOCAL_MCP_PLUGINS = {
    name
    for name, product in PRODUCTS.items()
    if product["plugin"]["kind"] == "local_mcp"
}
SKILL_ONLY_PLUGINS = {
    name
    for name, product in PRODUCTS.items()
    if product["plugin"]["kind"] == "skill_only"
}
REQUIRED_PLUGINS = set(PRODUCTS)
CODEX_SUFFIX = re.compile(r"^(?P<base>\d+\.\d+\.\d+)\+codex\.\d{14}$")
MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
PACKAGE_VERSION = re.compile(r"(?:PACKAGE_VERSION|__version__)\s*=\s*[\"']([^\"']+)")
REGISTERED_TS_TOOL = re.compile(r'server\.registerTool\(\s*"([a-z][a-z0-9_]*)"')
REGISTERED_PYTHON_TOOL = re.compile(r'@server\.tool\(\s*name="([a-z][a-z0-9_]*)"')


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def tracked_files() -> list[Path]:
    output = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT, text=False)
    return [ROOT / item.decode() for item in output.split(b"\0") if item]


def check_product_registry(errors: list[str]) -> None:
    if PRODUCT_REGISTRY.get("schema_version") != 1:
        errors.append("products.json schema_version must be 1")

    valid_kinds = {"remote_mcp", "local_mcp", "skill_only"}
    for name, product in PRODUCTS.items():
        plugin = product.get("plugin", {})
        plugin_path = ROOT / str(plugin.get("path", ""))
        if plugin_path != PLUGIN_ROOT / name:
            errors.append(f"{name}: products.json plugin path must be plugins/{name}")
        if plugin.get("kind") not in valid_kinds:
            errors.append(f"{name}: products.json has an invalid plugin kind")
        if not re.fullmatch(r"\d+\.\d+\.\d+", str(plugin.get("base_version", ""))):
            errors.append(f"{name}: products.json has an invalid base version")

        for component in product.get("components", []):
            if not (ROOT / component).exists():
                errors.append(f"{name}: missing registered component {component}")

        mcp = product.get("mcp")
        if plugin.get("kind") == "skill_only":
            if mcp is not None:
                errors.append(f"{name}: Skill-only product must not register MCP")
            continue
        if not isinstance(mcp, dict):
            errors.append(f"{name}: MCP product is missing its public contract")
            continue
        if mcp.get("server_key") != name:
            errors.append(f"{name}: MCP server key must match the product name")
        if not (ROOT / str(mcp.get("implementation", ""))).is_file():
            errors.append(f"{name}: registered MCP implementation is missing")
        tools = mcp.get("tools", [])
        if not tools or len(tools) != len(set(tools)):
            errors.append(f"{name}: MCP tools must be a non-empty unique list")


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
    product = PRODUCTS[name]
    plugin = product["plugin"]
    root = ROOT / plugin["path"]
    claude_path = root / ".claude-plugin" / "plugin.json"
    codex_path = root / ".codex-plugin" / "plugin.json"
    for required in (
        root / "README.md",
        root / "DESIGN.md",
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
    if base != plugin["base_version"]:
        errors.append(f"{name}: manifest version differs from products.json")
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
            server_key = product["mcp"]["server_key"]
            if set(claude_servers) != {server_key} or set(codex_servers) != {
                server_key
            }:
                errors.append(f"{name}: MCP server names differ across clients")
            elif name in REMOTE_PLUGINS:
                expected_url = product["mcp"]["url"]
                claude_server = claude_servers[server_key]
                codex_server = codex_servers[server_key]
                if (
                    claude_server.get("type") != "http"
                    or codex_server.get("type") != "http"
                    or claude_server.get("url") != expected_url
                    or codex_server.get("url") != expected_url
                ):
                    errors.append(f"{name}: remote MCP URL differs from products.json")
            elif name in LOCAL_MCP_PLUGINS and (
                "command" not in claude_servers[server_key]
                or "command" not in codex_servers[server_key]
            ):
                errors.append(f"{name}: local MCP commands are missing")

    pyproject = root / "pyproject.toml"
    if name in REMOTE_PLUGINS:
        for local_runtime in (pyproject, root / "src", root / "uv.lock"):
            if local_runtime.exists():
                errors.append(
                    f"{name}: remote plugin must keep local runtime outside its bundle"
                )
                break
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


def check_dependency_documentation(errors: list[str]) -> None:
    documents = [ROOT / "PRIVACY.md", ROOT / "THIRD_PARTY_NOTICES.md"]
    contents = {path: path.read_text(encoding="utf-8") for path in documents}
    package_paths = [ROOT / "auth" / "package.json"]
    package_paths.extend(ROOT.glob("services/*/package.json"))
    package_paths.extend(ROOT.glob("sites/*/package.json"))
    for package_path in sorted(package_paths):
        lock_path = package_path.with_name("package-lock.json")
        if not lock_path.is_file():
            continue
        lock_reference = relative(lock_path)
        for document, content in contents.items():
            if lock_reference not in content:
                errors.append(
                    f"{relative(document)} must list dependency lock {lock_reference}"
                )


def check_product_versions(errors: list[str]) -> None:
    for name, product in PRODUCTS.items():
        expected = product["plugin"]["base_version"]
        for package_file in product.get("versioned_packages", []):
            path = ROOT / package_file
            if read_json(path).get("version") != expected:
                errors.append(
                    f"{name}: {package_file} version must match product version {expected}"
                )

        mcp = product.get("mcp")
        if not mcp or "surface_version" not in mcp:
            continue
        implementation = ROOT / mcp["implementation"]
        if implementation == ROOT / "services/remote-context/src/mcp.ts":
            continue
        source = implementation.read_text(encoding="utf-8")
        identity = re.compile(
            rf'name:\s*"{re.escape(mcp["surface_name"])}"\s*,\s*'
            rf'version:\s*"{re.escape(mcp["surface_version"])}"'
        )
        if identity.search(source) is None:
            errors.append(
                f"{name}: MCP implementation identity differs from products.json"
            )


def check_public_mcp_contracts(errors: list[str]) -> None:
    surfaces_path = ROOT / "services" / "remote-context" / "src" / "surfaces.ts"
    surfaces = surfaces_path.read_text(encoding="utf-8")
    remote_surfaces: dict[str, dict[str, Any]] = {}
    context_products = {
        name: product
        for name, product in PRODUCTS.items()
        if product.get("mcp", {}).get("implementation")
        == "services/remote-context/src/mcp.ts"
    }
    for name, product in context_products.items():
        mcp = product["mcp"]
        block_match = re.search(
            rf"(?s)\b{name}:\s*\{{(?P<body>.*?)\n\s*\}},",
            surfaces,
        )
        if block_match is None:
            errors.append(f"{relative(surfaces_path)} is missing {name} surface")
            continue
        body = block_match.group("body")
        display_name = re.search(r'\bname:\s*"([^\"]+)"', body)
        version = re.search(r'\bversion:\s*"([^\"]+)"', body)
        tools = re.search(r"(?s)\btools:\s*\[(.*?)\]", body)
        if display_name is None or tools is None:
            errors.append(
                f"{relative(surfaces_path)} {name} surface is missing its name or tools"
            )
            continue
        remote_surfaces[name] = {
            "name": display_name.group(1),
            "version": version.group(1) if version is not None else "",
            "tools": re.findall(r'"([^\"]+)"', tools.group(1)),
        }

        expected_surface = {
            "name": mcp["surface_name"],
            "version": mcp["surface_version"],
            "tools": mcp["tools"],
        }
        if remote_surfaces[name] != expected_surface:
            errors.append(
                f"{relative(surfaces_path)} {name} surface differs from products.json"
            )

    context_implementation = ROOT / "services/remote-context/src/mcp.ts"
    actual_context_tools = REGISTERED_TS_TOOL.findall(
        context_implementation.read_text(encoding="utf-8")
    )
    expected_context_tools = [
        tool
        for product in context_products.values()
        for tool in product["mcp"]["tools"]
    ]
    if len(actual_context_tools) != len(set(actual_context_tools)) or set(
        actual_context_tools
    ) != set(expected_context_tools):
        errors.append(
            f"{relative(context_implementation)} tool registrations differ from products.json"
        )

    sync_path = ROOT / "apps" / "sync" / "src" / "personal_agent_sync" / "migration.py"
    sync_tree = ast.parse(sync_path.read_text(encoding="utf-8"))
    sync_surfaces: object | None = None
    for node in sync_tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(
            isinstance(target, ast.Name) and target.id == "EXPECTED_REMOTE_MCP_SURFACES"
            for target in node.targets
        ):
            try:
                sync_surfaces = ast.literal_eval(node.value)
            except (SyntaxError, ValueError):
                errors.append(
                    f"{relative(sync_path)} expected MCP surfaces must remain literal data"
                )
            break
    expected_sync_surfaces = {
        name: {
            "name": product["mcp"]["surface_name"],
            "version": product["mcp"]["surface_version"],
            "tools": product["mcp"]["tools"],
        }
        for name, product in context_products.items()
    }
    if sync_surfaces != expected_sync_surfaces:
        errors.append(
            f"{relative(sync_path)} expected MCP surfaces must match products.json"
        )

    for name, product in PRODUCTS.items():
        mcp = product.get("mcp")
        if not mcp or name in context_products:
            continue
        implementation = ROOT / mcp["implementation"]
        source = implementation.read_text(encoding="utf-8")
        pattern = (
            REGISTERED_PYTHON_TOOL
            if implementation.suffix == ".py"
            else REGISTERED_TS_TOOL
        )
        actual_tools = pattern.findall(source)
        if actual_tools != mcp["tools"]:
            errors.append(
                f"{relative(implementation)} tool registrations differ from products.json"
            )


def check_sync_version(errors: list[str]) -> None:
    root = ROOT / "apps" / "sync"
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]
    version = project["version"]
    init_path = root / "src" / "personal_agent_sync" / "__init__.py"
    package_version = PACKAGE_VERSION.search(init_path.read_text(encoding="utf-8"))
    if package_version is None or package_version.group(1) != version:
        errors.append(f"{relative(init_path)} package version differs from {version}")
    packages = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8")).get(
        "package", []
    )
    locked = [
        package.get("version")
        for package in packages
        if package.get("name") == project["name"]
    ]
    if locked != [version]:
        errors.append(f"apps/sync/uv.lock project version differs from {version}")


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
    check_product_registry(errors)
    check_marketplaces(errors)
    for name in sorted(REQUIRED_PLUGINS):
        check_plugin(name, errors)
    check_javascript_locks(errors)
    check_dependency_documentation(errors)
    check_product_versions(errors)
    check_public_mcp_contracts(errors)
    check_sync_version(errors)
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
