#!/usr/bin/env python3
"""Build the single OpenAI plugin from product Skills and hosted runtime sources."""

from __future__ import annotations

import argparse
import filecmp
import json
import shutil
import tempfile
from pathlib import Path

from document_files_release import document_source

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = ROOT / "plugins"
TARGET = PLUGIN_ROOT / "personal-agent-toolkit"
PRODUCTS = tuple(
    json.loads((ROOT / "products.json").read_text(encoding="utf-8"))["distributions"][
        "openai"
    ]["products"]
)


def copy_skills(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    for product in PRODUCTS:
        product_root = (
            document_source() if product == "document-files" else PLUGIN_ROOT / product
        )
        for source in sorted((product_root / "skills").iterdir()):
            if not source.is_dir():
                continue
            if source.name in seen:
                raise ValueError(f"duplicate Skill name: {source.name}")
            seen.add(source.name)
            shutil.copytree(
                source,
                target / source.name,
                ignore=shutil.ignore_patterns("agents", "__pycache__", "*.pyc"),
            )


def copy_document_runtime(target: Path) -> None:
    """Copy the canonical Python source without provisioning during document work."""

    source = document_source()
    shutil.copytree(
        source / "openai-runtime",
        target,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    shutil.copytree(
        source / "src" / "document_files",
        target / "src" / "document_files",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    shutil.copy2(source / "pyproject.toml", target / "pyproject.toml")
    for name in ("LICENSE", "NOTICE"):
        shutil.copy2(source / name, target / name)


def same_tree(left: Path, right: Path) -> bool:
    comparison = filecmp.dircmp(left, right)
    if comparison.left_only or comparison.right_only or comparison.funny_files:
        return False
    if any(
        not filecmp.cmp(left / name, right / name, shallow=False)
        for name in comparison.common_files
    ):
        return False
    return all(same_tree(left / name, right / name) for name in comparison.common_dirs)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail when the checked-in OpenAI Skill bundle is stale",
    )
    parser.add_argument(
        "--output", type=Path, help="Build outside the installed Toolkit bundle"
    )
    args = parser.parse_args()
    target = args.output.resolve() if args.output else TARGET

    with tempfile.TemporaryDirectory() as directory:
        built = Path(directory) / "bundle"
        copy_skills(built / "skills")
        copy_document_runtime(built / "runtime" / "document-files")
        if args.check:
            if (
                (target / "skills").is_dir()
                and (target / "runtime").is_dir()
                and same_tree(built / "skills", target / "skills")
                and same_tree(built / "runtime", target / "runtime")
            ):
                print("OpenAI plugin bundle is current.")
                return 0
            print("OpenAI plugin bundle is stale; run scripts/build_openai_plugin.py")
            return 1

        shutil.rmtree(target / "skills", ignore_errors=True)
        shutil.rmtree(target / "runtime", ignore_errors=True)
        shutil.copytree(built / "skills", target / "skills")
        shutil.copytree(built / "runtime", target / "runtime")
        skill_count = len(list((target / "skills").iterdir()))
        print(f"Built {skill_count} Skills and the hosted runtime in {target}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
