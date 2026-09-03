#!/usr/bin/env python3
"""Create a self-contained temporary source tree for a Sites deployment."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITES_ROOT = ROOT / "sites"
PACKAGES_ROOT = ROOT / "packages"
IGNORED_NAMES = {
    ".DS_Store",
    ".git",
    ".next",
    ".pytest_cache",
    ".venv",
    ".vinext",
    ".wrangler",
    "__pycache__",
    "dist",
    "node_modules",
}


def ignored(_directory: str, names: list[str]) -> set[str]:
    return {
        name
        for name in names
        if name in IGNORED_NAMES or name == ".env" or name.startswith(".env.")
    }


def within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def export_site(site: Path, destination: Path) -> None:
    site = site.resolve()
    destination = destination.resolve()
    if not within(site, SITES_ROOT) or site.parent != SITES_ROOT:
        raise ValueError("site must be one direct child of sites/")
    if not (site / ".openai" / "hosting.json").is_file():
        raise ValueError("site has no .openai/hosting.json")
    if destination.exists():
        raise FileExistsError("destination must not already exist")

    shutil.copytree(site, destination, ignore=ignored)
    package_path = destination / "package.json"
    package = json.loads(package_path.read_text())

    for group in ("dependencies", "devDependencies", "optionalDependencies"):
        dependencies = package.get(group, {})
        for name, value in list(dependencies.items()):
            if not isinstance(value, str) or not value.startswith("file:"):
                continue
            source = (site / value.removeprefix("file:")).resolve()
            if not within(source, PACKAGES_ROOT) or source.parent != PACKAGES_ROOT:
                raise ValueError(f"unsupported local dependency for {name}: {value}")
            target = destination / "vendor" / source.name
            shutil.copytree(source, target, ignore=ignored)
            dependencies[name] = f"file:./vendor/{source.name}"

    package_path.write_text(json.dumps(package, ensure_ascii=False, indent=2) + "\n")
    lock_path = destination / "package-lock.json"
    if lock_path.exists():
        lock_path.unlink()
    subprocess.run(
        [
            "npm",
            "install",
            "--package-lock-only",
            "--ignore-scripts",
            "--no-audit",
            "--no-fund",
            "--allow-remote=all",
            "--allow-file=all",
        ],
        cwd=destination,
        check=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("site", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    export_site(args.site, args.destination)
    print(args.destination.resolve())


if __name__ == "__main__":
    main()
