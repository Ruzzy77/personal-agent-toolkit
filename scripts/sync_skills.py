#!/usr/bin/env python3
"""Materialize each product plugin's Skills from the shared `skills/` source.

`skills/<name>/` is the only place a Skill is edited. Every delivery path -- the
product plugins, the OpenAI bundle, the client copies -- is generated from it.
`document-files` keeps its Skill inside its pinned package and is not generated
here.

Usage: `python3 scripts/sync_skills.py` writes, `--check` only reports drift.
"""

from __future__ import annotations

import filecmp
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "skills"
REGISTRY = json.loads((ROOT / "products.json").read_text(encoding="utf-8"))
GENERATED = [
    name
    for name, product in REGISTRY["products"].items()
    if product.get("skills") and name != "document-files"
]


def assignments() -> dict[str, list[str]]:
    return {name: REGISTRY["products"][name]["skills"] for name in GENERATED}


def differs(left: Path, right: Path) -> bool:
    if not right.is_dir():
        return True
    comparison = filecmp.dircmp(left, right)
    if comparison.left_only or comparison.right_only or comparison.diff_files:
        return True
    return any(
        differs(left / shared, right / shared) for shared in comparison.common_dirs
    )


def main(check_only: bool) -> int:
    known = {directory.name for directory in SOURCE.iterdir() if directory.is_dir()}
    assigned: set[str] = set()
    drift: list[str] = []
    for product, names in assignments().items():
        target_root = ROOT / "plugins" / product / "skills"
        for name in names:
            if name not in known:
                drift.append(f"{product}: {name} is not in skills/")
                continue
            if name in assigned:
                drift.append(f"{name} is assigned to more than one product")
            assigned.add(name)
            source = SOURCE / name
            target = target_root / name
            if not differs(source, target):
                continue
            drift.append(f"{product}/{name} differs from skills/{name}")
            if not check_only:
                shutil.rmtree(target, ignore_errors=True)
                shutil.copytree(source, target)
        if not check_only and target_root.is_dir():
            for existing in target_root.iterdir():
                if existing.is_dir() and existing.name not in names:
                    shutil.rmtree(existing)
                    drift.append(f"{product}/{existing.name} removed")
    for name in sorted(known - assigned):
        drift.append(f"skills/{name} is not assigned to a product")
    if check_only:
        for line in drift:
            print(line)
        print("skills are in sync" if not drift else f"{len(drift)} difference(s)")
        return 1 if drift else 0
    print(f"generated {len(assigned)} Skills into {len(GENERATED)} product plugins")
    for line in drift:
        print(" ", line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main("--check" in sys.argv))
