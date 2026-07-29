"""Packaged subprocess adapters backed by local operating-system capabilities."""

from __future__ import annotations

import hashlib
import os
import platform
import subprocess
import tempfile
from contextlib import suppress
from pathlib import Path

from .adapters import (
    AdapterBudgets,
    AdapterCapabilities,
    AdapterDescriptor,
    ExternalJSONLAdapter,
    ExtractionEnvelope,
)
from .errors import ExtractionError

_PDF_VISION_SOURCE = (
    Path(__file__).resolve().with_name("native") / "corpus_pdf_vision.swift"
)


def _source_digest(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ExtractionError(
            "packaged adapter source is unavailable",
            details={"source_name": path.name},
        ) from exc


def _macos_runtime_identity() -> dict[str, str]:
    release, version_info, machine = platform.mac_ver()
    return {
        "macos_release": release or "unknown",
        "macos_version_info": ".".join(version_info) if version_info else "unknown",
        "machine": machine or platform.machine() or "unknown",
    }


class PDFKitVisionAdapter:
    """Read PDF text and OCR page images using the host PDFKit and Vision."""

    def __init__(self, runtime_root: Path) -> None:
        self.runtime_root = Path(runtime_root).expanduser().resolve()
        self.source_hash = _source_digest(_PDF_VISION_SOURCE)
        self.config = {
            "max_pages": 200,
            "max_edge_pixels": 3_000,
            "recognition_languages": ["ko-KR", "en-US"],
            "ocr_scope": "all_pages",
            "runtime": _macos_runtime_identity(),
        }
        self.descriptor = AdapterDescriptor.from_config(
            adapter_id="work-corpus.native.pdfkit-vision",
            adapter_version=f"1.0.0+source.{self.source_hash[:12]}",
            config=self.config,
            capabilities=AdapterCapabilities(
                format_ids=("pdf",),
                structural_unit_types=("page", "page_region", "paragraph", "table_cell"),
                execution_mode="jsonl_subprocess",
                preserves_reading_order=False,
                supports_geometry=True,
                supports_confidence=True,
                supports_ocr=True,
                may_emit_partial=True,
            ),
        )
        self.budgets = AdapterBudgets(
            timeout_seconds=180,
            max_input_bytes=2 * 1024 * 1024 * 1024,
            max_stdout_bytes=128 * 1024 * 1024,
            max_units=250_000,
            max_total_content_chars=150_000_000,
        )

    @property
    def executable(self) -> Path:
        return self.runtime_root / f"pdfkit-vision-{self.source_hash[:16]}"

    def _build(self) -> Path:
        executable = self.executable
        if executable.is_file() and os.access(executable, os.X_OK):
            return executable
        self.runtime_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with suppress(PermissionError):
            self.runtime_root.chmod(0o700)
        with tempfile.NamedTemporaryFile(
            prefix=".pdfkit-vision-build-",
            dir=self.runtime_root,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
        temporary_path.unlink(missing_ok=True)
        try:
            result = subprocess.run(
                (
                    "/usr/bin/xcrun",
                    "swiftc",
                    "-warnings-as-errors",
                    "-parse-as-library",
                    "-O",
                    str(_PDF_VISION_SOURCE),
                    "-o",
                    str(temporary_path),
                ),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                check=False,
                timeout=120,
            )
            if result.returncode != 0:
                raise ExtractionError(
                    "could not build the packaged PDF OCR adapter",
                    details={
                        "return_code": result.returncode,
                        "stderr_sha256": hashlib.sha256(result.stderr).hexdigest(),
                    },
                )
            temporary_path.chmod(0o700)
            os.replace(temporary_path, executable)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ExtractionError(
                "could not build the packaged PDF OCR adapter",
                details={"error_type": type(exc).__name__},
            ) from exc
        finally:
            temporary_path.unlink(missing_ok=True)
        return executable

    def extract(self, path: Path, *, format_id: str) -> ExtractionEnvelope:
        executable = self._build()
        adapter = ExternalJSONLAdapter(
            self.descriptor,
            (str(executable),),
            self.budgets,
            config=self.config,
        )
        return adapter.extract(path, format_id=format_id)
