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
    ExtractedUnit,
    ExtractionEnvelope,
    ExtractionIssue,
)
from .errors import ExtractionError
from .extractors import extract_pdf

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
            "ocr_scope": "hybrid",
            "native_text_min_alphanumeric_characters": 32,
            "blank_page_detection": "rendered_luma_near_white_v1",
            "fallback_backend": "pypdf",
            "fallback_scope": "native_adapter_error",
            "runtime": _macos_runtime_identity(),
        }
        self.descriptor = AdapterDescriptor.from_config(
            adapter_id="work-corpus.native.pdfkit-vision",
            adapter_version=f"1.2.0+source.{self.source_hash[:12]}",
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
        try:
            return adapter.extract(path, format_id=format_id)
        except ExtractionError:
            return self._extract_with_pypdf(path, format_id=format_id)

    def _extract_with_pypdf(
        self,
        path: Path,
        *,
        format_id: str,
    ) -> ExtractionEnvelope:
        """Recover PDFs that the host PDFKit cannot open or process."""

        path = Path(path)
        if format_id != "pdf":
            raise ExtractionError(
                "PDF fallback does not support this format",
                details={"format_id": format_id},
            )
        if not path.is_file():
            raise ExtractionError("adapter input must be an existing regular file")
        input_bytes = path.stat().st_size
        if input_bytes > self.budgets.max_input_bytes:
            raise ExtractionError(
                "PDF fallback input exceeds its byte budget",
                details={"count": input_bytes, "limit": self.budgets.max_input_bytes},
            )

        fallback = extract_pdf(path)

        def convert_issue(raw: dict) -> ExtractionIssue:
            code = raw.get("code", "extraction_warning")
            message = raw.get("message", "The PDF fallback reported an issue.")
            severity = raw.get("severity", "info" if code == "unit_split" else "warning")
            details = {
                key: value
                for key, value in raw.items()
                if key not in {"code", "message", "severity"}
            }
            return ExtractionIssue(
                code=str(code),
                message=str(message),
                severity=severity,
                details=details,
            )

        units = tuple(
            ExtractedUnit(
                unit_type=unit.unit_type,
                structure_path=unit.structure_path,
                content=unit.content.encode("utf-8", errors="replace").decode("utf-8"),
                derivation_method="native_text",
                quality_flags=("pypdf_fallback", "reading_order_unverified"),
                issues=tuple(convert_issue(issue) for issue in unit.issues),
            )
            for unit in fallback.units
        )
        issues = tuple(convert_issue(issue) for issue in fallback.issues)
        material_issues = [
            issue
            for issue in (*issues, *(item for unit in units for item in unit.issues))
            if issue.severity in {"warning", "error"} and issue.code != "unit_split"
        ]
        completeness = "complete" if units and not material_issues else "partial"
        return ExtractionEnvelope.create(
            descriptor=self.descriptor,
            completeness=completeness,
            units=units,
            issues=issues,
        )
