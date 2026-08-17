from __future__ import annotations

import json
import struct
import sys
import tempfile
import unittest
import zipfile
import zlib
from dataclasses import FrozenInstanceError
from io import BytesIO
from pathlib import Path
from unittest import mock

from corpus.adapter_registry import build_default_registry
from corpus.adapters import (
    RESULT_SCHEMA_VERSION,
    AdapterBudgets,
    AdapterCapabilities,
    AdapterDescriptor,
    ExternalJSONLAdapter,
    ExtractedUnit,
    ExtractionEnvelope,
    builtin_adapter_descriptor,
    run_builtin_extraction,
)
from corpus.errors import BudgetExceededError, ExtractionError
from corpus.extractors import ExtractionResult, UnitDraft
from corpus.hwp5_adapter_main import (
    HWPAdapterError,
    _decode_paragraph,
    _extract,
    _inflate_raw_deflate,
    _records,
)
from corpus.native_adapters import _PDF_VISION_SOURCE, PDFKitVisionAdapter


def write_text_pdf(path: Path, text: bytes = b"Source backed PDF page") -> None:
    stream = b"BT /F1 12 Tf 72 720 Td (" + text + b") Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream",
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for object_number, body in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{object_number} 0 obj\n".encode())
        pdf.extend(body)
        pdf.extend(b"\nendobj\n")
    xref_offset = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode())
    pdf.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode()
    )
    path.write_bytes(pdf)


def external_descriptor(
    *,
    supports_geometry: bool = False,
    supports_ocr: bool = False,
    may_emit_partial: bool = True,
) -> AdapterDescriptor:
    return AdapterDescriptor.from_config(
        adapter_id="test.external.reader",
        adapter_version="1.0.0",
        config={},
        capabilities=AdapterCapabilities(
            format_ids=("hwp",),
            structural_unit_types=("paragraph",),
            execution_mode="jsonl_subprocess",
            supports_geometry=supports_geometry,
            supports_ocr=supports_ocr,
            may_emit_partial=may_emit_partial,
        ),
    )


def result_script(result: dict) -> str:
    encoded = json.dumps(result, ensure_ascii=False)
    return f"import sys\nsys.stdin.readline()\nprint({encoded!r})\n"


class BuiltinAdapterTest(unittest.TestCase):
    def test_builtin_result_is_deterministic_immutable_and_identity_neutral(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "source.md"
            path.write_text("# 제목\n\n본문", encoding="utf-8")
            first = run_builtin_extraction(path, "markdown")
            second = run_builtin_extraction(path, "markdown")

        self.assertEqual(first.manifest_hash, second.manifest_hash)
        self.assertEqual(first.completeness, "complete")
        self.assertEqual(first.descriptor.adapter_id, "work-corpus.builtin.markdown")
        self.assertEqual(first.units[0].unit_type, "heading")
        self.assertNotIn("document_id", first.to_dict())
        self.assertNotIn("revision_id", first.to_dict())
        self.assertNotIn("source_path", first.to_dict())
        with self.assertRaises(TypeError):
            first.units[0].structure_path["injected"] = True
        with self.assertRaises(FrozenInstanceError):
            first.completeness = "partial"

    def test_builtin_empty_result_is_partial(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "empty.txt"
            path.write_text("", encoding="utf-8")
            result = run_builtin_extraction(path, "text")

        self.assertEqual(result.completeness, "partial")
        self.assertEqual(result.units, ())
        self.assertEqual(result.issues[0].code, "no_extractable_text")

    @mock.patch("corpus.adapters.extract")
    def test_builtin_unverified_reading_order_is_declared_and_partial(self, extract) -> None:
        unit_types = {"docx": "paragraph", "pdf": "page", "pptx": "slide_text"}
        extract.side_effect = lambda _path, adapter_name: ExtractionResult(
            units=[UnitDraft(unit_types[adapter_name], {"ordinal": 1}, "text")]
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "source.bin"
            path.write_bytes(b"fixture")
            for adapter_name in ("docx", "pdf", "pptx"):
                with self.subTest(adapter=adapter_name):
                    result = run_builtin_extraction(path, adapter_name)
                    self.assertFalse(result.descriptor.capabilities.preserves_reading_order)
                    self.assertEqual(result.completeness, "partial")
                    self.assertIn(
                        "reading_order_unverified",
                        {issue.code for issue in result.issues},
                    )


class ExternalAdapterTest(unittest.TestCase):
    def test_external_adapter_preserves_virtual_environment_launcher_path(self) -> None:
        adapter = ExternalJSONLAdapter(
            external_descriptor(),
            (
                sys.executable,
                "-c",
                result_script(
                    {
                        "schema_version": RESULT_SCHEMA_VERSION,
                        "completeness": "partial",
                        "units": [],
                        "issues": [],
                    }
                ),
            ),
        )

        self.assertEqual(adapter.command[0], sys.executable)

    def test_external_jsonl_reads_only_fd_and_returns_valid_envelope(self) -> None:
        script = f"""
import json
import sys

request = json.loads(sys.stdin.readline())
with open(request["input"]["path"], encoding="utf-8") as source:
    content = source.read()
print(json.dumps({{
    "schema_version": {RESULT_SCHEMA_VERSION!r},
    "completeness": "complete",
    "units": [{{
        "unit_type": "paragraph",
        "structure_path": {{"paragraph": 1}},
        "content": content,
    }}],
    "issues": [],
}}, ensure_ascii=False))
"""
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "confidential-source.hwp"
            path.write_text("업무 원문", encoding="utf-8")
            before = path.read_bytes()
            adapter = ExternalJSONLAdapter(
                external_descriptor(),
                (sys.executable, "-c", script),
            )
            result = adapter.extract(path, format_id="hwp")

            self.assertEqual(path.read_bytes(), before)

        self.assertEqual(result.completeness, "complete")
        self.assertEqual(result.units[0].content, "업무 원문")
        self.assertEqual(result.units[0].structure_path["paragraph"], 1)
        self.assertNotIn("path", result.to_dict())

    def test_external_output_cannot_set_core_identity_or_source_path(self) -> None:
        result = {
            "schema_version": RESULT_SCHEMA_VERSION,
            "completeness": "complete",
            "units": [
                {
                    "unit_type": "paragraph",
                    "structure_path": {"paragraph": 1},
                    "content": "text",
                    "source_path": "/private/source.hwp",
                }
            ],
            "issues": [],
        }
        adapter = ExternalJSONLAdapter(
            external_descriptor(),
            (sys.executable, "-c", result_script(result)),
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "source.hwp"
            path.write_text("text", encoding="utf-8")
            with self.assertRaisesRegex(ExtractionError, "core-owned field"):
                adapter.extract(path, format_id="hwp")

    def test_external_output_cannot_set_uri_or_url_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "source.hwp"
            path.write_text("text", encoding="utf-8")
            for forbidden_field in ("uri", "url", "original_uri", "source_url"):
                with self.subTest(field=forbidden_field):
                    result = {
                        "schema_version": RESULT_SCHEMA_VERSION,
                        "completeness": "complete",
                        "units": [
                            {
                                "unit_type": "paragraph",
                                "structure_path": {"paragraph": 1},
                                "content": "text",
                            }
                        ],
                        "issues": [
                            {
                                "code": "external_reference",
                                "message": "reference",
                                "details": {forbidden_field: "file:///private/source.hwp"},
                            }
                        ],
                    }
                    adapter = ExternalJSONLAdapter(
                        external_descriptor(),
                        (sys.executable, "-c", result_script(result)),
                    )
                    with self.assertRaisesRegex(ExtractionError, "core-owned field"):
                        adapter.extract(path, format_id="hwp")

    def test_external_ocr_markers_require_declared_capability_and_exact_method(self) -> None:
        base_unit = {
            "unit_type": "paragraph",
            "structure_path": {"paragraph": 1},
            "content": "ocr text",
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "source.hwp"
            path.write_text("text", encoding="utf-8")

            for unit in (
                {**base_unit, "derivation_method": "ocr"},
                {**base_unit, "quality_flags": ["ocr"]},
                {**base_unit, "derivation_method": "ocr_v2"},
            ):
                with self.subTest(unit=unit):
                    result = {
                        "schema_version": RESULT_SCHEMA_VERSION,
                        "completeness": "complete",
                        "units": [unit],
                        "issues": [],
                    }
                    adapter = ExternalJSONLAdapter(
                        external_descriptor(),
                        (sys.executable, "-c", result_script(result)),
                    )
                    with self.assertRaises(ExtractionError):
                        adapter.extract(path, format_id="hwp")

            valid_result = {
                "schema_version": RESULT_SCHEMA_VERSION,
                "completeness": "complete",
                "units": [
                    {
                        **base_unit,
                        "derivation_method": "ocr",
                        "quality_flags": ["ocr"],
                    }
                ],
                "issues": [],
            }
            adapter = ExternalJSONLAdapter(
                external_descriptor(supports_ocr=True),
                (sys.executable, "-c", result_script(valid_result)),
            )
            envelope = adapter.extract(path, format_id="hwp")
            self.assertEqual(envelope.units[0].derivation_method, "ocr")

    def test_external_result_schema_is_strict(self) -> None:
        result = {
            "schema_version": RESULT_SCHEMA_VERSION,
            "completeness": "complete",
            "units": [],
            "issues": [],
            "document_metadata": {"title": "adapter-controlled"},
        }
        adapter = ExternalJSONLAdapter(
            external_descriptor(),
            (sys.executable, "-c", result_script(result)),
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "source.hwp"
            path.write_text("text", encoding="utf-8")
            with self.assertRaisesRegex(ExtractionError, "unknown fields"):
                adapter.extract(path, format_id="hwp")

    def test_external_warning_forces_honest_partial_result(self) -> None:
        result = {
            "schema_version": RESULT_SCHEMA_VERSION,
            "completeness": "complete",
            "units": [
                {
                    "unit_type": "paragraph",
                    "structure_path": {"paragraph": 1},
                    "content": "partial text",
                }
            ],
            "issues": [
                {
                    "code": "embedded_object_skipped",
                    "message": "One embedded object was not extracted.",
                    "severity": "warning",
                }
            ],
        }
        adapter = ExternalJSONLAdapter(
            external_descriptor(),
            (sys.executable, "-c", result_script(result)),
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "source.hwp"
            path.write_text("text", encoding="utf-8")
            envelope = adapter.extract(path, format_id="hwp")

        self.assertEqual(envelope.completeness, "partial")

    def test_external_timeout_is_bounded(self) -> None:
        script = "import sys, time\nsys.stdin.readline()\ntime.sleep(2)\n"
        adapter = ExternalJSONLAdapter(
            external_descriptor(),
            (sys.executable, "-c", script),
            AdapterBudgets(timeout_seconds=0.05),
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "source.hwp"
            path.write_text("text", encoding="utf-8")
            with self.assertRaisesRegex(BudgetExceededError, "timeout"):
                adapter.extract(path, format_id="hwp")

    def test_external_stdout_is_hard_bounded(self) -> None:
        script = "import sys\nsys.stdin.readline()\nsys.stdout.write('x' * 100000)\n"
        adapter = ExternalJSONLAdapter(
            external_descriptor(),
            (sys.executable, "-c", script),
            AdapterBudgets(max_stdout_bytes=512),
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "source.hwp"
            path.write_text("text", encoding="utf-8")
            with self.assertRaisesRegex(BudgetExceededError, "stdout"):
                adapter.extract(path, format_id="hwp")


class PackagedAdapterTest(unittest.TestCase):
    def test_pdf_native_source_is_installed_package_data(self) -> None:
        self.assertTrue(_PDF_VISION_SOURCE.is_file())
        self.assertEqual(_PDF_VISION_SOURCE.parent.name, "native")
        self.assertEqual(_PDF_VISION_SOURCE.parent.parent.name, "corpus")

    def test_default_registry_routes_pdf_and_binary_hwp_independently(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            registry = build_default_registry(Path(temporary) / "runtime")

        pdf = registry.resolve("pdf").descriptor
        hwp = registry.resolve("hwp").descriptor
        self.assertEqual(pdf.adapter_id, "work-corpus.native.pdfkit-vision")
        self.assertTrue(pdf.capabilities.supports_ocr)
        self.assertTrue(pdf.capabilities.supports_geometry)
        self.assertIn("table_cell", pdf.capabilities.structural_unit_types)
        self.assertEqual(hwp.adapter_id, "work-corpus.hwp5.spec-partial")
        self.assertFalse(hwp.capabilities.supports_ocr)
        self.assertTrue(hwp.capabilities.may_emit_partial)

    @unittest.skipUnless(sys.platform == "darwin", "PDFKit is available only on macOS")
    def test_pdf_native_adapter_treats_blank_page_as_observed(self) -> None:
        from pypdf import PdfReader, PdfWriter

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first_page = root / "text.pdf"
            path = root / "text-with-blank-page.pdf"
            write_text_pdf(first_page, b"Searchable first page with enough native text")
            writer = PdfWriter()
            writer.add_page(PdfReader(first_page).pages[0])
            writer.add_blank_page(width=100, height=100)
            with path.open("wb") as handle:
                writer.write(handle)
            result = PDFKitVisionAdapter(root / "runtime").extract(
                path,
                format_id="pdf",
            )

        self.assertEqual(result.completeness, "complete")
        self.assertEqual(len(result.units), 1)
        self.assertNotIn("pdf_page_without_text", {issue.code for issue in result.issues})

    @unittest.skipUnless(sys.platform == "darwin", "PDFKit is available only on macOS")
    def test_pdf_native_adapter_uses_hybrid_ocr_and_keeps_text_pdf_complete(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "text.pdf"
            write_text_pdf(path, b"This native PDF page has enough searchable text for indexing")
            adapter = PDFKitVisionAdapter(root / "runtime")
            result = adapter.extract(path, format_id="pdf")

        self.assertEqual(adapter.config["ocr_scope"], "hybrid")
        self.assertEqual(result.completeness, "complete")
        self.assertEqual(
            [unit.derivation_method for unit in result.units],
            ["native_text"],
        )
        self.assertIn("reading_order_unverified", result.units[0].quality_flags)
        self.assertEqual(result.issues, ())

    @unittest.skipUnless(sys.platform == "darwin", "PDFKit is available only on macOS")
    def test_pdf_native_adapter_falls_back_to_pypdf_on_native_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "fallback.pdf"
            write_text_pdf(path, b"PDFKit compatibility fallback text")
            adapter = PDFKitVisionAdapter(root / "runtime")
            with (
                mock.patch.object(adapter, "_build", return_value=Path(sys.executable)),
                mock.patch(
                    "corpus.native_adapters.ExternalJSONLAdapter.extract",
                    side_effect=ExtractionError("native PDF adapter failed"),
                ),
            ):
                result = adapter.extract(path, format_id="pdf")

        self.assertEqual(result.descriptor, adapter.descriptor)
        self.assertEqual(result.completeness, "complete")
        self.assertEqual(
            [unit.content for unit in result.units],
            ["PDFKit compatibility fallback text"],
        )
        self.assertIn("pypdf_fallback", result.units[0].quality_flags)
        self.assertIn("reading_order_unverified", result.units[0].quality_flags)
        self.assertEqual(result.issues, ())

    def test_hwpx_security_change_does_not_invalidate_other_builtin_formats(
        self,
    ) -> None:
        self.assertEqual(
            builtin_adapter_descriptor("hwpx").adapter_version,
            "source-units-v5",
        )
        self.assertEqual(
            builtin_adapter_descriptor("markdown").adapter_version,
            "source-units-v4",
        )

    def test_hwpx_router_preserves_zip_hwpx_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            registry = build_default_registry(Path(temporary) / "runtime")
            path = Path(temporary) / "sample.hwpx"
            xml = """<?xml version="1.0" encoding="UTF-8"?>
            <hs:sec xmlns:hs="urn:section" xmlns:hp="urn:paragraph">
              <hp:p><hp:run><hp:t>정상 HWPX</hp:t></hp:run></hp:p>
            </hs:sec>
            """
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("Contents/section0.xml", xml)

            binary_hwp = registry.resolve("hwp")
            with mock.patch.object(
                binary_hwp,
                "extract",
                side_effect=AssertionError("binary HWP adapter must not be called"),
            ) as binary_extract:
                result = registry.resolve("hwpx").extract(path, format_id="hwpx")

        binary_extract.assert_not_called()
        self.assertEqual([unit.content for unit in result.units], ["정상 HWPX"])
        self.assertEqual(
            result.descriptor.adapter_id,
            "work-corpus.hwpx.content-router",
        )

    def test_hwpx_router_sends_ole_bytes_to_binary_hwp_adapter_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            registry = build_default_registry(Path(temporary) / "runtime")
            path = Path(temporary) / "misnamed.hwpx"
            original = bytes.fromhex("d0cf11e0a1b11ae1") + b"binary-hwp-fixture"
            path.write_bytes(original)

            binary_hwp = registry.resolve("hwp")
            binary_result = ExtractionEnvelope.create(
                descriptor=binary_hwp.descriptor,
                completeness="partial",
                units=(
                    ExtractedUnit(
                        unit_type="section_paragraph",
                        structure_path={"section": 1, "paragraph": 1},
                        content="binary HWP 본문",
                        quality_flags=("binary_hwp", "structure_partial"),
                    ),
                ),
            )
            with mock.patch.object(
                binary_hwp,
                "extract",
                return_value=binary_result,
            ) as binary_extract:
                result = registry.resolve("hwpx").extract(path, format_id="hwpx")

            self.assertEqual(path.read_bytes(), original)

        binary_extract.assert_called_once_with(path, format_id="hwp")
        self.assertEqual([unit.content for unit in result.units], ["binary HWP 본문"])
        self.assertEqual(result.completeness, "partial")
        self.assertIn(
            "hwpx_contains_binary_hwp",
            {issue.code for issue in result.issues},
        )
        self.assertEqual(
            result.descriptor.adapter_id,
            "work-corpus.hwpx.content-router",
        )

    def test_hwp5_paragraph_control_decoder_preserves_text_boundaries(self) -> None:
        def extended_control(code: int) -> bytes:
            return struct.pack("<H", code) + (b"\x00" * 12) + struct.pack("<H", code)

        payload = (
            "산업".encode("utf-16-le")
            + extended_control(9)
            + "AI".encode("utf-16-le")
            + struct.pack("<H", 10)
            + struct.pack("<H", 30)
            + "과제".encode("utf-16-le")
            + struct.pack("<H", 13)
        )

        text, controls, anomalies = _decode_paragraph(payload)

        self.assertEqual(text, "산업\tAI\n 과제")
        self.assertEqual(dict(controls), {9: 1, 10: 1, 30: 1, 13: 1})
        self.assertEqual(anomalies, [])

    def test_hwp5_record_and_raw_deflate_boundaries_are_bounded(self) -> None:
        payload = "본문".encode("utf-16-le")
        short_header = 0x43 | (1 << 10) | (len(payload) << 20)
        record_stream = struct.pack("<I", short_header) + payload
        compressed = zlib.compressobj(wbits=-15)
        deflated = compressed.compress(record_stream) + compressed.flush()

        inflated = _inflate_raw_deflate(deflated, limit=1024)
        records = list(_records(inflated, max_records=2))

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0][1:3], (0x43, 1))
        self.assertEqual(records[0][3], payload)
        with self.assertRaisesRegex(Exception, "byte budget"):
            _inflate_raw_deflate(deflated, limit=2)

    def test_hwp5_child_enforces_exact_fd_and_result_budgets(self) -> None:
        paragraphs = []
        for text in ("첫째", "둘째"):
            payload = text.encode("utf-16-le")
            header = 0x43 | (1 << 10) | (len(payload) << 20)
            paragraphs.append(struct.pack("<I", header) + payload)
        section = b"".join(paragraphs)
        file_header = bytearray(40)
        file_header[: len(b"HWP Document File")] = b"HWP Document File"

        class FakeCompound:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def exists(self, name):
                return name == "FileHeader"

            def listdir(self, **_kwargs):
                return [["BodyText", "Section0"]]

            def openstream(self, name):
                if name == "FileHeader":
                    return BytesIO(bytes(file_header))
                return BytesIO(section)

        opened_file_descriptors = []

        def open_fake_compound(source, **_kwargs):
            self.assertTrue(hasattr(source, "read"))
            opened_file_descriptors.append(source.fileno())
            return FakeCompound()

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "input.hwp"
            path.write_bytes(b"fixture")
            file_descriptor = path.open("rb")
            try:
                base_request = {
                    "schema_version": "corpus.extraction-request.v1",
                    "operation": "extract",
                    "input": {
                        "kind": "read_only_file_descriptor",
                        "file_descriptor": file_descriptor.fileno(),
                        "path": f"/dev/fd/{file_descriptor.fileno()}",
                        "format_id": "hwp",
                    },
                    "config": {},
                    "budgets": {
                        "max_units": 1,
                        "max_unit_content_chars": 100,
                        "max_total_content_chars": 100,
                    },
                }
                with mock.patch(
                    "corpus.hwp5_adapter_main.olefile.OleFileIO",
                    side_effect=open_fake_compound,
                ):
                    with self.assertRaisesRegex(HWPAdapterError, "unit count"):
                        _extract(base_request)
                    self.assertEqual(len(opened_file_descriptors), 1)
                    self.assertNotEqual(opened_file_descriptors[0], file_descriptor.fileno())
                    traversal = {
                        **base_request,
                        "input": {
                            **base_request["input"],
                            "path": "/dev/fd/../../tmp/other.hwp",
                        },
                    }
                    with self.assertRaisesRegex(HWPAdapterError, "invalid input"):
                        _extract(traversal)
            finally:
                file_descriptor.close()


if __name__ == "__main__":
    unittest.main()
