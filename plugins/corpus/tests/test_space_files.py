from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from corpus.database import corpus_connection, workspace_connection
from corpus.errors import SpaceConflictError, SpaceValidationError
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
