#!/usr/bin/env python3
"""Check repository-level plugin, package, and documentation contracts."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Any

from document_files_release import document_source, release_lock

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = ROOT / "plugins"
PRODUCT_REGISTRY = json.loads((ROOT / "products.json").read_text(encoding="utf-8"))
PRODUCTS: dict[str, dict[str, Any]] = PRODUCT_REGISTRY["products"]
OPENAI_DISTRIBUTION: dict[str, Any] = PRODUCT_REGISTRY["distributions"]["openai"]
OPENAI_PRODUCTS = set(OPENAI_DISTRIBUTION["products"])
OPENAI_REMOTE_MCP_PRODUCTS = {
    name
    for name, product in PRODUCTS.items()
    if product["delivery"]["openai"]["runtime"] == "remote_mcp"
}
CLAUDE_REMOTE_MCP_PLUGINS = {
    name
    for name, product in PRODUCTS.items()
    if product["delivery"]["claude"]["mode"] == "remote_mcp"
}
CLAUDE_LOCAL_MCP_PLUGINS = {
    name
    for name, product in PRODUCTS.items()
    if product["delivery"]["claude"]["mode"] == "local_mcp"
}
REQUIRED_PLUGINS = set(PRODUCTS)
CODEX_PLUGINS = {"personal-agent-toolkit"}
CODEX_SUFFIX = re.compile(r"^(?P<base>\d+\.\d+\.\d+)\+codex\.\d{14}$")
MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
PACKAGE_VERSION = re.compile(r"(?:PACKAGE_VERSION|__version__)\s*=\s*[\"']([^\"']+)")
REGISTERED_TS_TOOL = re.compile(r'server\.registerTool\(\s*"([a-z][a-z0-9_]*)"')
REGISTERED_PYTHON_TOOL = re.compile(r'@server\.tool\(\s*name="([a-z][a-z0-9_]*)"')


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def relative(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def product_root(name: str) -> Path:
    return (
        document_source()
        if name == "document-files"
        else ROOT / PRODUCTS[name]["plugin"]["path"]
    )


def registered_path(path: str) -> Path:
    prefix = "plugins/document-files"
    if path == prefix or path.startswith(prefix + "/"):
        return document_source() / path.removeprefix(prefix).lstrip("/")
    return ROOT / path


def independent_pinned(name: str) -> bool:
    return name == "document-files" and release_lock()["state"] == "pinned"


def tracked_files() -> list[Path]:
    output = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT, text=False)
    files = [ROOT / item.decode() for item in output.split(b"\0") if item]
    return [path for path in files if path.is_file()]


def check_product_registry(errors: list[str]) -> None:
    if PRODUCT_REGISTRY.get("schema_version") != 2:
        errors.append("products.json schema_version must be 2")

    valid_openai_runtimes = {"remote_mcp", "host"}
    valid_claude_modes = {"remote_mcp", "local_mcp"}
    valid_sync_modes = {
        "none",
        "migration_client",
        "source_work_bridge",
        "embedded_engine",
    }
    for name, product in PRODUCTS.items():
        plugin = product.get("plugin", {})
        plugin_path = ROOT / str(plugin.get("path", ""))
        if plugin_path != PLUGIN_ROOT / name:
            errors.append(f"{name}: products.json plugin path must be plugins/{name}")
        if not re.fullmatch(r"\d+\.\d+\.\d+", str(plugin.get("base_version", ""))):
            errors.append(f"{name}: products.json has an invalid base version")

        delivery = product.get("delivery", {})
        openai = delivery.get("openai", {})
        claude = delivery.get("claude", {})
        sync = delivery.get("sync", {})
        if (
            openai.get("mode") != "bundled_skills"
            or openai.get("runtime") not in valid_openai_runtimes
        ):
            errors.append(f"{name}: products.json has an invalid OpenAI delivery")
        if claude.get("mode") not in valid_claude_modes:
            errors.append(f"{name}: products.json has an invalid Claude delivery")
        if sync.get("mode") not in valid_sync_modes:
            errors.append(f"{name}: products.json has an invalid Sync delivery")

        for component in product.get("components", []):
            if not registered_path(component).exists():
                errors.append(f"{name}: missing registered component {component}")

        mcp = product.get("mcp")
        if not isinstance(mcp, dict):
            errors.append(f"{name}: MCP product is missing its public contract")
            continue
        if mcp.get("server_key") != name:
            errors.append(f"{name}: MCP server key must match the product name")
        if not registered_path(str(mcp.get("implementation", ""))).is_file():
            errors.append(f"{name}: registered MCP implementation is missing")
        tools = mcp.get("tools", [])
        if not tools or len(tools) != len(set(tools)):
            errors.append(f"{name}: MCP tools must be a non-empty unique list")


def check_marketplaces(errors: list[str]) -> None:
    claude = read_json(ROOT / ".claude-plugin" / "marketplace.json")
    codex = read_json(ROOT / ".agents" / "plugins" / "marketplace.json")
    claude_entries = {item["name"]: item for item in claude["plugins"]}
    codex_entries = {item["name"]: item for item in codex["plugins"]}

    if set(claude_entries) != REQUIRED_PLUGINS:
        errors.append(
            "Claude marketplace plugins differ: "
            f"{sorted(set(claude_entries) ^ REQUIRED_PLUGINS)}"
        )
    if set(codex_entries) != CODEX_PLUGINS:
        errors.append(
            "Codex marketplace plugins differ: "
            f"{sorted(set(codex_entries) ^ CODEX_PLUGINS)}"
        )

    if "local-first" in claude.get("description", "").casefold():
        errors.append(
            "Claude marketplace still describes the remote service set as local-first"
        )

    for name in sorted(REQUIRED_PLUGINS):
        expected = (
            {
                "source": "url",
                "url": "https://github.com/Ruzzy77/document-files.git",
                "ref": release_lock()["sourceCommit"],
            }
            if independent_pinned(name)
            else f"./plugins/{name}"
        )
        if claude_entries.get(name, {}).get("source") != expected:
            errors.append(f"Claude marketplace source for {name} must be {expected}")

    unified_path = OPENAI_DISTRIBUTION["plugin"]["path"]
    unified_source = codex_entries.get("personal-agent-toolkit", {}).get("source", {})
    if (
        unified_source.get("source") != "local"
        or unified_source.get("path") != f"./{unified_path}"
    ):
        errors.append(
            "Codex Personal Agent Toolkit source must match the OpenAI distribution"
        )

    if claude_entries["design"].get("displayName") != "Personal Design":
        errors.append(
            "Claude Design listing must remain distinct from Anthropic Design"
        )


def check_plugin(name: str, errors: list[str]) -> None:
    product = PRODUCTS[name]
    plugin = product["plugin"]
    root = product_root(name)
    claude_path = root / ".claude-plugin" / "plugin.json"
    required_files = [
        root / "README.md",
        root / "DESIGN.md",
        root / "LICENSE",
        root / "NOTICE",
        claude_path,
    ]
    for required in required_files:
        if not required.is_file():
            errors.append(f"{relative(required)} is required")
            return

    claude = read_json(claude_path)
    codex_path = root / ".codex-plugin" / "plugin.json"
    codex = read_json(codex_path) if codex_path.is_file() else None
    if claude.get("name") != name:
        errors.append(f"{name}: manifest name differs from its directory")
    if codex is not None and not independent_pinned(name):
        errors.append(
            f"{name}: product-specific Codex manifest must be replaced by the OpenAI bundle"
        )

    base = claude.get("version")
    if base != plugin["base_version"]:
        errors.append(f"{name}: manifest version differs from products.json")
    if codex is not None and independent_pinned(name):
        if codex.get("version") != base or codex.get("name") != name:
            errors.append(
                f"{name}: independent Codex manifest differs from pinned release"
            )
    elif codex is not None:
        match = CODEX_SUFFIX.fullmatch(str(codex.get("version", "")))
        if match is None or match.group("base") != base:
            errors.append(f"{name}: Claude and Codex base versions differ")

        prompts = codex.get("interface", {}).get("defaultPrompt", [])
        if not isinstance(prompts, list) or not 1 <= len(prompts) <= 3:
            errors.append(
                f"{name}: Codex defaultPrompt must contain one to three prompts"
            )
        elif not all(isinstance(prompt, str) and prompt.strip() for prompt in prompts):
            errors.append(
                f"{name}: Codex defaultPrompt contains an empty or non-string value"
            )

    if name == "design" and claude.get("displayName") != "Personal Design":
        errors.append(
            "design: Claude display name must avoid the generic Design collision"
        )

    skill_path = "./skills/"
    if skill_path and not (root / str(skill_path)).is_dir():
        errors.append(f"{name}: Codex skills path does not exist")

    claude_mcp_path = root / ".mcp.json"
    codex_app_path = root / ".app.json"
    if name in CLAUDE_REMOTE_MCP_PLUGINS:
        if not claude_mcp_path.is_file():
            errors.append(f"{name}: .mcp.json is required")
        else:
            claude_servers = read_json(claude_mcp_path).get("mcpServers", {})
            server_key = product["mcp"]["server_key"]
            if set(claude_servers) != {server_key}:
                errors.append(f"{name}: Claude MCP server name differs")
            else:
                expected_url = product["mcp"]["url"]
                claude_server = claude_servers[server_key]
                if (
                    claude_server.get("type") != "http"
                    or claude_server.get("url") != expected_url
                ):
                    errors.append(f"{name}: remote MCP URL differs from products.json")

        if codex_app_path.exists():
            errors.append(
                f"{name}: OpenAI packaging belongs only in plugins/personal-agent-toolkit"
            )
    elif name in CLAUDE_LOCAL_MCP_PLUGINS:
        if codex_app_path.exists():
            errors.append(f"{name}: local MCP plugin must not declare a remote app")
        if not claude_mcp_path.is_file():
            errors.append(f"{name}: .mcp.json is required")
        else:
            claude_servers = read_json(claude_mcp_path).get("mcpServers", {})
            server_key = product["mcp"]["server_key"]
            if set(claude_servers) != {server_key}:
                errors.append(f"{name}: Claude MCP server name differs")
            elif "command" not in claude_servers[server_key]:
                errors.append(f"{name}: local MCP command is missing")

    pyproject = root / "pyproject.toml"
    if name in CLAUDE_REMOTE_MCP_PLUGINS:
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


def check_openai_distribution(errors: list[str]) -> None:
    distribution = OPENAI_DISTRIBUTION
    document_skills = {
        path.parent.name
        for path in (product_root("document-files") / "skills").glob("*/SKILL.md")
    }
    if document_skills != {"document-files"}:
        errors.append("Document Files must expose only the document-files Skill")
    personal = distribution.get("client_installation", {}).get("chatgpt_personal", {})
    if personal.get("skills") != ["document-files"] or personal.get("skill_ids") != {
        "document-files": "document-files"
    }:
        errors.append("ChatGPT personal Skills must contain only document-files")
    bundled_products = distribution.get("products", [])
    if set(bundled_products) != REQUIRED_PLUGINS or len(bundled_products) != len(
        REQUIRED_PLUGINS
    ):
        errors.append(
            "OpenAI distribution products must contain each bundled product once"
        )

    root = ROOT / distribution["plugin"]["path"]
    manifest_path = root / ".codex-plugin" / "plugin.json"
    app_path = root / ".app.json"
    for required in (
        manifest_path,
        app_path,
        root / "README.md",
        root / "DESIGN.md",
        root / "LICENSE",
        root / "NOTICE",
        root / "assets" / "icon.png",
        root / "skills",
        root / "runtime" / "document-files" / "document-files",
    ):
        if not required.exists():
            errors.append(f"{relative(required)} is required")

    if not manifest_path.is_file() or not app_path.is_file():
        return
    manifest = read_json(manifest_path)
    if manifest.get("name") != "personal-agent-toolkit":
        errors.append(
            "OpenAI distribution manifest name must be personal-agent-toolkit"
        )
    match = CODEX_SUFFIX.fullmatch(str(manifest.get("version", "")))
    if match is None or match.group("base") != distribution["plugin"]["base_version"]:
        errors.append("OpenAI distribution manifest version differs from products.json")
    if manifest.get("interface", {}).get("displayName") != distribution.get(
        "display_name"
    ):
        errors.append("OpenAI distribution display name differs from products.json")
    if manifest.get("apps") != "./.app.json":
        errors.append("OpenAI distribution must reference its registered app")

    app_entries = read_json(app_path).get("apps", {})
    registered_app = distribution["registered_app"]
    app_id = registered_app["id"]
    expected_app = {
        "id": app_id,
        "required": registered_app.get("required", False),
    }
    if app_entries != {f"dev-{app_id.removeprefix('asdk_app_')}": expected_app}:
        errors.append("OpenAI distribution app mapping differs from products.json")

    expected_skills = {
        path.parent.name
        for product in bundled_products
        for path in (product_root(product) / "skills").glob("*/SKILL.md")
    }
    actual_skills = {path.parent.name for path in (root / "skills").glob("*/SKILL.md")}
    if actual_skills != expected_skills:
        errors.append("OpenAI distribution Skills differ from product Skills")

    result = subprocess.run(
        ["python3", "scripts/build_openai_plugin.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        errors.append(result.stdout.strip() or "OpenAI Skill bundle is stale")


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
        implementation = registered_path(mcp["implementation"])
        if implementation == ROOT / "services/remote-context/src/mcp.ts":
            continue
        source = implementation.read_text(encoding="utf-8")
        identity = re.compile(
            rf'name:\s*"{re.escape(mcp["surface_name"])}"\s*,\s*'
            rf'version:\s*"{re.escape(mcp["surface_version"])}"'
        )
        package_identity = re.search(
            rf'name:\s*"{re.escape(mcp["surface_name"])}"\s*,\s*'
            r'version:\s*packageInfo\.version', source
        )
        package_path = implementation.parent.parent / "package.json"
        linked_version_matches = (
            package_identity is not None
            and 'import packageInfo from "../package.json"' in source
            and package_path.is_file()
            and read_json(package_path).get("version") == mcp["surface_version"]
        )
        if identity.search(source) is None and not linked_version_matches:
            errors.append(
                f"{name}: MCP implementation identity differs from products.json"
            )

    context_package = ROOT / "services/remote-context/package.json"
    context_site_package = ROOT / "sites/context/package.json"
    if context_site_package.is_file() and read_json(context_package).get(
        "version"
    ) != read_json(context_site_package).get("version"):
        errors.append("Context service and Site package versions must match")


def check_public_mcp_contracts(errors: list[str]) -> None:
    surfaces_path = ROOT / "services" / "remote-context" / "src" / "surfaces.ts"
    surfaces = surfaces_path.read_text(encoding="utf-8")
    context_products = {
        name: product for name, product in PRODUCTS.items()
        if product.get("mcp", {}).get("implementation") == "services/remote-context/src/mcp.ts"
    }
    # Surfaces now read the deployment registry directly. The service contract
    # test also enumerates actual individual and combined MCP registrations.
    if 'import registry from "../../../products.json"' not in surfaces or "productSurface" not in surfaces:
        errors.append(f"{relative(surfaces_path)} must derive surface identities from products.json")
    implementation = ROOT / "services/remote-context/src/mcp.ts"
    source = implementation.read_text(encoding="utf-8")
    api_source = (implementation.parent / "context-api.ts").read_text(encoding="utf-8")
    actual_tools = re.findall(r"^\s+([a-z][a-z0-9_]*): operation\(", api_source, re.MULTILINE)
    actual_tools += [f"{name}_capabilities" for name in context_products]
    expected_tools = [tool for product in context_products.values() for tool in product["mcp"]["tools"]]
    if len(actual_tools) != len(set(actual_tools)) or set(actual_tools) != set(expected_tools):
        errors.append("Context operation definitions differ from products.json")
    for name in context_products:
        if f'name.startsWith("{name}_")' not in source or re.search(r"server\.registerTool\(\s*name\s*,", source) is None:
            errors.append(f"Context MCP must register the shared {name} operation definitions")

    for name, product in PRODUCTS.items():
        mcp = product.get("mcp")
        if not mcp or name in context_products:
            continue
        implementation = registered_path(mcp["implementation"])
        source = implementation.read_text(encoding="utf-8")
        pattern = (
            REGISTERED_PYTHON_TOOL
            if implementation.suffix == ".py"
            else REGISTERED_TS_TOOL
        )
        actual_tools = pattern.findall(source)
        if name in {"library", "design"}:
            definitions = (implementation.parent / "management.ts").read_text(encoding="utf-8")
            operation_source = (implementation.parent / "operations.ts").read_text(encoding="utf-8")
            if f"{name}Operations(" not in source or re.search(r"server\.registerTool\(\s*name\s*,", source) is None:
                errors.append(f"{name} MCP must register the shared operation definitions")
            actual_tools = re.findall(rf"\b({name}_[a-z_]+):\s*op\(", definitions) + [f"{name}_capabilities"] + re.findall(rf"\b({name}_[a-z_]+):\s*op\(", operation_source)
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


def check_document_release(errors: list[str]) -> None:
    lock = release_lock()
    product = PRODUCTS["document-files"]
    independent = product.get("independent_product", {})
    if independent.get("state") != lock["state"]:
        errors.append("Document Files product state differs from release lock")
    expected = lock["version"] if lock["state"] == "pinned" else lock["baselineVersion"]
    if product["plugin"]["base_version"] != expected:
        errors.append("Document Files product version differs from release lock")
    sync = tomllib.loads((ROOT / "apps/sync/pyproject.toml").read_text())
    if f"document-files=={expected}" not in sync["project"]["dependencies"]:
        errors.append("Sync must depend on the exact Document Files consumer version")
    packages = tomllib.loads((ROOT / "apps/sync/uv.lock").read_text()).get(
        "package", []
    )
    entries = [item for item in packages if item.get("name") == "document-files"]
    if len(entries) != 1 or entries[0].get("version") != expected:
        errors.append("Sync lock must match the Document Files consumer version")
        return
    if lock["state"] == "pinned":
        wheel = lock["artifacts"]["wheel"]
        configured = (
            sync.get("tool", {}).get("uv", {}).get("sources", {}).get("document-files")
        )
        if configured != {"url": wheel["url"]}:
            errors.append(
                "Pinned Sync source must reference the exact independent wheel URL"
            )
        entry = entries[0]
        if entry.get("source") != {"url": wheel["url"]}:
            errors.append(
                "Pinned Sync lock cannot retain a local Document Files source"
            )
        wheels = entry.get("wheels", [])
        if not any(
            item.get("url") == wheel["url"]
            and item.get("hash") == "sha256:" + wheel["sha256"]
            for item in wheels
        ):
            errors.append("Pinned Sync lock wheel checksum differs from release lock")


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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--document-files-only", action="store_true")
    args = parser.parse_args()
    errors: list[str] = []
    try:
        check_document_release(errors)
    except (ValueError, OSError, KeyError) as exc:
        print(f"Document Files release check failed: {exc}")
        return 1
    if args.document_files_only:
        for error in errors:
            print(error)
        return 1 if errors else 0
    files = tracked_files()
    check_product_registry(errors)
    check_marketplaces(errors)
    for name in sorted(REQUIRED_PLUGINS):
        check_plugin(name, errors)
    check_openai_distribution(errors)
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
    print(
        "Repository consistency check passed for "
        f"{len(REQUIRED_PLUGINS)} products and the OpenAI distribution."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
