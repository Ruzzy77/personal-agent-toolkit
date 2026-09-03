#!/usr/bin/env python3
"""Build the single OpenAI plugin from the five remote product Skills."""

from __future__ import annotations

import argparse
import filecmp
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = ROOT / "plugins"
TARGET = PLUGIN_ROOT / "personal-agent-toolkit" / "skills"
PRODUCTS = ("sense", "corpus", "hypes", "journal", "library")


def copy_skills(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    for product in PRODUCTS:
        for source in sorted((PLUGIN_ROOT / product / "skills").iterdir()):
            if not source.is_dir():
                continue
            if source.name in seen:
                raise ValueError(f"duplicate Skill name: {source.name}")
            seen.add(source.name)
            shutil.copytree(
                source,
                target / source.name,
                ignore=shutil.ignore_patterns("agents"),
            )


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
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as directory:
        built = Path(directory) / "skills"
        copy_skills(built)
        if args.check:
            if TARGET.is_dir() and same_tree(built, TARGET):
                print("OpenAI Skill bundle is current.")
                return 0
            print("OpenAI Skill bundle is stale; run scripts/build_openai_plugin.py")
            return 1

        shutil.rmtree(TARGET, ignore_errors=True)
        shutil.copytree(built, TARGET)
        print(f"Built {len(list(TARGET.iterdir()))} Skills in {TARGET.relative_to(ROOT)}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
