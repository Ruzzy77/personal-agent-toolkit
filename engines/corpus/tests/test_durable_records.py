from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
import unicodedata
import unittest
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from corpus.adapter_registry import build_default_registry
from corpus.adapters import AdapterDescriptor
from corpus.database import (
    context_connection,
    corpus_connection,
    ensure_catalog,
    migrate_context_database,
)
from corpus.errors import ExtractionError
from corpus.maintenance import _publish_maintenance_state
from corpus.service import CorpusService


class DurableRecordTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name).resolve()
        self.data = self.base / "private"
        self.root = self.base / "source"
        self.root.mkdir()
        self.service = CorpusService(self.data)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _register_text(self, text: str = "durable marker") -> tuple[Path, dict]:
        document = self.root / "note.txt"
        document.write_text(text, encoding="utf-8")
        self.service.register(
            corpus_id="durable",
            source_root=self.root,
            execution_policy="local_only",
        )
        self.service.sync("durable")
        hit = self.service.search("durable", text)["candidates"][0]
        return document, hit

    def test_deleted_source_keeps_last_successful_record_searchable(self) -> None:
        document, hit = self._register_text()
        document.unlink()

        self.service.scan("durable")

        result = self.service.search("durable", "durable marker")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["candidates"][0]["source_state"], "unavailable")
        read = self.service.read_units("durable", [hit["unit_id"]])
        self.assertEqual(read["units"][0]["untrusted_content"], "durable marker")
        self.assertEqual(read["units"][0]["dependency_state"], "source_unavailable")
        self.assertNotIn("absolute_path", read["units"][0]["source_anchor"])
        self.assertNotIn("surface_open_target", read["units"][0]["source_anchor"])
        self.assertEqual(
            self.service.status("durable")["record_retention"]["detached_records"],
            1,
        )

    def test_rename_preserves_document_and_projection_identity(self) -> None:
        document, hit = self._register_text("rename marker")
        renamed = self.root / "renamed.txt"
        document.rename(renamed)

        synced = self.service.sync("durable")
        inventory = self.service.inventory("durable")
        current = inventory["documents"][0]

        self.assertEqual(current["document_id"], hit["document_id"])
        self.assertEqual(current["current_revision_id"], hit["revision_id"])
        self.assertEqual(current["relative_path"], "renamed.txt")
        self.assertGreaterEqual(synced["summary"]["reused"], 1)
        self.assertEqual(
            self.service.search("durable", "rename marker")["candidates"][0][
                "relative_path"
            ],
            "renamed.txt",
        )

    @unittest.skipUnless(sys.platform == "darwin", "uses macOS file identities")
    def test_root_move_is_resolved_without_manual_rebind(self) -> None:
        _document, hit = self._register_text("root move marker")
        moved_root = self.base / "renamed-source"
        self.root.rename(moved_root)

        scan = self.service.scan("durable")

        self.assertEqual(scan["location_resolution"], "filesystem_identity")
        registered = self.service.corpora()[0]
        self.assertEqual(Path(registered["source_root"]), moved_root.resolve())
        self.assertEqual(
            self.service.search("durable", "root move marker")["candidates"][0][
                "document_id"
            ],
            hit["document_id"],
        )

    def test_registered_path_refreshes_a_changed_volume_device_number(self) -> None:
        self._register_text("device refresh marker")
        observed = self.root.stat()
        catalog = ensure_catalog(self.data)
        with closing(sqlite3.connect(catalog)) as connection, connection:
            connection.execute(
                "UPDATE corpora SET root_device = ? WHERE corpus_id = 'durable'",
                (observed.st_dev + 1,),
            )

        scan = self.service.scan("durable")
        registered = self.service.corpora()[0]

        self.assertEqual(
            scan["location_resolution"],
            "registered_path_identity_refreshed",
        )
        self.assertEqual(registered["root_device"], observed.st_dev)
        self.assertEqual(registered["root_inode"], observed.st_ino)

    def test_failed_changed_extraction_does_not_replace_last_good_record(self) -> None:
        document, hit = self._register_text("last good marker")
        original_registry = self.service.adapter_registry
        original_adapter = original_registry.resolve("txt")

        class FailingAdapter:
            descriptor = AdapterDescriptor.from_config(
                adapter_id="test.text-failure",
                adapter_version="1",
                config={},
                capabilities=original_adapter.descriptor.capabilities,
            )

            def extract(self, _path, *, format_id):
                raise ExtractionError("intentional extraction failure")

        document.write_text("replacement content", encoding="utf-8")
        self.service.adapter_registry = build_default_registry(
            overrides={"txt": FailingAdapter()}
        )
        synced = self.service.sync("durable")

        self.assertGreaterEqual(synced["refresh"]["state"].count("failures"), 1)
        result = self.service.search("durable", "last good marker")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["candidates"][0]["revision_id"], hit["revision_id"])
        self.assertEqual(result["candidates"][0]["source_state"], "changed")

    def test_retention_archives_trashes_and_purges_only_unprotected_records(
        self,
    ) -> None:
        document, hit = self._register_text("retention marker")
        document.unlink()
        self.service.scan("durable")
        with corpus_connection(self.data, "durable") as connection:
            deleted_at = datetime.fromisoformat(
                connection.execute(
                    "SELECT deleted_at FROM documents WHERE document_id = ?",
                    (hit["document_id"],),
                ).fetchone()[0]
            )
            connection.execute(
                "UPDATE documents SET last_user_access_at = NULL WHERE document_id = ?",
                (hit["document_id"],),
            )

        archived = self.service.maintain_retention(
            "durable", now=deleted_at + timedelta(days=31)
        )
        self.assertEqual(archived["actions"][0]["action"], "archive")
        self.assertEqual(self.service.search("durable", "retention marker")["count"], 0)
        self.assertEqual(
            self.service.read_units("durable", [hit["unit_id"]])["count"],
            1,
        )
        with corpus_connection(self.data, "durable") as connection:
            connection.execute(
                "UPDATE documents SET last_user_access_at = NULL WHERE document_id = ?",
                (hit["document_id"],),
            )
        trashed = self.service.maintain_retention(
            "durable", now=deleted_at + timedelta(days=212)
        )
        self.assertEqual(trashed["actions"][0]["action"], "trash")
        purged = self.service.maintain_retention(
            "durable", now=deleted_at + timedelta(days=243)
        )
        self.assertEqual(purged["actions"][0]["action"], "purge")
        self.assertEqual(purged["purged"]["documents"], 1)

    def test_context_link_and_explicit_protection_block_automatic_cleanup(self) -> None:
        document, hit = self._register_text("protected marker")
        self.service.context_update(
            action="create",
            context_id="protected-context",
            expected_version=0,
            payload={
                "title": "Protected",
                "purpose": "Protect linked source provenance.",
                "scope": {},
                "corpus_ids": ["durable"],
            },
        )
        now = datetime.now(UTC).isoformat()
        with context_connection(self.data) as connection:
            connection.execute(
                """
                INSERT INTO context_items(
                    item_id, context_id, client_ref, input_sha256, kind,
                    body_text, attributes_json, disclosure_state,
                    lifecycle_state, supersedes_item_id, created_at
                ) VALUES (?, ?, ?, ?, 'finding', ?, '{}', 'restricted',
                          'active', NULL, ?)
                """,
                (
                    "item_protected",
                    "protected-context",
                    "protected",
                    hashlib.sha256(b"protected").hexdigest(),
                    "Protected finding.",
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO context_sources(
                    source_ref_id, item_id, corpus_id, document_id,
                    revision_id, projection_id, source_unit_id,
                    link_role, source_span_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'direct', '{}')
                """,
                (
                    "source_protected",
                    "item_protected",
                    "durable",
                    hit["document_id"],
                    hit["revision_id"],
                    hit["projection_id"],
                    hit["unit_id"],
                ),
            )
        document.unlink()
        self.service.scan("durable")
        far_future = datetime.now(UTC) + timedelta(days=1_000)

        retained = self.service.maintain_retention("durable", now=far_future)

        self.assertEqual(retained["actions"], [])
        self.assertEqual(self.service.search("durable", "protected marker")["count"], 1)

    def test_context_link_keeps_older_extracted_revision_after_source_update(
        self,
    ) -> None:
        document, hit = self._register_text("context revision one")
        self.service.context_update(
            action="create",
            context_id="revision-context",
            expected_version=0,
            payload={
                "title": "Revision context",
                "purpose": "Retain the cited extracted revision.",
                "scope": {},
                "corpus_ids": ["durable"],
            },
        )
        now = datetime.now(UTC).isoformat()
        with context_connection(self.data) as connection:
            connection.execute(
                """
                INSERT INTO context_items(
                    item_id, context_id, client_ref, input_sha256, kind,
                    body_text, attributes_json, disclosure_state,
                    lifecycle_state, supersedes_item_id, created_at
                ) VALUES (?, ?, ?, ?, 'finding', ?, '{}', 'restricted',
                          'active', NULL, ?)
                """,
                (
                    "item_revision",
                    "revision-context",
                    "revision",
                    hashlib.sha256(b"revision").hexdigest(),
                    "Finding tied to the first revision.",
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO context_sources(
                    source_ref_id, item_id, corpus_id, document_id,
                    revision_id, projection_id, source_unit_id,
                    link_role, source_span_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'direct', '{}')
                """,
                (
                    "source_revision",
                    "item_revision",
                    "durable",
                    hit["document_id"],
                    hit["revision_id"],
                    hit["projection_id"],
                    hit["unit_id"],
                ),
            )

        document.write_text("context revision two", encoding="utf-8")
        self.service.sync("durable")

        older = self.service.read_units("durable", [hit["unit_id"]])
        self.assertEqual(older["count"], 1)
        self.assertEqual(older["units"][0]["untrusted_content"], "context revision one")
        self.assertEqual(
            older["units"][0]["dependency_state"],
            "stale_source_revision",
        )
        with corpus_connection(self.data, "durable") as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM revisions").fetchone()[0],
                2,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM extraction_projections"
                ).fetchone()[0],
                2,
            )

    def test_pruning_removes_an_unlinked_projection_from_a_linked_revision(
        self,
    ) -> None:
        _document, hit = self._register_text("projection replacement")
        self.service.context_update(
            action="create",
            context_id="projection-context",
            expected_version=0,
            payload={
                "title": "Projection context",
                "purpose": "Retain only the currently cited projection.",
                "scope": {},
                "corpus_ids": ["durable"],
            },
        )
        now = datetime.now(UTC).isoformat()
        with context_connection(self.data) as connection:
            connection.execute(
                """
                INSERT INTO context_items(
                    item_id, context_id, client_ref, input_sha256, kind,
                    body_text, attributes_json, disclosure_state,
                    lifecycle_state, supersedes_item_id, created_at
                ) VALUES (?, ?, ?, ?, 'finding', ?, '{}', 'restricted',
                          'active', NULL, ?)
                """,
                (
                    "item_projection",
                    "projection-context",
                    "projection",
                    hashlib.sha256(b"projection").hexdigest(),
                    "Finding tied to the selected projection.",
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO context_sources(
                    source_ref_id, item_id, corpus_id, document_id,
                    revision_id, projection_id, source_unit_id,
                    link_role, source_span_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'direct', '{}')
                """,
                (
                    "source_projection",
                    "item_projection",
                    "durable",
                    hit["document_id"],
                    hit["revision_id"],
                    hit["projection_id"],
                    hit["unit_id"],
                ),
            )

        original_adapter = self.service.adapter_registry.resolve("txt")

        class ReplacementAdapter:
            descriptor = AdapterDescriptor.from_config(
                adapter_id="test.text-replacement",
                adapter_version="2",
                config={},
                capabilities=original_adapter.descriptor.capabilities,
            )

            def extract(self, path, *, format_id):
                return original_adapter.extract(path, format_id=format_id)

        self.service.adapter_registry = build_default_registry(
            overrides={"txt": ReplacementAdapter()}
        )
        self.service.sync("durable")
        with corpus_connection(self.data, "durable") as connection:
            active = connection.execute(
                """
                SELECT p.projection_id, u.unit_id
                FROM extraction_projections p
                JOIN source_units u ON u.projection_id = p.projection_id
                WHERE p.revision_id = ? AND p.is_active = 1
                ORDER BY u.ordinal
                LIMIT 1
                """,
                (hit["revision_id"],),
            ).fetchone()
        with context_connection(self.data) as connection:
            connection.execute(
                """
                UPDATE context_sources
                SET projection_id = ?, source_unit_id = ?
                WHERE source_ref_id = 'source_projection'
                """,
                (active["projection_id"], active["unit_id"]),
            )

        self.service.scan("durable")

        with corpus_connection(self.data, "durable") as connection:
            projections = connection.execute(
                "SELECT projection_id FROM extraction_projections"
            ).fetchall()
            attempts = connection.execute(
                "SELECT projection_id FROM extraction_attempts"
            ).fetchall()
        self.assertEqual(
            [row["projection_id"] for row in projections],
            [active["projection_id"]],
        )
        self.assertTrue(attempts)
        self.assertEqual(
            {row["projection_id"] for row in attempts},
            {active["projection_id"]},
        )

    def test_public_paths_use_nfc_without_renaming_the_source_file(self) -> None:
        nfd_name = unicodedata.normalize("NFD", "한글.txt")
        path = self.root / nfd_name
        path.write_text("nfc marker", encoding="utf-8")
        self.service.register(
            corpus_id="durable",
            source_root=self.root,
            execution_policy="local_only",
        )
        self.service.sync("durable")

        expected = unicodedata.normalize("NFC", nfd_name)
        self.assertEqual(
            self.service.inventory("durable")["documents"][0]["relative_path"],
            expected,
        )
        self.assertEqual(
            self.service.search("durable", "nfc marker")["candidates"][0][
                "relative_path"
            ],
            expected,
        )
        self.assertTrue(path.exists())

    def test_change_queue_is_coalesced_and_cleared_after_successful_refresh(
        self,
    ) -> None:
        document, _hit = self._register_text("old queue marker")
        document.write_text("new queue marker", encoding="utf-8")
        self.service.enqueue_source_changes(
            "durable",
            ["note.txt", "note.txt"],
            event_kind="changed",
        )

        processed = self.service.process_source_change_queue("durable")

        self.assertEqual(processed["state"], "complete")
        self.assertEqual(
            self.service.source_change_queue_status("durable")["pending_change_count"],
            0,
        )
        self.assertEqual(self.service.search("durable", "new queue marker")["count"], 1)
        self.assertEqual(self.service.search("durable", "old queue marker")["count"], 0)

    def test_maintenance_replaces_one_compact_state_snapshot(self) -> None:
        self._register_text("maintenance state marker")
        first = {
            "state": "pending",
            "count": 1,
            "corpora": [
                {
                    "corpus_id": "durable",
                    "state": "pending",
                    "pending_change_count": 1,
                    "sync": {
                        "state": "pending",
                        "summary": {"remaining": 1},
                        "source_state": "available",
                        "change_queue": {"pending": 1},
                        "retention": {
                            "actions": [
                                {
                                    "document_id": "doc_unused",
                                    "relative_path": "large/history/path.txt",
                                    "action": "archive",
                                }
                            ],
                            "action_count": 1,
                            "limit_reached": False,
                            "purged": {"documents": 0},
                            "lifecycle_counts": {"active": 1},
                        },
                    },
                }
            ],
        }
        _publish_maintenance_state(self.service, first)
        state_path = self.data / "maintenance-state.json"
        first_size = state_path.stat().st_size
        second = {
            "state": "complete",
            "count": 1,
            "corpora": [
                {
                    "corpus_id": "durable",
                    "state": "complete",
                    "pending_change_count": 0,
                }
            ],
        }

        _publish_maintenance_state(self.service, second)

        payload = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["result"]["state"], "complete")
        self.assertNotIn("actions", json.dumps(payload))
        self.assertLess(state_path.stat().st_size, first_size)
        self.assertEqual(state_path.stat().st_mode & 0o777, 0o600)

    def test_v1_catalog_migrates_to_stable_location_identity(self) -> None:
        legacy_data = self.base / "legacy-data"
        legacy_data.mkdir(mode=0o700)
        legacy_source = self.base / "legacy-source"
        legacy_source.mkdir()
        catalog = legacy_data / "catalog.sqlite"
        with closing(sqlite3.connect(catalog)) as connection, connection:
            connection.executescript(
                """
                CREATE TABLE schema_info(version INTEGER NOT NULL);
                INSERT INTO schema_info(version) VALUES (1);
                CREATE TABLE corpora (
                    corpus_id TEXT PRIMARY KEY,
                    source_root TEXT NOT NULL UNIQUE,
                    source_root_nfc TEXT NOT NULL,
                    execution_policy TEXT NOT NULL,
                    provider_kind TEXT NOT NULL,
                    source_scope_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            connection.execute(
                """
                INSERT INTO corpora VALUES (?, ?, ?, 'local_only', 'filesystem',
                    '{"exclude_directory_names":[],"exclude_path_prefixes":[]}',
                    '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')
                """,
                ("legacy", str(legacy_source), str(legacy_source)),
            )
            connection.execute("PRAGMA user_version = 1")
        catalog.chmod(0o600)

        ensure_catalog(legacy_data)

        with closing(sqlite3.connect(catalog)) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute("SELECT * FROM corpora").fetchone()
            version = connection.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(version, 2)
        self.assertTrue(row["location_id"].startswith("loc_"))
        self.assertEqual(row["location_state"], "available")
        self.assertEqual(
            (row["root_device"], row["root_inode"]),
            (
                legacy_source.stat().st_dev,
                legacy_source.stat().st_ino,
            ),
        )

    def test_catalog_migration_rolls_back_all_schema_changes_on_failure(self) -> None:
        legacy_data = self.base / "rollback-data"
        legacy_data.mkdir(mode=0o700)
        first_source = self.base / "rollback-source-one"
        second_source = self.base / "rollback-source-two"
        first_source.mkdir()
        second_source.mkdir()
        catalog = legacy_data / "catalog.sqlite"
        with closing(sqlite3.connect(catalog)) as connection, connection:
            connection.executescript(
                """
                CREATE TABLE schema_info(version INTEGER NOT NULL);
                INSERT INTO schema_info(version) VALUES (1);
                CREATE TABLE corpora (
                    corpus_id TEXT PRIMARY KEY,
                    source_root TEXT NOT NULL UNIQUE,
                    source_root_nfc TEXT NOT NULL,
                    execution_policy TEXT NOT NULL,
                    provider_kind TEXT NOT NULL,
                    source_scope_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            rows = [
                (
                    "first",
                    str(first_source),
                    "duplicate-normalized-root",
                ),
                (
                    "second",
                    str(second_source),
                    "duplicate-normalized-root",
                ),
            ]
            connection.executemany(
                """
                INSERT INTO corpora VALUES (
                    ?, ?, ?, 'local_only', 'filesystem',
                    '{"exclude_directory_names":[],"exclude_path_prefixes":[]}',
                    '2026-01-01T00:00:00+00:00',
                    '2026-01-01T00:00:00+00:00'
                )
                """,
                rows,
            )
            connection.execute("PRAGMA user_version = 1")
        catalog.chmod(0o600)

        with self.assertRaises(sqlite3.IntegrityError):
            ensure_catalog(legacy_data)

        with closing(sqlite3.connect(catalog)) as connection:
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(corpora)")
            }
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            schema_version = connection.execute(
                "SELECT version FROM schema_info"
            ).fetchone()[0]
        self.assertNotIn("location_id", columns)
        self.assertNotIn("root_device", columns)
        self.assertEqual(version, 1)
        self.assertEqual(schema_version, 1)

    def test_context_v5_migration_removes_historical_workspace_paths(self) -> None:
        self.root.joinpath("note.txt").write_text("context migration", encoding="utf-8")
        self.service.register(
            corpus_id="durable",
            source_root=self.root,
            execution_policy="local_only",
        )
        binding = self.service.corpus_source_update(
            action="bind",
            corpus_id="durable",
            binding_id="durable-codex",
            payload={
                "provider_kind": "codex",
                "selector": {
                    "cwd_prefix": str(self.root),
                    "actor": "all",
                    "lookback_days": 30,
                },
            },
        )
        self.service.corpus_source_update(
            action="observe",
            corpus_id="durable",
            binding_id=binding["binding_id"],
            payload={
                "run_id": "run_legacy_paths",
                "complete": True,
                "records": [
                    {
                        "external_id": "turn_legacy",
                        "parent_external_id": "session_legacy",
                        "occurred_at": "2026-01-01T00:00:00+00:00",
                        "provider_metadata": {
                            "session_id": "session_legacy",
                            "turn_id": "turn_legacy_id",
                            "cwd": "/old/workspace",
                            "workspace": "/old/workspace",
                            "actor": "user_task",
                            "task_kind": "codex_turn",
                        },
                        "locator": {
                            "root_ref": "codex_sessions",
                            "relative_path": "legacy.jsonl",
                            "session_id": "session_legacy",
                            "turn_id": "turn_legacy_id",
                        },
                        "freshness_identity": "sha256:" + "0" * 64,
                    }
                ],
            },
        )
        context_database = self.data / "contexts.sqlite3"
        with closing(sqlite3.connect(context_database)) as connection, connection:
            connection.execute(
                "ALTER TABLE context_corpora ADD COLUMN last_checked_snapshot_id TEXT"
            )
            connection.execute(
                """
                ALTER TABLE context_sources
                ADD COLUMN snapshot_id TEXT NOT NULL DEFAULT ''
                """
            )
            metadata = json.loads(
                connection.execute(
                    "SELECT provider_metadata_json FROM external_source_records"
                ).fetchone()[0]
            )
            metadata["cwd"] = "/old/workspace"
            metadata["workspace"] = "/old/workspace"
            connection.execute(
                "UPDATE external_source_records SET provider_metadata_json = ?",
                (json.dumps(metadata),),
            )
            connection.execute("UPDATE schema_info SET version = 5")
            connection.execute("PRAGMA user_version = 5")

        migrate_context_database(self.data)

        with closing(sqlite3.connect(context_database)) as connection:
            metadata = json.loads(
                connection.execute(
                    "SELECT provider_metadata_json FROM external_source_records"
                ).fetchone()[0]
            )
            context_corpora_columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(context_corpora)")
            }
            context_source_columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(context_sources)")
            }
        self.assertNotIn("cwd", metadata)
        self.assertNotIn("workspace", metadata)
        self.assertNotIn("last_checked_snapshot_id", context_corpora_columns)
        self.assertNotIn("snapshot_id", context_source_columns)


if __name__ == "__main__":
    unittest.main()
