from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from corpus.adapter_registry import build_default_registry
from corpus.adapters import (
    AdapterDescriptor,
    ExtractedUnit,
    ExtractionEnvelope,
    ExtractionIssue,
)
from corpus.database import corpus_read_connection
from corpus.errors import BudgetExceededError, ExtractionError
from corpus.native_adapters import PDFKitVisionAdapter
from corpus.service import CorpusService
from test_adapters import write_text_pdf


@unittest.skipUnless(sys.platform == "darwin", "PDFKit requires macOS")
class PDFContinuationTest(unittest.TestCase):
    def test_exact_legacy_pdf_identity_keeps_observed_files_but_queues_page_limits(
        self,
    ):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source = base / "source"
            source.mkdir()
            (source / "long.pdf").write_bytes(b"long")
            (source / "complete.pdf").write_bytes(b"complete")
            current = PDFKitVisionAdapter(base / "runtime")
            aid, version, config_hash = next(
                iter(current.compatible_projection_identities)
            )

            class Legacy:
                descriptor = AdapterDescriptor(
                    aid, version, config_hash, current.descriptor.capabilities
                )

                def extract(self, path, *, format_id):
                    partial = path.read_bytes() == b"long"
                    return ExtractionEnvelope.create(
                        descriptor=self.descriptor,
                        completeness="partial" if partial else "complete",
                        units=[
                            ExtractedUnit("page", {"page": 1}, "Original native text")
                        ],
                        issues=[
                            ExtractionIssue(
                                "pdf_page_limit_reached",
                                "Remaining pages",
                                "warning",
                                {"processed_pages": 200, "document_pages": 201},
                            )
                        ]
                        if partial
                        else [],
                    )

            legacy = Legacy()
            service = CorpusService(
                base / "private",
                adapter_registry=build_default_registry(
                    base / "runtime", overrides={"pdf": legacy}
                ),
            )
            service.register(
                corpus_id="legacy", source_root=source, execution_policy="local_only"
            )
            service.sync("legacy")
            before = {
                d["relative_path"]: d for d in service.inventory("legacy")["documents"]
            }
            service = CorpusService(
                base / "private",
                adapter_registry=build_default_registry(
                    base / "runtime", overrides={"pdf": current}
                ),
            )
            inventory = {
                d["relative_path"]: d for d in service.inventory("legacy")["documents"]
            }
            self.assertEqual(
                inventory["long.pdf"]["refresh_reasons"], ["extraction_continuation"]
            )
            self.assertEqual(inventory["complete.pdf"]["index_state"], "current")
            result = ExtractionEnvelope.create(
                descriptor=current.descriptor,
                completeness="complete",
                units=[
                    ExtractedUnit("page", {"page": 1}, "Original native text"),
                    ExtractedUnit("page", {"page": 201}, "Last native text"),
                ],
            )
            with mock.patch.object(current, "extract", return_value=result) as extract:
                service.sync("legacy")
                self.assertEqual(extract.call_count, 1)
            after = {
                d["relative_path"]: d for d in service.inventory("legacy")["documents"]
            }
            self.assertEqual(
                after["complete.pdf"]["active_projection_id"],
                before["complete.pdf"]["active_projection_id"],
            )
            self.assertEqual(after["long.pdf"]["index_state"], "current")

    def test_fallback_ranges_and_terminal_single_page_budget(self):
        from pypdf import PdfReader, PdfWriter

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            page_path, path = base / "page.pdf", base / "long.pdf"
            write_text_pdf(
                page_path, b"Native text which exceeds one character on every page"
            )
            writer = PdfWriter()
            for _ in range(201):
                writer.add_page(PdfReader(page_path).pages[0])
            writer.write(path)
            adapter = PDFKitVisionAdapter(base / "runtime")
            with mock.patch.object(
                adapter, "_build", side_effect=ExtractionError("native unavailable")
            ):
                first = adapter.extract(path, format_id="pdf")
                resumed = adapter.resume(path, format_id="pdf", previous=first)
            self.assertEqual(len(resumed.units), 201)
            self.assertEqual(resumed.completeness, "complete")
            self.assertIn("pypdf_fallback", resumed.units[-1].quality_flags)
            # This next range is too dense for its configured unit budget.
            adapter.budgets = replace(adapter.budgets, max_unit_content_chars=1)
            terminal = adapter.resume(path, format_id="pdf", previous=first)
            self.assertEqual(terminal.units, first.units)
            self.assertNotIn(
                "pdf_page_range_pending", {i.code for i in terminal.issues}
            )
            self.assertIn(
                "pdf_page_unit_budget_exhausted", {i.code for i in terminal.issues}
            )
            with self.assertRaises(BudgetExceededError):
                adapter.extract(path, format_id="pdf")

    def test_bounded_current_projection_continuation_and_failure_retention(self):
        from pypdf import PdfReader, PdfWriter

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            sources = base / "sources"
            sources.mkdir()
            first = base / "first.pdf"
            last = base / "last.pdf"
            write_text_pdf(
                first, b"Native searchable text on the first two hundred pages"
            )
            write_text_pdf(
                last, b"Unique continuation target on original page two hundred one"
            )
            writer = PdfWriter()
            page = PdfReader(first).pages[0]
            for _ in range(200):
                writer.add_page(page)
            writer.add_page(PdfReader(last).pages[0])
            source = sources / "source.pdf"
            writer.write(source)
            original = source.read_bytes()
            registry = build_default_registry(base / "runtime")
            adapter = registry.resolve("pdf")
            service = CorpusService(base / "private", adapter_registry=registry)
            service.register(
                corpus_id="pages", source_root=sources, execution_policy="local_only"
            )
            service.sync("pages")
            inventory = service.inventory("pages")
            document = inventory["documents"][0]
            self.assertEqual(document["refresh_reasons"], ["extraction_continuation"])
            self.assertEqual(
                service.search("pages", "Unique continuation target")["count"], 0
            )
            projection = document["active_projection_id"]

            with mock.patch.object(
                adapter, "resume", side_effect=ExtractionError("temporary failure")
            ):
                service.sync("pages")
            self.assertEqual(
                service.inventory("pages")["documents"][0]["active_projection_id"],
                projection,
            )
            with corpus_read_connection(service.data_root, "pages") as connection:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM source_units").fetchone()[
                        0
                    ],
                    200,
                )
            # A current extraction failure is not retried forever by automatic refresh.
            with mock.patch.object(
                adapter, "resume", side_effect=AssertionError("must not retry")
            ):
                service.sync("pages")

            service.ingest("pages", document_ids=[document["document_id"]])
            current = service.inventory("pages")["documents"][0]
            self.assertEqual(current["index_state"], "current")
            self.assertEqual(current["projection_completeness"], "complete")
            hit = service.search("pages", "Unique continuation target")["candidates"][0]
            read = service.read_units("pages", [hit["unit_id"]])
            self.assertEqual(read["units"][0]["structure_path"]["page"], 201)
            with corpus_read_connection(service.data_root, "pages") as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM extraction_projections"
                    ).fetchone()[0],
                    1,
                )
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM source_units").fetchone()[
                        0
                    ],
                    201,
                )
            self.assertEqual(source.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
