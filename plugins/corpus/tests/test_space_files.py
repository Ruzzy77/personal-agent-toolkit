from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from corpus.adapter_registry import build_default_registry
from corpus.adapters import AdapterDescriptor
from corpus.database import corpus_connection, workspace_connection
from corpus.errors import ExtractionError, SpaceConflictError, SpaceValidationError
from corpus.scanner import scan_corpus
from corpus.service import CorpusService
from corpus.spaces import decode_space_reference


class SpaceFileServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name).resolve()
        self.data = self.base / "private"
        self.service = CorpusService(self.data)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_office_structure_context_does_not_cross_slide_parts(self):
        root = self.base / "slides"
        root.mkdir()
        presentation = """<?xml version="1.0" encoding="UTF-8"?>
<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
 <p:sldIdLst>
  <p:sldId id="256" r:id="rId1"/>
  <p:sldId id="257" r:id="rId2"/>
 </p:sldIdLst>
</p:presentation>"""
        relationships = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/>
 <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide2.xml"/>
</Relationships>"""

        def slide_xml(label: str) -> str:
            return f"""<?xml version="1.0" encoding="UTF-8"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
 xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
 <p:cSld><p:spTree><p:graphicFrame><a:graphic><a:graphicData><a:tbl>
  <a:tblGrid><a:gridCol w="1"/><a:gridCol w="1"/></a:tblGrid>
  <a:tr h="1">
   <a:tc><a:txBody><a:p><a:r><a:t>{label} name</a:t></a:r></a:p></a:txBody></a:tc>
   <a:tc><a:txBody><a:p><a:r><a:t>{label} value</a:t></a:r></a:p></a:txBody></a:tc>
  </a:tr>
 </a:tbl></a:graphicData></a:graphic></p:graphicFrame></p:spTree></p:cSld>
</p:sld>"""

        with zipfile.ZipFile(root / "tables.pptx", "w") as archive:
            archive.writestr("ppt/presentation.xml", presentation)
            archive.writestr("ppt/_rels/presentation.xml.rels", relationships)
            archive.writestr("ppt/slides/slide1.xml", slide_xml("First"))
            archive.writestr("ppt/slides/slide2.xml", slide_xml("Second"))
        self.service.register(
            corpus_id="slides", source_root=root, execution_policy="local_only"
        )
        self.service.sync("slides")
        status = self.service.status("slides", include_warning_items=True)
        self.assertEqual(
            status["coverage_profiles"]["reading_order"]["unverified"],
            1,
        )
        self.assertEqual(status["coverage_gaps"]["partial_active_projections"], 0)
        reading_order = next(
            item
            for item in status["warning_items"]
            if item["issue_code"] == "reading_order_unverified"
        )
        self.assertEqual(reading_order["impact"], "reading_order_unverified")
        self.assertEqual(reading_order["coverage_dimensions"], ["reading_order"])
        hit = self.service.search("slides", "First value")["candidates"][0]
        context = self.service.read_units(
            "slides", [hit["unit_id"]], include_structure_context=True
        )
        self.assertEqual(
            {
                u["untrusted_content"]
                for u in context["units"]
                if u["untrusted_content"]
            },
            {"First name", "First value"},
        )

    def test_table_context_preserves_exact_hits_and_existing_read_default(self) -> None:
        root = self.base / "forms"
        root.mkdir()
        cells = []
        for row, values in enumerate((("항목", "금액"), ("장비", "100"))):
            for col, text in enumerate(values):
                cells.append(
                    f'<tc header="{int(row == 0)}"><cellAddr colAddr="{col}" rowAddr="{row}"/><cellSpan colSpan="1" rowSpan="1"/><subList><p><run><t>{text}</t></run></p></subList></tc>'
                )
        xml = (
            '<sec><p><run><tbl rowCnt="2" colCnt="2"><tr>'
            + "".join(cells)
            + "</tr></tbl></run></p><p><run><t>설명</t>"
            '<footNote number="1"><subList><p><run><t>각주본문</t></run></p>'
            "</subList></footNote></run></p></sec>"
        )
        with zipfile.ZipFile(root / "form.hwpx", "w") as archive:
            archive.writestr("Contents/section0.xml", xml)
        self.service.register(
            corpus_id="forms", source_root=root, execution_policy="local_only"
        )
        self.service.sync("forms")
        hits = self.service.search("forms", "100")
        self.assertEqual(hits["count"], 1)
        self.assertEqual(self.service.search("forms", "0")["count"], 0)
        unit_id = hits["candidates"][0]["unit_id"]
        exact = self.service.read_units("forms", [unit_id])
        self.assertEqual([u["untrusted_content"] for u in exact["units"]], ["100"])
        expanded = self.service.read_units(
            "forms", [unit_id], include_structure_context=True
        )
        self.assertEqual(
            {
                u["untrusted_content"]
                for u in expanded["units"]
                if u["untrusted_content"]
            },
            {"항목", "금액", "장비", "100"},
        )
        self.assertEqual(sum(u["requested"] for u in expanded["units"]), 1)
        self.assertTrue(
            all(u["dependency_state"] == "valid" for u in expanded["units"])
        )
        note = self.service.search("forms", "각주본문")["candidates"][0]
        note_context = self.service.read_units(
            "forms", [note["unit_id"]], include_structure_context=True
        )
        self.assertEqual(
            {
                u["untrusted_content"]
                for u in note_context["units"]
                if u["untrusted_content"]
            },
            {"설명", "각주본문"},
        )

        original_registry = self.service.adapter_registry
        original_adapter = original_registry.resolve("hwpx")

        class FailingAdapter:
            descriptor = AdapterDescriptor.from_config(
                adapter_id="test.structure-failure",
                adapter_version="1",
                config={},
                capabilities=original_adapter.descriptor.capabilities,
            )

            def extract(self, _path, *, format_id):
                raise ExtractionError("intentional structure extraction failure")

        self.service.adapter_registry = build_default_registry(
            overrides={"hwpx": FailingAdapter()}
        )
        self.service.ingest(
            "forms", document_ids=[hits["candidates"][0]["document_id"]]
        )
        with corpus_connection(self.data, "forms") as connection:
            active = connection.execute(
                "SELECT projection_id FROM extraction_projections WHERE is_active=1"
            ).fetchone()[0]
        self.assertEqual(active, hits["candidates"][0]["projection_id"])
        self.service.adapter_registry = original_registry
        self.assertEqual(self.service.search("forms", "100")["count"], 1)

    def _create_context(self, context_id: str, corpus_ids: list[str]) -> None:
        self.service.context_update(
            action="create",
            context_id=context_id,
            expected_version=0,
            payload={
                "title": context_id,
                "purpose": f"Reusable Context for {context_id}.",
                "scope": {"test": context_id},
                "corpus_ids": corpus_ids,
            },
        )

    def test_promoted_remote_connection_uses_one_space_file_surface(self) -> None:
        root = self.base / "research-note"
        root.mkdir()
        (root / "README.md").write_text(
            "Research note contains the adaptive relation marker.",
            encoding="utf-8",
        )
        (root / "drafts").mkdir()
        (root / "drafts" / "nested.md").write_text("nested", encoding="utf-8")
        (root / "alpha.txt").write_text("alpha", encoding="utf-8")
        (root / "beta.txt").write_text("beta", encoding="utf-8")
        self.service.register(
            corpus_id="relation-learning-research",
            source_root=root,
            execution_policy="external_host_allowed",
        )
        unindexed = self.service.space_get(
            space_id="relation-learning-research",
            audience="external_mcp",
        )["space"]
        self.assertEqual(unindexed["connections"][0]["source_state"], "needs_refresh")

        def incomplete_scan(*args, **kwargs):
            result = dict(scan_corpus(*args, **kwargs))
            result["observation_complete"] = False
            result["completeness_failure_count"] = 1
            return result

        with patch("corpus.service.scan_corpus", side_effect=incomplete_scan):
            synced = self.service.sync("relation-learning-research")
        self.assertEqual(synced["state"], "partial")
        self.assertGreater(synced["summary"]["indexed"], 0)
        self._create_context("research-note", ["relation-learning-research"])
        self.service.workspace_connect(
            workspace_id="research-note",
            context_id="research-note",
            display_name="Research Note",
            root=root,
            execution_policy="external_host_allowed",
        )
        with workspace_connection(self.data) as connection:
            connection.execute(
                "UPDATE workspaces SET root_device = root_device + 1 WHERE workspace_id = ?",
                ("research-note",),
            )
        with corpus_connection(self.data, "relation-learning-research") as connection:
            connection.execute("UPDATE documents SET device = device + 1")
        self.assertEqual(
            self.service.workspace_status(
                workspace_id="research-note",
                audience="external_mcp",
            )["work_folder"]["connection_state"],
            "connected",
        )
        ready = self.service.space_get(
            space_id="research-note",
            audience="external_mcp",
        )["space"]
        self.assertEqual(ready["connections"][0]["source_state"], "ready")

        search = self.service.space_search(
            space_id="research-note",
            query="adaptive relation marker",
            audience="external_mcp",
        )
        self.assertEqual(search["space_id"], "research-note")
        self.assertEqual(search["query_mode"], "exact_phrase_fts")
        self.assertEqual(search["count"], 1)
        candidate = search["candidates"][0]
        self.assertEqual(candidate["connection_id"], "main")
        self.assertTrue(candidate["read_ref"].startswith("read1."))
        decoded_ref = decode_space_reference("read", candidate["read_ref"])
        self.assertEqual(
            set(decoded_ref),
            {"space_id", "connection_id", "unit_id"},
        )
        self.assertNotIn("relation-learning-research", repr(decoded_ref))
        self.assertNotIn("corpus_id", repr(search))
        self.assertNotIn("document_id", repr(search))
        self.assertNotIn("unit_id", repr(search))
        self.assertNotIn("relation-learning-research", repr(search))
        self.assertNotIn(str(root), repr(search))

        fallback = self.service.space_search(
            space_id="research-note",
            query="marker adaptive relation",
            audience="external_mcp",
        )
        self.assertEqual(fallback["query_mode"], "all_terms_fts")
        self.assertEqual(fallback["count"], 1)

        indexed = self.service.space_file_read(
            space_id="Research Note",
            read_ref=candidate["read_ref"],
            audience="external_mcp",
        )
        self.assertEqual(indexed["source_kind"], "indexed_source")
        self.assertIn(
            "adaptive relation marker", indexed["units"][0]["untrusted_content"]
        )
        self.assertNotIn("relation-learning-research", repr(indexed))
        rescan = self.service.scan("relation-learning-research")
        self.assertEqual(rescan["change_counts"]["metadata_changed"], 0)

        first_page = self.service.space_file_list(
            space_id="research-note",
            mode="list_directory",
            limit=2,
            audience="external_mcp",
        )
        self.assertEqual(first_page["returned_count"], 2)
        self.assertTrue(first_page["has_more"])
        self.assertTrue(first_page["next_cursor"].startswith("cursor1."))
        self.assertNotIn(
            "drafts/nested.md",
            [item["relative_path"] for item in first_page["entries"]],
        )
        second_page = self.service.space_file_list(
            space_id="research-note",
            mode="list_directory",
            cursor=first_page["next_cursor"],
            limit=2,
            audience="external_mcp",
        )
        self.assertNotEqual(first_page["entries"], second_page["entries"])
        with self.assertRaises(SpaceConflictError):
            self.service.space_file_list(
                space_id="research-note",
                mode="find",
                query="draft",
                cursor=first_page["next_cursor"],
                audience="external_mcp",
            )

        work_folder = self.service.workspace_status(
            workspace_id="research-note",
            audience="external_mcp",
        )["work_folder"]
        with patch.object(
            self.service.workspaces,
            "files",
            return_value={
                "work_folder": work_folder,
                "relative_path": None,
                "path_contains": None,
                "offset": 10_000,
                "limit": 200,
                "returned_count": 200,
                "total_matching": None,
                "listing_truncated": True,
                "has_more": False,
                "next_offset": None,
                "entries": [],
                "skipped": {"symlinks": 0, "special": 0, "excluded": 0},
            },
        ):
            bounded_end = self.service.space_file_list(
                space_id="research-note",
                mode="list_directory",
                audience="external_mcp",
            )
        self.assertIsNone(bounded_end["has_more"])
        self.assertIsNone(bounded_end["next_cursor"])
        self.assertTrue(bounded_end["listing_truncated"])

        created = self.service.space_file_write(
            space_id="research-note",
            relative_path="working.md",
            content="first version",
            content_encoding="utf8",
            expected_version="absent",
            audience="external_mcp",
        )
        self.assertTrue(created["created"])
        self.assertIsNone(created["recovery_id"])
        self.assertIsNone(
            self.service.space_get(
                space_id="research-note",
                audience="external_mcp",
            )["space"]["current_file"]
        )
        live = self.service.space_file_read(
            space_id="research-note",
            relative_path="working.md",
            audience="external_mcp",
        )
        self.assertEqual(live["source_kind"], "live_file")
        self.assertEqual(live["content"], "first version")
        self.assertEqual(live["file"]["content_capability"], "text_inline")

        replaced = self.service.space_file_write(
            space_id="research-note",
            relative_path="working.md",
            content="second version",
            content_encoding="utf8",
            expected_version=live["file"]["version_token"],
            audience="external_mcp",
        )
        self.assertTrue(replaced["undo_available"])
        restored = self.service.space_file_restore(
            space_id="research-note",
            recovery_id=replaced["recovery_id"],
            expected_version=replaced["file"]["version_token"],
            audience="external_mcp",
        )
        self.assertTrue(restored["restored"])
        self.assertEqual(
            (root / "working.md").read_text(encoding="utf-8"), "first version"
        )

        live = self.service.space_file_read(
            space_id="research-note",
            relative_path="working.md",
            audience="external_mcp",
        )
        second = self.service.space_file_write(
            space_id="research-note",
            relative_path="working.md",
            content="second version",
            content_encoding="utf8",
            expected_version=live["file"]["version_token"],
            audience="external_mcp",
        )
        third = self.service.space_file_write(
            space_id="research-note",
            relative_path="working.md",
            content="third version",
            content_encoding="utf8",
            expected_version=second["file"]["version_token"],
            audience="external_mcp",
        )
        with workspace_connection(self.data) as connection:
            available = connection.execute(
                """
                SELECT COUNT(*) FROM workspace_recoveries
                WHERE workspace_id = ? AND relative_path = ? AND state = 'available'
                """,
                ("research-note", "working.md"),
            ).fetchone()[0]
        self.assertEqual(available, 1)
        self.service.space_file_restore(
            space_id="research-note",
            recovery_id=third["recovery_id"],
            expected_version=third["file"]["version_token"],
            audience="external_mcp",
        )
        live = self.service.space_file_read(
            space_id="research-note",
            relative_path="working.md",
            audience="external_mcp",
        )
        deleted = self.service.space_file_delete(
            space_id="research-note",
            relative_path="working.md",
            expected_version=live["file"]["version_token"],
            confirm_delete=True,
            audience="external_mcp",
        )
        self.assertTrue(deleted["deleted"])
        self.assertFalse((root / "working.md").exists())
        (root / "working.md").write_text("first version", encoding="utf-8")

        start_marker = "<!-- findings -->"
        end_marker = "<!-- discussion -->"
        manuscript = f"{start_marker}old{end_marker}" + "x" * 1_200
        (root / "thesis.md").write_text(manuscript, encoding="utf-8")
        partial = self.service.space_file_read(
            space_id="research-note",
            relative_path="thesis.md",
            max_chars=1_000,
            audience="external_mcp",
        )
        self.assertEqual(partial["next_start_char"], 1_000)
        self.assertNotIn("content_sha256", partial)
        self.service.space_file_write(
            space_id="research-note",
            relative_path="thesis.md",
            content="revised",
            content_encoding="utf8",
            expected_version=partial["file"]["version_token"],
            replace_start_marker=start_marker,
            replace_end_marker=end_marker,
            audience="external_mcp",
        )
        self.assertEqual(
            (root / "thesis.md").read_text(),
            f"{start_marker}revised{end_marker}" + "x" * 1_200,
        )

        space = self.service.space_get(
            space_id="research-note",
            audience="external_mcp",
        )["space"]
        generation = space["connections"][0]["generation"]
        selected = self.service.space_file_select_current(
            space_id="research-note",
            relative_path="working.md",
            audience="external_mcp",
        )
        self.assertEqual(selected["current_file"]["relative_path"], "working.md")
        self.assertEqual(selected["generation"], generation + 1)
        self.assertEqual(selected["connection"]["generation"], generation + 1)
        self.assertEqual(
            selected["connection"]["current_file"]["relative_path"],
            "working.md",
        )
        self.assertNotIn(str(root), repr(selected))

        self.service.scan("relation-learning-research")
        self.service.ingest("relation-learning-research")
        with corpus_connection(self.data, "relation-learning-research") as connection:
            self.assertEqual(
                {
                    row["name"]
                    for row in connection.execute(
                        """
                        SELECT name FROM sqlite_master
                        WHERE type = 'table' AND name IN ('events', 'snapshots')
                        """
                    )
                },
                set(),
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM extraction_projections WHERE is_active = 0"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM revisions
                    WHERE revision_id NOT IN (
                        SELECT current_revision_id FROM documents
                        WHERE current_revision_id IS NOT NULL
                    )
                    """
                ).fetchone()[0],
                0,
            )
        current_context = self.service.space_get(
            space_id="research-note",
            audience="external_mcp",
        )["space"]["context"]
        self.assertNotIn("status", current_context)
        self.assertNotIn("status_reason", current_context)

    def test_remote_context_and_work_do_not_open_local_source_connection(self) -> None:
        source = self.base / "private-materials"
        work = self.base / "hci-server"
        source.mkdir()
        work.mkdir()
        (source / "secret.md").write_text("private source marker", encoding="utf-8")
        (work / "draft.md").write_text("remote work", encoding="utf-8")
        self.service.register(
            corpus_id="hci-virtualization",
            source_root=source,
            execution_policy="local_only",
        )
        self.service.scan("hci-virtualization")
        self.service.ingest("hci-virtualization")
        self._create_context("hci-server", ["hci-virtualization"])
        self.service.workspace_connect(
            workspace_id="hci-server",
            context_id="hci-server",
            display_name="HCI Server",
            root=work,
            execution_policy="external_host_allowed",
        )

        with self.assertRaises(SpaceValidationError):
            self.service.space_search(
                space_id="hci-server",
                query="private source marker",
                audience="external_mcp",
            )
        remote_files = self.service.space_file_list(
            space_id="hci-server",
            mode="list_directory",
            audience="external_mcp",
        )
        self.assertEqual(remote_files["connection"]["connection_id"], "work")
        self.assertEqual(remote_files["entries"][0]["relative_path"], "draft.md")
        self.assertNotIn("hci-virtualization", repr(remote_files))
        self.assertNotIn(str(source), repr(remote_files))

        local_search = self.service.space_search(
            space_id="hci-server",
            connection_id="source",
            query="private source marker",
            audience="local_cli",
        )
        self.assertEqual(local_search["count"], 1)

        registered_after_connect = self.service.register(
            corpus_id="hci-work-source",
            source_root=work,
            execution_policy="external_host_allowed",
        )
        self.assertEqual(registered_after_connect["corpus_id"], "hci-work-source")

    def test_source_only_connection_uses_search_refs_and_indexed_file_find(
        self,
    ) -> None:
        root = self.base / "shared-source"
        root.mkdir()
        (root / "guide.md").write_text("shared indexed marker", encoding="utf-8")
        self.service.register(
            corpus_id="shared-source",
            source_root=root,
            execution_policy="external_host_allowed",
        )
        self.service.scan("shared-source")
        self.service.ingest("shared-source")

        found = self.service.space_file_list(
            space_id="shared-source",
            mode="find",
            query="guide",
            audience="external_mcp",
        )
        self.assertEqual(found["entries"][0]["relative_path"], "guide.md")
        self.assertEqual(found["entries"][0]["content_capability"], "indexed_text")
        with self.assertRaises(SpaceValidationError):
            self.service.space_file_list(
                space_id="shared-source",
                mode="list_directory",
                audience="external_mcp",
            )
        with self.assertRaises(SpaceValidationError):
            self.service.space_file_read(
                space_id="shared-source",
                relative_path="guide.md",
                audience="external_mcp",
            )
        candidate = self.service.space_search(
            space_id="shared-source",
            query="shared indexed marker",
            audience="external_mcp",
        )["candidates"][0]
        exact = self.service.space_file_read(
            space_id="shared-source",
            read_ref=candidate["read_ref"],
            audience="external_mcp",
        )
        self.assertIn("shared indexed marker", exact["units"][0]["untrusted_content"])


if __name__ == "__main__":
    unittest.main()
