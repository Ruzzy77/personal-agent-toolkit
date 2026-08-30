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
    _honest_completeness,
)
from .errors import BudgetExceededError, ExtractionError
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
            "page_ranges": "contiguous_current_projection_v1",
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
            adapter_version=f"1.3.0+source.{self.source_hash[:12]}",
            config=self.config,
            capabilities=AdapterCapabilities(
                format_ids=("pdf",),
                structural_unit_types=(
                    "page",
                    "page_region",
                    "paragraph",
                    "table_cell",
                ),
                execution_mode="jsonl_subprocess",
                preserves_reading_order=False,
                supports_geometry=True,
                supports_confidence=True,
                supports_ocr=True,
                may_emit_partial=True,
            ),
        )
        # Page selection and image-only support do not change already observed
        # PDF content. Retain this exact prior implementation on the same host;
        # the core separately queues its page-limit issues for a fresh range run.
        legacy = AdapterDescriptor.from_config(
            adapter_id=self.descriptor.adapter_id,
            adapter_version="1.2.0+source.ebd131a63d82",
            config={k: v for k, v in self.config.items() if k != "page_ranges"},
            capabilities=self.descriptor.capabilities,
        )
        self.compatible_projection_identities = frozenset(
            {
                (
                    legacy.adapter_id,
                    legacy.adapter_version,
                    legacy.config_hash,
                )
            }
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

    def _build(self, *, timeout_seconds: float = 120) -> Path:
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
                timeout=timeout_seconds,
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
        return self._extract_range(path, format_id=format_id, page_start=1)

    def _extract_range(self, path: Path, *, format_id: str, page_start: int):
        try:
            executable = self._build()
            config = {**self.config, "page_start": page_start}
            descriptor = AdapterDescriptor.from_config(
                adapter_id=self.descriptor.adapter_id,
                adapter_version=self.descriptor.adapter_version,
                config=config,
                capabilities=self.descriptor.capabilities,
            )
            adapter = ExternalJSONLAdapter(
                descriptor, (str(executable),), self.budgets, config=config
            )
            result = adapter.extract(path, format_id=format_id)
        except ExtractionError:
            result = self._extract_with_pypdf(
                path, format_id=format_id, page_start=page_start
            )
        if page_start == 1 and any(
            i.code == "pdf_page_unit_budget_exhausted" for i in result.issues
        ):
            raise BudgetExceededError(
                "The first PDF page exceeds the extraction result budget"
            )
        # A transport page selection is not a new extraction configuration.
        return ExtractionEnvelope.create(
            descriptor=self.descriptor,
            completeness=result.completeness,
            units=result.units,
            issues=result.issues,
        )

    def resume(self, path: Path, *, format_id: str, previous: ExtractionEnvelope):
        if previous.descriptor != self.descriptor:
            raise ExtractionError("PDF continuation requires the same adapter identity")
        coverage = [
            i.details for i in previous.issues if i.code == "pdf_page_range_observed"
        ]
        pending = [
            i.details for i in previous.issues if i.code == "pdf_page_range_pending"
        ]
        if len(coverage) != 1 or len(pending) != 1:
            raise ExtractionError("PDF continuation has no unique current page range")
        start = pending[0].get("next_page")
        total = coverage[0].get("document_pages")
        if (
            isinstance(start, bool)
            or not isinstance(start, int)
            or not isinstance(total, int)
            or not 1 < start <= total
            or coverage[0].get("page_start") != 1
            or coverage[0].get("page_end") != start - 1
            or pending[0].get("document_pages") != total
        ):
            raise ExtractionError("PDF continuation range is inconsistent")
        result = self._extract_range(path, format_id=format_id, page_start=start)
        if any(i.code == "pdf_page_unit_budget_exhausted" for i in result.issues):
            return ExtractionEnvelope.create(
                descriptor=self.descriptor,
                completeness="partial",
                units=previous.units,
                issues=(
                    *(i for i in previous.issues if i.code != "pdf_page_range_pending"),
                    *(
                        i
                        for i in result.issues
                        if i.code
                        not in {"pdf_page_range_observed", "pdf_page_range_pending"}
                    ),
                ),
            )
        observed = [i for i in result.issues if i.code == "pdf_page_range_observed"]
        if (
            len(observed) != 1
            or observed[0].details.get("page_start") != start
            or observed[0].details.get("document_pages") != total
            or not start <= observed[0].details.get("page_end", 0) <= total
        ):
            raise ExtractionError("PDF continuation made no contiguous page progress")
        end = observed[0].details["page_end"]
        if any(
            not isinstance(u.structure_path.get("page"), int)
            or not start <= u.structure_path["page"] <= end
            for u in result.units
        ):
            raise ExtractionError(
                "PDF continuation emitted a unit outside its page range"
            )
        progress_codes = {"pdf_page_range_observed", "pdf_page_range_pending"}
        old_issues = tuple(i for i in previous.issues if i.code not in progress_codes)
        units = (*previous.units, *result.units)
        if (
            len(units) > self.budgets.max_units
            or sum(len(u.content) for u in units) > self.budgets.max_total_content_chars
            or len(old_issues) + len(result.issues) > self.budgets.max_issues
        ):
            # Keep searchable coverage without endlessly rescheduling a full result.
            return ExtractionEnvelope.create(
                descriptor=self.descriptor,
                completeness="partial",
                units=previous.units,
                issues=(
                    *old_issues,
                    ExtractionIssue(
                        code="pdf_output_limit_reached",
                        severity="warning",
                        message="Further pages exceed the cumulative extraction result budget.",
                        details={"next_page": start, "document_pages": total},
                    ),
                ),
            )
        issues = (
            *old_issues,
            *(i for i in result.issues if i.code != "pdf_page_range_observed"),
            ExtractionIssue(
                code="pdf_page_range_observed",
                severity="info",
                message="The adapter observed this contiguous original page range.",
                details={"page_start": 1, "page_end": end, "document_pages": total},
            ),
        )
        return ExtractionEnvelope.create(
            descriptor=self.descriptor,
            completeness=_honest_completeness("complete", units, issues),
            units=units,
            issues=issues,
        )

    def _extract_with_pypdf(
        self,
        path: Path,
        *,
        format_id: str,
        page_start: int = 1,
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

        fallback = extract_pdf(
            path, page_start=page_start, max_pages=self.config["max_pages"]
        )

        def convert_issue(raw: dict) -> ExtractionIssue:
            code = raw.get("code", "extraction_warning")
            message = raw.get("message", "The PDF fallback reported an issue.")
            severity = raw.get(
                "severity", "info" if code == "unit_split" else "warning"
            )
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
        if (
            len(units) > self.budgets.max_units
            or sum(len(u.content) for u in units) > self.budgets.max_total_content_chars
            or any(len(u.content) > self.budgets.max_unit_content_chars for u in units)
        ):
            raise BudgetExceededError(
                "PDF fallback exceeds the extraction result budget"
            )
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
