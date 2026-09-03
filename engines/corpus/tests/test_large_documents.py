from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from corpus.config import RuntimePaths
from corpus.migrations import migrate_corpus_database
from corpus.service import CorpusService


class LargeDocumentApprovalTest(unittest.TestCase):
    def test_v4_database_migrates_coverage_and_approval_state_together(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source = base / "source"
            source.mkdir()
            (source / "sample.txt").write_text("indexed text", encoding="utf-8")
            service = CorpusService(base / "data")
            service.register(
                corpus_id="migration",
                source_root=source,
                execution_policy="local_only",
            )
            service.sync("migration")
            paths = RuntimePaths(data_root=base / "data", corpus_id="migration")
            with closing(sqlite3.connect(paths.corpus_db)) as connection, connection:
                unit_id = connection.execute(
                    "SELECT unit_id FROM source_units LIMIT 1"
                ).fetchone()[0]
                connection.execute(
                    "UPDATE source_units SET normalized_content = '' WHERE unit_id = ?",
                    (unit_id,),
                )
                connection.execute(
                    "DELETE FROM source_units_fts WHERE unit_id = ?",
                    (unit_id,),
                )
                connection.execute("DROP TABLE large_document_approvals")
                connection.execute(
                    "ALTER TABLE extraction_projections DROP COLUMN coverage_json"
                )
                connection.execute("UPDATE schema_info SET version = 4")
                connection.execute("PRAGMA user_version = 4")

            migrated = migrate_corpus_database(paths)

            self.assertTrue(migrated["migrated"])
            with closing(sqlite3.connect(paths.corpus_db)) as connection:
                projection_columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(extraction_projections)"
                    )
                }
                approval_table = connection.execute(
                    """
                    SELECT 1 FROM sqlite_master
                    WHERE type = 'table' AND name = 'large_document_approvals'
                    """
                ).fetchone()
            self.assertIn("coverage_json", projection_columns)
            self.assertIsNotNone(approval_table)

    def test_approval_is_bound_to_the_observed_source_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source = base / "source"
            source.mkdir()
            document = source / "large.txt"
            with document.open("wb") as stream:
                stream.truncate(251 * 1024 * 1024)

            service = CorpusService(base / "data")
            service.register(
                corpus_id="large-files",
                source_root=source,
                execution_policy="local_only",
            )
            service.scan("large-files")
            document_id = service.inventory("large-files")["documents"][0][
                "document_id"
            ]

            approved = service.approve_large_document(
                "large-files",
                document_id=document_id,
            )
            self.assertEqual(approved["state"], "approved")
            self.assertEqual(
                service.list_large_document_approvals("large-files")["approvals"][0][
                    "state"
                ],
                "approved",
            )

            with document.open("ab") as stream:
                stream.truncate(252 * 1024 * 1024)
            service.scan("large-files")

            self.assertEqual(
                service.list_large_document_approvals("large-files")["approvals"][0][
                    "state"
                ],
                "source_changed",
            )


if __name__ == "__main__":
    unittest.main()
