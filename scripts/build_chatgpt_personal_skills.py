#!/usr/bin/env python3
"""Build the five document Skills for a private ChatGPT personal account."""

from __future__ import annotations

import argparse
import shutil
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCUMENT_FILES = ROOT / "plugins" / "document-files"
TOOLKIT_ICON = ROOT / "plugins" / "personal-agent-toolkit" / "assets" / "icon.png"
SKILLS = ("document-files", "documents", "pdf", "spreadsheets", "presentations")
CHATGPT_SKILL_NAMES = {
    "documents": "word-documents",
    "pdf": "pdf-files",
    "spreadsheets": "workbooks",
    "presentations": "slide-decks",
}

CHATGPT_PERSONAL_BOUNDARY = """## 개인 ChatGPT 실행

이 Skill이 명시적으로 선택된 작업에서는 같은 기능의 OpenAI 기본 plugin이나 Library를 함께
호출하지 않는다. 현재 작업의 파일과 OpenAI 호스트의 실행 환경만 사용한다.
"""

INTERFACE = {
    "document-files": (
        "Document Files",
        "문서의 구조와 값을 읽고 HWP/HWPX를 다룹니다",
        "Use $document-files to inspect this document and extract its explicit structure and values.",
    ),
    "documents": (
        "Documents",
        "DOCX 문서를 만들고 편집합니다",
        "Use $documents to create or edit this DOCX document.",
    ),
    "pdf": (
        "PDF",
        "PDF를 읽고 만들며 페이지와 양식을 다룹니다",
        "Use $pdf to read, create, or edit this PDF.",
    ),
    "spreadsheets": (
        "Spreadsheets",
        "XLSX와 CSV를 만들고 편집하고 분석합니다",
        "Use $spreadsheets to create, edit, or analyze this workbook.",
    ),
    "presentations": (
        "Presentations",
        "PPTX 프레젠테이션을 만들고 편집합니다",
        "Use $presentations to create or edit this presentation.",
    ),
}


def write_openai_yaml(target: Path, skill_name: str) -> None:
    display_name, short_description, default_prompt = INTERFACE[skill_name]
    chatgpt_name = CHATGPT_SKILL_NAMES.get(skill_name, skill_name)
    default_prompt = default_prompt.replace(f"${skill_name}", f"${chatgpt_name}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "\n".join(
            (
                "interface:",
                f'  display_name: "{display_name}"',
                f'  short_description: "{short_description}"',
                '  icon_small: "./assets/icon.png"',
                '  icon_large: "./assets/icon.png"',
                '  brand_color: "#E86D5B"',
                f'  default_prompt: "{default_prompt}"',
                "policy:",
                "  allow_implicit_invocation: true",
                "",
            )
        ),
        encoding="utf-8",
    )


def stage_skill(skill_name: str, target: Path) -> None:
    source = DOCUMENT_FILES / "skills" / skill_name
    shutil.copytree(source, target)
    (target / "assets").mkdir(exist_ok=True)
    shutil.copy2(TOOLKIT_ICON, target / "assets" / "icon.png")
    write_openai_yaml(target / "agents" / "openai.yaml", skill_name)

    chatgpt_name = CHATGPT_SKILL_NAMES.get(skill_name)
    if chatgpt_name is not None:
        skill_path = target / "SKILL.md"
        contents = skill_path.read_text(encoding="utf-8")
        marker = f"name: {skill_name}"
        if marker not in contents:
            raise ValueError(f"{skill_name} name was not found in SKILL.md")
        contents = contents.replace(marker, f"name: {chatgpt_name}", 1)
        heading = f"# {INTERFACE[skill_name][0]}\n"
        if heading not in contents:
            raise ValueError(f"{skill_name} heading was not found in SKILL.md")
        contents = contents.replace(
            heading,
            f"{heading}\n{CHATGPT_PERSONAL_BOUNDARY}\n",
            1,
        )
        skill_path.write_text(contents, encoding="utf-8")

    if skill_name != "document-files":
        return

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
    new = 'python3 "${SKILL_DIR}/scripts/document-files/host_cli.py"'
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
        help="directory that will receive five .skill upload archives",
    )
    args = parser.parse_args()
    output = args.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as directory:
        staging = Path(directory)
        for skill_name in SKILLS:
            source = staging / skill_name
            stage_skill(skill_name, source)
            write_archive(source, output / f"{skill_name}.skill")

    print(f"Built {len(SKILLS)} ChatGPT personal Skills in {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
