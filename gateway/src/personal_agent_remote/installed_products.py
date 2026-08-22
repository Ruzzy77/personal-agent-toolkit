"""Resolve the installed local product packages used by the personal gateway."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tomllib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PRODUCTS = ("sense", "corpus", "hypes")
MCP_LAUNCHERS = {product: f"launchers/{product}-mcp" for product in PRODUCTS}
DEFAULT_MARKETPLACE = "personal-agent-toolkit"


class InstalledProductError(ValueError):
    """An installed product cannot be used as a trusted gateway target."""


@dataclass(frozen=True)
class InstalledProduct:
    product: str
    root: Path
    launcher: Path
    base_version: str
    plugin_version: str
    marketplace: str


def normalize_products(values: Iterable[str]) -> tuple[str, ...]:
    requested = set(values)
    unknown = requested.difference(PRODUCTS)
    if unknown:
        raise InstalledProductError(
            "products must contain only sense, corpus, and hypes"
        )
    if not requested:
        raise InstalledProductError("at least one installed product is required")
    return tuple(product for product in PRODUCTS if product in requested)


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InstalledProductError(f"installed product metadata is unreadable: {path}") from exc
    if not isinstance(value, dict):
        raise InstalledProductError(f"installed product metadata must be an object: {path}")
    return value


def _trusted_path(path: Path, *, kind: str, executable: bool = False) -> Path:
    expanded = path.expanduser()
    if not expanded.is_absolute():
        raise InstalledProductError(f"{kind} must be an absolute path")
    try:
        canonical = expanded.resolve(strict=True)
        metadata = expanded.lstat()
    except OSError as exc:
        raise InstalledProductError(f"{kind} is unavailable") from exc
    if canonical != expanded or stat.S_ISLNK(metadata.st_mode):
        raise InstalledProductError(f"{kind} must not use symbolic links")
    if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o022:
        raise InstalledProductError(f"{kind} must be owned and not group/other writable")
    if kind.endswith("root"):
        if not stat.S_ISDIR(metadata.st_mode):
            raise InstalledProductError(f"{kind} must be a directory")
    elif not stat.S_ISREG(metadata.st_mode):
        raise InstalledProductError(f"{kind} must be a regular file")
    if executable and not metadata.st_mode & stat.S_IXUSR:
        raise InstalledProductError(f"{kind} must be executable")
    return canonical


def installation_from_root(
    product: str,
    root: Path,
    *,
    expected_plugin_version: str | None = None,
    marketplace: str = DEFAULT_MARKETPLACE,
) -> InstalledProduct:
    normalized = normalize_products((product,))[0]
    canonical_root = _trusted_path(root, kind=f"{normalized} product root")
    manifest = _load_object(canonical_root / ".codex-plugin" / "plugin.json")
    if manifest.get("name") != normalized:
        raise InstalledProductError(f"{normalized} plugin identity is invalid")
    plugin_version = manifest.get("version")
    if not isinstance(plugin_version, str) or not plugin_version:
        raise InstalledProductError(f"{normalized} plugin version is missing")
    if expected_plugin_version is not None and plugin_version != expected_plugin_version:
        raise InstalledProductError(f"{normalized} installed version changed during discovery")

    project_path = canonical_root / "pyproject.toml"
    try:
        project = tomllib.loads(project_path.read_text(encoding="utf-8"))["project"]
    except (OSError, UnicodeError, tomllib.TOMLDecodeError, KeyError, TypeError) as exc:
        raise InstalledProductError(f"{normalized} package metadata is unreadable") from exc
    base_version = project.get("version")
    if project.get("name") != normalized or not isinstance(base_version, str):
        raise InstalledProductError(f"{normalized} package identity is invalid")
    if plugin_version.split("+", 1)[0] != base_version:
        raise InstalledProductError(f"{normalized} plugin and package versions do not match")

    launcher = _trusted_path(
        canonical_root / MCP_LAUNCHERS[normalized],
        kind=f"{normalized} MCP launcher",
        executable=True,
    )
    return InstalledProduct(
        product=normalized,
        root=canonical_root,
        launcher=launcher,
        base_version=base_version,
        plugin_version=plugin_version,
        marketplace=marketplace,
    )


def discover_codex_installations(
    *,
    products: Iterable[str] = PRODUCTS,
    marketplace: str = DEFAULT_MARKETPLACE,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, InstalledProduct]:
    selected = normalize_products(products)
    completed = runner(
        ["codex", "plugin", "list", "--json"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise InstalledProductError("Codex installed-plugin discovery failed")
    try:
        entries = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise InstalledProductError(
            "Codex installed-plugin discovery returned invalid JSON"
        ) from exc
    if isinstance(entries, dict):
        if set(entries) != {"installed", "available"} or not isinstance(
            entries.get("installed"), list
        ):
            raise InstalledProductError(
                "Codex installed-plugin discovery returned the wrong shape"
            )
        entries = entries["installed"]
    if not isinstance(entries, list):
        raise InstalledProductError(
            "Codex installed-plugin discovery returned the wrong shape"
        )

    candidates: dict[str, tuple[Path, str]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        product = entry.get("name")
        if (
            product not in selected
            or entry.get("marketplaceName") != marketplace
            or entry.get("installed") is not True
            or entry.get("enabled") is not True
        ):
            continue
        source = entry.get("source")
        version = entry.get("version")
        if (
            not isinstance(source, dict)
            or source.get("source") != "local"
            or not isinstance(source.get("path"), str)
            or not isinstance(version, str)
        ):
            raise InstalledProductError(f"{product} installed source is not a local package")
        if product in candidates:
            raise InstalledProductError(f"multiple enabled {product} installations were found")
        candidates[product] = (Path(source["path"]), version)

    missing = [product for product in selected if product not in candidates]
    if missing:
        raise InstalledProductError(
            "selected products are not installed and enabled in Codex: " + ", ".join(missing)
        )
    return {
        product: installation_from_root(
            product,
            candidates[product][0],
            expected_plugin_version=candidates[product][1],
            marketplace=marketplace,
        )
        for product in selected
    }


def parse_product_roots(values: Iterable[str]) -> dict[str, Path]:
    parsed: dict[str, Path] = {}
    for value in values:
        product, separator, raw_path = value.partition("=")
        if not separator or not raw_path:
            raise InstalledProductError("product roots must use product=/absolute/path")
        normalized = normalize_products((product,))[0]
        if normalized in parsed:
            raise InstalledProductError(f"duplicate {normalized} product root")
        parsed[normalized] = Path(raw_path)
    return parsed
