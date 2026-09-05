#!/usr/bin/env python3
"""Build the Document Files Skill for a private ChatGPT personal account."""

from __future__ import annotations

import argparse
import shutil
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCUMENT_FILES = ROOT / "plugins" / "document-files"
TOOLKIT_ICON = ROOT / "plugins" / "personal-agent-toolkit" / "assets" / "icon.png"
SKILL_NAME = "document-files"


def write_openai_yaml(target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        """interface:
  display_name: "Document Files"
  short_description: "문서·표·발표 자료를 읽고 만들고 편집합니다"
  icon_small: "./assets/icon.png"
  icon_large: "./assets/icon.png"
  brand_color: "#E86D5B"
  default_prompt: >-
    Use $document-files to read, create, or edit this document,
    spreadsheet, or presentation.
policy:
  allow_implicit_invocation: true
""",
        encoding="utf-8",
    )


def stage_skill(target: Path) -> None:
    source = DOCUMENT_FILES / "skills" / SKILL_NAME
    shutil.copytree(source, target)
    (target / "assets").mkdir(exist_ok=True)
    shutil.copy2(TOOLKIT_ICON, target / "assets" / "icon.png")
    write_openai_yaml(target / "agents" / "openai.yaml")

    runtime = target / "scripts" / "document-files"
    shutil.copytree(
        DOCUMENT_FILES / "openai-runtime",
        runtime,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    shutil.copytree(
        DOCUMENT_FILES / "src" / "document_files",
        runtime / "src" / "document_files",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    skill_path = target / "SKILL.md"
    contents = skill_path.read_text(encoding="utf-8")
    old = "${SKILL_DIR}/../../runtime/document-files/document-files"
    new = "${SKILL_DIR}/scripts/document-files/document-files"
    if old not in contents:
        raise ValueError("Document Files host runtime path was not found in SKILL.md")
    skill_path.write_text(contents.replace(old, new), encoding="utf-8")


def write_archive(source: Path, target: Path) -> None:
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(item for item in source.rglob("*") if item.is_file()):
            info = zipfile.ZipInfo(path.relative_to(source).as_posix())
            info.date_time = (1980, 1, 1, 0, 0, 0)
            info.create_system = 3
            mode = 0o755 if path.name == "document-files" else 0o644
            info.external_attr = mode << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "output",
        type=Path,
        help="directory that will receive document-files.skill for upload",
    )
    args = parser.parse_args()
    output = args.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / SKILL_NAME
        stage_skill(source)
        write_archive(source, output / f"{SKILL_NAME}.skill")

    print(f"Built Document Files personal Skill in {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
