from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from corpus.database import context_connection, utc_now, workspace_connection
from corpus.errors import ConfigurationError, ContextNotFoundError
from corpus.service import CorpusService


class CorpusUnregisterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name).resolve()
        self.data = self.base / "private"
        self.source = self.base / "source"
        self.source.mkdir()
        self.service = CorpusService(self.data)
        self.service.register(
            corpus_id="stale-source",
            source_root=self.source,
            execution_policy="local_only",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_unregister_requires_confirmation_and_matching_root(self) -> None:
        with self.assertRaises(ConfigurationError) as confirmation:
            self.service.unregister(
                corpus_id="stale-source",
                expected_source_root=self.source,
                confirm_unregister=False,
            )
        self.assertEqual(
            confirmation.exception.details["reason"], "confirmation_required"
        )

        with self.assertRaises(ConfigurationError):
            self.service.unregister(
                corpus_id="stale-source",
                expected_source_root=self.base / "different-source",
                confirm_unregister=True,
            )

        result = self.service.unregister(
            corpus_id="stale-source",
            expected_source_root=self.source,
            confirm_unregister=True,
        )

        self.assertTrue(result["changed"])
        self.assertTrue(result["private_index_retained"])
        self.assertEqual(self.service.corpora(), [])
        self.assertTrue(Path(result["private_index_root"]).is_dir())

    def test_unregister_stops_for_context_or_work_folder_connections(self) -> None:
        self.service.context_update(
            action="create",
            context_id="linked-context",
            expected_version=0,
            payload={
                "title": "Linked Context",
                "purpose": "Keep this source connected.",
                "scope": {"topic": "test"},
                "corpus_ids": ["stale-source"],
            },
        )
        with self.assertRaises(ConfigurationError) as context_reference:
            self.service.unregister(
                corpus_id="stale-source",
                expected_source_root=self.source,
                confirm_unregister=True,
            )
        self.assertEqual(
            context_reference.exception.details["reason"], "context_references"
        )

        second_source = self.base / "work-source"
        second_source.mkdir()
        self.service.register(
            corpus_id="work-source",
            source_root=second_source,
            execution_policy="local_only",
        )
        observed = second_source.stat()
        now = utc_now()
        with workspace_connection(self.data) as connection:
            connection.execute(
                """
                INSERT INTO workspaces(
                    workspace_id, context_id, display_name, root_path,
                    root_path_nfc, root_device, root_inode, execution_policy,
                    current_relative_path, generation, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, 1, ?, ?)
                """,
                (
                    "linked-work",
                    "linked-work-context",
                    "Linked Work",
                    str(second_source),
                    str(second_source),
                    observed.st_dev,
                    observed.st_ino,
                    "local_only",
                    now,
                    now,
                ),
            )
        with self.assertRaises(ConfigurationError) as workspace_reference:
            self.service.unregister(
                corpus_id="work-source",
                expected_source_root=second_source,
                confirm_unregister=True,
            )
        self.assertEqual(
            workspace_reference.exception.details["reason"],
            "workspace_connection",
        )

    def test_unregister_can_remove_one_expected_archived_context_and_history(
        self,
    ) -> None:
        self.service.corpus_source_update(
            action="bind",
            corpus_id="stale-source",
            binding_id="stale-codex",
            payload={
                "provider_kind": "codex",
                "selector": {
                    "cwd_prefix": "/workspace/project",
                    "actor": "all",
                    "lookback_days": 30,
                    "include_archived": False,
                },
            },
        )
        self.service.corpus_source_update(
            action="observe",
            corpus_id="stale-source",
            binding_id="stale-codex",
            payload={
                "run_id": "retired-run",
                "records": [
                    {
                        "external_id": "retired-turn",
                        "parent_external_id": "retired-session",
                        "occurred_at": "2026-08-20T00:00:00Z",
                        "provider_metadata": {
                            "session_id": "retired-session",
                            "turn_id": "retired-turn",
                            "cwd": "/workspace/project",
                            "workspace": "/workspace/project",
                            "actor": "user_task",
                            "task_kind": "codex_turn",
                        },
                        "locator": {
                            "root_ref": "active",
                            "relative_path": "2026/08/session.jsonl",
                            "session_id": "retired-session",
                            "turn_id": "retired-turn",
                        },
                        "freshness_identity": "sha256:" + ("a" * 64),
                    }
                ],
                "complete": True,
            },
        )
        self.service.context_update(
            action="create",
            context_id="retired-context",
            expected_version=0,
            payload={
                "title": "Retired Context",
                "purpose": "Retain one relationship until it moves elsewhere.",
                "scope": {"topic": "retired"},
                "corpus_ids": ["stale-source"],
            },
        )
        with context_connection(self.data) as connection:
            record = connection.execute(
                """
                SELECT source_record_id, metadata_sha256
                FROM external_source_records
                WHERE binding_id = ? AND external_id = ?
                """,
                ("stale-codex", "retired-turn"),
            ).fetchone()
            connection.execute(
                """
                INSERT INTO context_items(
                    item_id, context_id, client_ref, input_sha256, kind,
                    body_text, attributes_json, disclosure_state,
                    lifecycle_state, supersedes_item_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
                """,
                (
                    "retired-item",
                    "retired-context",
                    "retired-relationship",
                    "a" * 64,
                    "relationship",
                    "This relationship has moved to its durable home.",
                    "{}",
                    "restricted",
                    "active",
                    utc_now(),
                ),
            )
            connection.execute(
                """
                INSERT INTO context_external_sources(
                    source_ref_id, item_id, corpus_id, binding_id,
                    source_record_id, link_role, observed_metadata_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "retired-source-link",
                    "retired-item",
                    "stale-source",
                    "stale-codex",
                    record["source_record_id"],
                    "direct",
                    record["metadata_sha256"],
                ),
            )
        self.service.context_update(
            action="archive",
            context_id="retired-context",
            expected_version=1,
            payload={},
        )

        with self.assertRaises(ConfigurationError) as wrong_version:
            self.service.unregister(
                corpus_id="stale-source",
                expected_source_root=self.source,
                confirm_unregister=True,
                archived_context_id="retired-context",
                expected_context_version=1,
                confirm_remove_linked_history=True,
            )
        self.assertEqual(
            wrong_version.exception.details["reason"],
            "archived_context_mismatch",
        )

        result = self.service.unregister(
            corpus_id="stale-source",
            expected_source_root=self.source,
            confirm_unregister=True,
            archived_context_id="retired-context",
            expected_context_version=2,
            confirm_remove_linked_history=True,
        )

        removed = result["context_cleanup"]["removed"]
        self.assertEqual(removed["contexts"], 1)
        self.assertEqual(removed["context_items"], 1)
        self.assertEqual(removed["context_external_source_links"], 1)
        self.assertEqual(removed["source_bindings"], 1)
        self.assertEqual(removed["source_runs"], 1)
        self.assertEqual(removed["source_records"], 1)
        self.assertEqual(len(result["backups"]), 2)
        self.assertTrue(all(Path(path).is_file() for path in result["backups"]))
        self.assertTrue(result["private_index_retained"])
        self.assertEqual(self.service.corpora(), [])
        with self.assertRaises(ContextNotFoundError):
            self.service.context_read(
                context_id="retired-context",
                state="archived",
            )


if __name__ == "__main__":
    unittest.main()
