from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from corpus.adapter_registry import build_default_registry
from corpus.adapters import (
    RESULT_SCHEMA_VERSION,
    AdapterBudgets,
    AdapterCapabilities,
    AdapterDescriptor,
    ExternalJSONLAdapter,
)
from corpus.errors import BudgetExceededError, ExtractionError
from corpus.service import _validate_ingest_budgets


def external_descriptor(
    *, supports_geometry: bool = False, may_emit_partial: bool = True
) -> AdapterDescriptor:
    return AdapterDescriptor.from_config(
        adapter_id="test.external.reader",
        adapter_version="1.0.0",
        config={},
        capabilities=AdapterCapabilities(
            format_ids=("txt",),
            structural_unit_types=("paragraph",),
            execution_mode="jsonl_subprocess",
            supports_geometry=supports_geometry,
            may_emit_partial=may_emit_partial,
        ),
    )


def result_script(result: dict) -> str:
    encoded = json.dumps(result, ensure_ascii=False)
    return f"import sys\nsys.stdin.readline()\nprint({encoded!r})\n"


class ExternalAdapterBoundaryTest(unittest.TestCase):
    def test_external_jsonl_uses_inherited_fd_and_returns_valid_envelope(self) -> None:
        result = {
            "schema_version": RESULT_SCHEMA_VERSION,
            "completeness": "complete",
            "units": [
                {
                    "unit_type": "paragraph",
                    "structure_path": {"ordinal": 1},
                    "content": "본문",
                }
            ],
            "issues": [],
        }
        adapter = ExternalJSONLAdapter(
            external_descriptor(),
            (sys.executable, "-c", result_script(result)),
            config={},
        )
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source.txt"
            source.write_text("원본", encoding="utf-8")
            before = source.read_bytes()
            extracted = adapter.extract(source, format_id="txt")
            self.assertEqual(source.read_bytes(), before)

        self.assertEqual(extracted.completeness, "complete")
        self.assertEqual([unit.content for unit in extracted.units], ["본문"])
        self.assertNotIn("source_path", extracted.to_dict())

    def test_external_output_cannot_set_index_identity_or_source_location(self) -> None:
        for forbidden in (
            {"document_id": "injected"},
            {"source_unit_id": "injected"},
            {"path": "/private/source.txt"},
            {"source_url": "file:///private/source.txt"},
        ):
            with self.subTest(forbidden=next(iter(forbidden))):
                result = {
                    "schema_version": RESULT_SCHEMA_VERSION,
                    "completeness": "complete",
                    "units": [
                        {
                            "unit_type": "paragraph",
                            "structure_path": {"ordinal": 1, **forbidden},
                            "content": "본문",
                        }
                    ],
                    "issues": [],
                }
                adapter = ExternalJSONLAdapter(
                    external_descriptor(),
                    (sys.executable, "-c", result_script(result)),
                    config={},
                )
                with tempfile.TemporaryDirectory() as temporary:
                    source = Path(temporary) / "source.txt"
                    source.write_text("원본", encoding="utf-8")
                    with self.assertRaises(ExtractionError):
                        adapter.extract(source, format_id="txt")

    def test_warning_forces_honest_partial_result(self) -> None:
        result = {
            "schema_version": RESULT_SCHEMA_VERSION,
            "completeness": "complete",
            "units": [
                {
                    "unit_type": "paragraph",
                    "structure_path": {"ordinal": 1},
                    "content": "본문",
                }
            ],
            "issues": [
                {
                    "code": "coverage_gap",
                    "message": "Some source content was not observed.",
                    "severity": "warning",
                    "details": {},
                }
            ],
        }
        adapter = ExternalJSONLAdapter(
            external_descriptor(),
            (sys.executable, "-c", result_script(result)),
            config={},
        )
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source.txt"
            source.write_text("원본", encoding="utf-8")
            extracted = adapter.extract(source, format_id="txt")
        self.assertEqual(extracted.completeness, "partial")

    def test_timeout_and_stdout_budgets_are_hard_limits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source.txt"
            source.write_text("원본", encoding="utf-8")
            timeout = ExternalJSONLAdapter(
                external_descriptor(),
                (sys.executable, "-c", "import time; time.sleep(10)"),
                AdapterBudgets(timeout_seconds=0.05),
                config={},
            )
            with self.assertRaisesRegex(BudgetExceededError, "timeout"):
                timeout.extract(source, format_id="txt")

            noisy = ExternalJSONLAdapter(
                external_descriptor(),
                (
                    sys.executable,
                    "-c",
                    "import sys; sys.stdin.readline(); sys.stdout.write('x' * 100000)",
                ),
                AdapterBudgets(max_stdout_bytes=512),
                config={},
            )
            with self.assertRaisesRegex(BudgetExceededError, "stdout"):
                noisy.extract(source, format_id="txt")


class DocumentFilesIntegrationTest(unittest.TestCase):
    def test_default_registry_uses_only_document_files_process_routes(self) -> None:
        registry = build_default_registry()
        for format_id in (
            "docx",
            "htm",
            "html",
            "hwp",
            "hwpx",
            "markdown",
            "md",
            "pdf",
            "pptx",
            "txt",
            "xlsx",
        ):
            descriptor = registry.resolve(format_id).descriptor
            self.assertEqual(
                descriptor.adapter_id,
                f"document-files.process.{format_id}",
            )
            self.assertEqual(
                descriptor.capabilities.execution_mode,
                "jsonl_subprocess",
            )

    def test_document_files_process_supplies_units_without_index_fields(self) -> None:
        registry = build_default_registry()
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source.md"
            source.write_text("# 제목\n\n본문", encoding="utf-8")
            extracted = registry.resolve("md").extract(source, format_id="md")
        self.assertEqual(extracted.completeness, "complete")
        self.assertEqual(
            [(unit.unit_type, unit.content) for unit in extracted.units],
            [("heading", "제목"), ("paragraph", "본문")],
        )
        self.assertNotIn("document_id", extracted.to_dict())
        self.assertNotIn("source_path", extracted.to_dict())

    def test_exact_document_selection_retains_one_gibibyte_capture_ceiling(self) -> None:
        mib = 1024 * 1024
        with self.assertRaises(BudgetExceededError):
            _validate_ingest_budgets(
                max_files=1,
                max_bytes=500 * mib,
                max_file_bytes=251 * mib,
                timeout_seconds=120,
                exact_selection=False,
            )
        _validate_ingest_budgets(
            max_files=1,
            max_bytes=1024 * mib,
            max_file_bytes=1024 * mib,
            timeout_seconds=120,
            exact_selection=True,
        )


if __name__ == "__main__":
    unittest.main()
