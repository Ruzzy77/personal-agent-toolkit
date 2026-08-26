from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from corpus.database import utc_now, workspace_connection
from corpus.errors import ConfigurationError
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


if __name__ == "__main__":
    unittest.main()
