#!/usr/bin/env python3
"""Build the Skill registry the unified MCP serves.

Reads the shared `skills/` source and writes one JSON file the Worker imports,
so the served text and the repository source cannot drift. Run with --check to
verify the generated file matches the source.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "skills"
TARGET = ROOT / "services" / "remote-context" / "src" / "skill-registry.json"
REGISTRY = json.loads((ROOT / "products.json").read_text(encoding="utf-8"))
TEXT_SUFFIXES = {".md", ".txt", ".yaml", ".yml", ".json", ".py", ".sh", ".toml"}
MIME = {
    ".md": "text/markdown", ".txt": "text/plain", ".yaml": "application/yaml",
    ".yml": "application/yaml", ".json": "application/json",
    ".py": "text/x-python", ".sh": "application/x-sh", ".toml": "application/toml",
}


def owner(name: str) -> str | None:
    for product, definition in REGISTRY["products"].items():
        if name in definition.get("skills", []):
            return product
    return None


def front_matter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    _, _, rest = text.partition("\n")
    body, _, _ = rest.partition("\n---")
    fields: dict[str, str] = {}
    for line in body.splitlines():
        key, separator, value = line.partition(":")
        if separator and not key.startswith(" "):
            fields[key.strip()] = value.strip().strip('"')
    return fields


def build() -> dict:
    skills = []
    for directory in sorted(SOURCE.iterdir()):
        if not directory.is_dir():
            continue
        document = directory / "SKILL.md"
        if not document.is_file():
            continue
        meta = front_matter(document.read_text(encoding="utf-8"))
        files = []
        for path in sorted(directory.rglob("*")):
            if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
                continue
            relative = path.relative_to(directory).as_posix()
            files.append(
                {
                    "uri": f"skill://pat/{directory.name}/{relative}",
                    "path": relative,
                    "mime_type": MIME.get(path.suffix, "text/plain"),
                    "bytes": path.stat().st_size,
                    "text": path.read_text(encoding="utf-8"),
                }
            )
        skills.append(
            {
                "name": directory.name,
                "product": owner(directory.name),
                "description": meta.get("description", ""),
                "uri": f"skill://pat/{directory.name}/SKILL.md",
                "files": files,
            }
        )
    return {"version": REGISTRY["distributions"]["openai"]["plugin"]["base_version"], "skills": skills}


if __name__ == "__main__":
    built = build()
    serialized = json.dumps(built, ensure_ascii=False, indent=2) + "\n"
    if "--check" in sys.argv:
        current = TARGET.read_text(encoding="utf-8") if TARGET.is_file() else ""
        if current != serialized:
            print("skill registry is stale; run scripts/build_skill_registry.py")
            raise SystemExit(1)
        print("skill registry matches skills/")
    else:
        TARGET.write_text(serialized, encoding="utf-8")
        total = sum(len(skill["files"]) for skill in built["skills"])
        print(f"registry: {len(built['skills'])} Skills, {total} files, {len(serialized):,} bytes")
