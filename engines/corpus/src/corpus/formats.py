"""Source format declarations used for scanning and media-type metadata."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FormatSpec:
    extension: str
    media_type: str
    adapter: str


FORMAT_SPECS = {
    "md": FormatSpec("md", "text/markdown", "document-files"),
    "markdown": FormatSpec("markdown", "text/markdown", "document-files"),
    "txt": FormatSpec("txt", "text/plain", "document-files"),
    "html": FormatSpec("html", "text/html", "document-files"),
    "htm": FormatSpec("htm", "text/html", "document-files"),
    "pdf": FormatSpec("pdf", "application/pdf", "document-files"),
    "docx": FormatSpec(
        "docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "document-files",
    ),
    "pptx": FormatSpec(
        "pptx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "document-files",
    ),
    "xlsx": FormatSpec(
        "xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "document-files",
    ),
    "hwpx": FormatSpec(
        "hwpx",
        "application/vnd.hancom.hwpx",
        "document-files",
    ),
    "hwp": FormatSpec(
        "hwp",
        "application/x-hwp",
        "document-files",
    ),
}

IGNORED_NAMES = {".DS_Store"}


def classify(name: str) -> tuple[str, str | None, str | None, str]:
    if name in IGNORED_NAMES:
        return "", None, None, "ignored"
    extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    spec = FORMAT_SPECS.get(extension)
    if spec is None:
        return extension, None, None, "unsupported"
    return extension, spec.media_type, spec.adapter, "supported"
