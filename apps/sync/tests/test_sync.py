from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Self

import personal_agent_sync.analysis as analysis_module
import personal_agent_sync.daemon as daemon_module
import personal_agent_sync.migration as migration_module
import personal_agent_sync.state as state_module
import personal_agent_sync.storage as storage_module
import personal_agent_sync.work as work_module
import pytest
from personal_agent_sync.analysis import build_projection, select_analyzer
from personal_agent_sync.config import load_config
from personal_agent_sync.daemon import SyncDaemon
from personal_agent_sync.errors import PolicyDenied, SyncError
from personal_agent_sync.paths import Snapshot, capture_snapshot, resolve_moved_root
from personal_agent_sync.reconcile import reconcile_all
from personal_agent_sync.state import SyncState
from personal_agent_sync.storage import maintain_remote_storage, remote_storage_report
from personal_agent_sync.work import SYNC_OPERATIONS, WORK_OPERATIONS, WorkExecutor


def write_config(tmp_path: Path, root: Path, *, route: str = "local") -> Path:
    data = tmp_path / "private"
    config = tmp_path / "config.toml"
    config.write_text(
        "\n".join(
            [
                'service_url = "https://context.example.test"',
                'device_id = "test-mac"',
                'display_name = "Test Mac"',
                f"data_root = {json.dumps(str(data))}",
                f"corpus_python = {json.dumps(sys.executable)}",
                f"document_files_python = {json.dumps(sys.executable)}",
                "reconcile_seconds = 2",
                "[[connections]]",
                'space_id = "notes"',
                'connection_id = "main"',
                f"root = {json.dumps(str(root))}",
                'roles = ["source", "work"]',
                'access_scope = "remote_allowed"',
                'permission = "read_write"',
                'corpus_id = "notes"',
                f'analyzer_route = "{route}"',
                "max_transfer_bytes = 1048576",
                "generation = 3",
            ]
        ),
        encoding="utf-8",
    )
    return config


def test_migration_document_verification_ignores_live_modified_time() -> None:
    document = {
        "documentId": "doc_1",
        "relativePath": "notes/한글.txt",
        "extension": ".txt",
        "sourceState": "available",
        "mediaType": "text/plain",
        "logicalSize": 7,
        "modifiedNs": "100",
        "residencyState": "resident",
        "eligibilityState": "supported",
        "currentRevisionId": "rev_1",
        "firstSeenAt": "2026-09-01T00:00:00Z",
        "deletedAt": None,
        "lifecycleState": "active",
        "retentionClass": "managed",
    }
    expected = migration_module._document_verification_record(document)
    actual = dict(expected, modified_ns="200")

    assert "modified_ns" not in expected
    assert migration_module._first_record_mismatch(actual, expected) is None


def test_migration_record_verification_reports_durable_field() -> None:
    expected = {
        "document_id": "doc_1",
        "relative_path": "notes/한글.txt",
        "current_revision_id": "rev_1",
    }
    actual = dict(expected, relative_path="notes/moved.txt")

    assert migration_module._first_record_mismatch(actual, expected) == "relative_path"


def test_migration_counts_allow_only_projection_history_superset() -> None:
    expected = {"documents": 2, "revisions": 2, "projections": 2, "units": 10}

    assert migration_module._durable_counts_cover_expected(
        {"documents": 2, "revisions": 2, "projections": 3, "units": 14},
        expected,
    )
    assert not migration_module._durable_counts_cover_expected(
        {"documents": 3, "revisions": 2, "projections": 3, "units": 14},
        expected,
    )
    assert not migration_module._durable_counts_cover_expected(
        {"documents": 2, "revisions": 2, "projections": 1, "units": 9},
        expected,
    )


def test_successful_migration_checkpoint_cleanup_keeps_only_current_items(
    tmp_path: Path,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    state = SyncState(load_config(write_config(tmp_path, root)))
    state.remember_migration("sense", "complete", "a" * 64, {"ok": True})
    state.remember_migration(
        "corpus-projection", "notes:projection_current", "b" * 64, {"ok": True}
    )
    state.remember_migration(
        "corpus-projection", "notes:projection_retired", "c" * 64, {"ok": True}
    )
    state.remember_migration("retired-product", "old", "d" * 64, {"ok": True})

    removed = state.prune_migration_progress(
        {
            "sense": {"complete"},
            "corpus-projection": {"notes:projection_current"},
        }
    )

    assert removed == 2
    assert state.migration_checkpoint("sense", "complete") is not None
    assert (
        state.migration_checkpoint("corpus-projection", "notes:projection_current")
        is not None
    )
    assert (
        state.migration_checkpoint("corpus-projection", "notes:projection_retired")
        is None
    )


def test_remote_storage_report_and_conservative_maintenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    config = load_config(write_config(tmp_path, root))
    calls: list[dict[str, object]] = []

    class FakeCorpus:
        def __init__(self, _config: object) -> None:
            pass

        def corpus_ids(self) -> list[str]:
            return ["notes"]

    class FakeRemote:
        def __init__(self, _config: object, _token: str) -> None:
            pass

        async def close(self) -> None:
            pass

        async def inventory(self, corpus_id: str, **options: object) -> dict:
            calls.append({"operation": "inventory", "corpus_id": corpus_id, **options})
            detailed = bool(options.get("include_storage_details"))
            return {
                "counts": {
                    "documents": 1,
                    "revisions": 1,
                    "projections": 2,
                    "units": 3,
                },
                "external": {"binding_count": 0, "run_count": 0, "record_count": 0},
                "staged_upload_count": 1 if not detailed else 0,
                "staged_uploads": (
                    [
                        {
                            "upload_id": "upload_old",
                            "created_at": "2026-09-01T00:00:00Z",
                        }
                    ]
                    if not detailed
                    else []
                ),
                "storage": {
                    "database_size_bytes": 42,
                    "search_index_pending_projections": 0 if detailed else 2,
                },
                "storage_details": (
                    {
                        "indexed_unit_count": 2,
                        "searchable_unit_count": 2,
                        "structural_only_unit_count": 1,
                        "hotspots": [],
                    }
                    if detailed
                    else None
                ),
            }

        async def maintain_corpus(self, corpus_id: str, **options: object) -> dict:
            calls.append({"operation": "maintain", "corpus_id": corpus_id, **options})
            if options.get("remove_upload_ids"):
                return {"removed": {"uploads": 1}}
            if options.get("compact_unit_metadata_limit"):
                return {
                    "unit_metadata": {
                        "scanned_units": 3,
                        "rewritten_units": 2,
                        "compacted_units": 2,
                        "structure_path_scanned_units": 3,
                        "compacted_structure_path_units": 1,
                        "bytes_before": 120,
                        "bytes_after": 40,
                        "complete": True,
                    }
                }
            compact_calls = sum(
                1
                for call in calls
                if call.get("operation") == "maintain"
                and call.get("compact_search_index_limit")
            )
            return {
                "search_index": {
                    "processed_projections": 1,
                    "reindexed_searchable_rows": 2,
                    "removed_structural_only_rows": 1,
                    "excluded_structure_path_logical_bytes": 80,
                    "legacy_index_reclaimed": compact_calls == 2,
                    "pending_projections": max(0, 2 - compact_calls),
                }
            }

    monkeypatch.setattr(storage_module, "LocalCorpusMigration", FakeCorpus)
    monkeypatch.setattr(storage_module, "RemoteClient", FakeRemote)
    monkeypatch.setattr(storage_module, "read_token", lambda _device_id: "token")

    report = asyncio.run(remote_storage_report(config, hotspot_limit=3))
    assert report["database_size_bytes"] == 42
    assert report["corpora"][0]["corpus_id"] == "notes"

    maintained = asyncio.run(
        maintain_remote_storage(
            config,
            staged_min_age_hours=0,
            maximum_batches_per_corpus=3,
        )
    )
    summary = maintained["corpora"][0]
    assert maintained["canonical_records_removed"] == 0
    assert summary["removed_staged_uploads"] == 1
    assert summary["processed_search_index_projections"] == 2
    assert summary["reindexed_searchable_rows"] == 4
    assert summary["removed_structural_only_index_rows"] == 2
    assert summary["excluded_search_index_structure_path_logical_bytes"] == 160
    assert summary["legacy_search_index_reclaimed"] is True
    assert summary["pending_search_index_projections"] == 0
    assert summary["scanned_unit_metadata_rows"] == 3
    assert summary["compacted_source_anchor_rows"] == 2
    assert summary["scanned_structure_path_rows"] == 3
    assert summary["compacted_structure_path_rows"] == 1
    assert summary["unit_metadata_bytes_saved"] == 80
    assert summary["unit_metadata_complete"] is True


def test_reconcile_coalesces_change_and_preserves_document_identity_on_rename(
    tmp_path: Path,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    document = root / "note.txt"
    document.write_text("first", encoding="utf-8")
    config = load_config(write_config(tmp_path, root))
    assert config.full_reconcile_seconds == 900
    assert config.event_debounce_seconds == 2
    state = SyncState(config)

    first = reconcile_all(state)
    assert first[0]["changed"] == 1
    queued = state.due_changes()
    assert len(queued) == 1
    document_id = queued[0]["document_id"]

    document.write_text("second", encoding="utf-8")
    reconcile_all(state)
    assert len(state.due_changes()) == 1
    assert state.due_changes()[0]["document_id"] == document_id

    renamed = root / "renamed.txt"
    document.rename(renamed)
    reconcile_all(state)
    moved = state.due_changes()[0]
    assert moved["document_id"] == document_id
    assert moved["relative_path_nfc"] == "renamed.txt"
    assert moved["event_kind"] == "moved"


def test_corpus_seed_adopts_canonical_id_for_the_same_observed_file(
    tmp_path: Path,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    document = root / "note.txt"
    document.write_text("indexed", encoding="utf-8")
    state = SyncState(load_config(write_config(tmp_path, root)))
    reconcile_all(state)
    observed = state.due_changes()[0]
    assert observed["document_id"] != "doc_canonical"
    with state.connect() as connection:
        connection.execute(
            "UPDATE documents SET inode = inode + 1 WHERE connection_key = ?",
            ("notes:main",),
        )

    metadata = document.stat()
    seeded = state.seed_documents(
        "notes:main",
        [
            {
                "document_id": "doc_canonical",
                "relative_path": "note.txt",
                "relative_path_nfc": "note.txt",
                "device": metadata.st_dev,
                "inode": metadata.st_ino,
                "size": metadata.st_size,
                "modified_ns": metadata.st_mtime_ns,
                "changed_ns": metadata.st_ctime_ns,
                "last_revision_sha256": "a" * 64,
                "last_projection_id": "projection_canonical",
                "needs_refresh": False,
            }
        ],
    )

    assert seeded == {"seeded": 1, "queued": 0}
    with state.connect() as connection:
        rows = connection.execute(
            "SELECT document_id FROM documents WHERE connection_key = ?",
            ("notes:main",),
        ).fetchall()
    assert [row["document_id"] for row in rows] == ["doc_canonical"]
    assert state.due_changes() == []


def test_corpus_seed_records_active_projection_adapter_identity(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    document = root / "note.txt"
    document.write_text("indexed", encoding="utf-8")
    metadata = document.stat()
    state = SyncState(load_config(write_config(tmp_path, root)))

    data_root = tmp_path / "corpus-data"
    database_path = data_root / "corpora" / "notes" / "corpus.sqlite"
    database_path.parent.mkdir(parents=True)
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE documents (
                document_id TEXT PRIMARY KEY,
                relative_path TEXT NOT NULL,
                relative_path_nfc TEXT NOT NULL,
                device INTEGER NOT NULL,
                inode INTEGER NOT NULL,
                logical_size INTEGER NOT NULL,
                modified_ns INTEGER NOT NULL,
                changed_ns INTEGER NOT NULL,
                current_revision_id TEXT,
                eligibility_state TEXT NOT NULL,
                lifecycle_state TEXT NOT NULL,
                deleted_at TEXT
            );
            CREATE TABLE revisions (
                revision_id TEXT PRIMARY KEY,
                sha256 TEXT NOT NULL,
                source_size INTEGER NOT NULL,
                source_modified_ns INTEGER NOT NULL,
                source_changed_ns INTEGER NOT NULL,
                source_inode INTEGER NOT NULL
            );
            CREATE TABLE extraction_projections (
                projection_id TEXT PRIMARY KEY,
                revision_id TEXT NOT NULL,
                is_active INTEGER NOT NULL,
                adapter_id TEXT NOT NULL,
                adapter_version TEXT NOT NULL,
                config_hash TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
            (
                "doc_canonical",
                "note.txt",
                "note.txt",
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_size,
                metadata.st_mtime_ns,
                metadata.st_ctime_ns,
                "revision_current",
                "supported",
                "active",
            ),
        )
        connection.execute(
            "INSERT INTO revisions VALUES (?, ?, ?, ?, ?, ?)",
            (
                "revision_current",
                hashlib.sha256(b"indexed").hexdigest(),
                metadata.st_size,
                metadata.st_mtime_ns,
                metadata.st_ctime_ns,
                metadata.st_ino,
            ),
        )
        connection.execute(
            "INSERT INTO extraction_projections VALUES (?, ?, 1, ?, ?, ?)",
            (
                "projection_current",
                "revision_current",
                "document-files.builtin.text",
                "source-units-v4",
                "a" * 64,
            ),
        )

    corpus = object.__new__(migration_module.LocalCorpusMigration)
    corpus.config = state.config
    corpus.data_root = data_root
    corpus._catalog = {"notes": {}}

    assert corpus.seed_documents(state, "notes") == {"seeded": 1, "queued": 0}
    with state.connect() as connection:
        row = connection.execute(
            "SELECT adapter_id, adapter_version, config_hash FROM documents"
        ).fetchone()
    assert dict(row) == {
        "adapter_id": "document-files.builtin.text",
        "adapter_version": "source-units-v4",
        "config_hash": "a" * 64,
    }


def test_analyzer_change_queues_only_known_stale_projections(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    current_path = root / "current.txt"
    stale_path = root / "stale.md"
    unknown_path = root / "unknown.pdf"
    for path in (current_path, stale_path, unknown_path):
        path.write_text(path.stem, encoding="utf-8")
    state = SyncState(load_config(write_config(tmp_path, root)))
    reconcile_all(state)
    observed = {row["relative_path_nfc"]: row for row in state.due_changes()}

    state.complete_change(
        "notes:main",
        observed["current.txt"]["document_id"],
        hashlib.sha256(b"current").hexdigest(),
        "projection_current",
        adapter_id="document-files.process.txt",
        adapter_version="2",
        config_hash="a" * 64,
    )
    state.complete_change(
        "notes:main",
        observed["stale.md"]["document_id"],
        hashlib.sha256(b"stale").hexdigest(),
        "projection_stale",
        adapter_id="document-files.process.md",
        adapter_version="1",
        config_hash="b" * 64,
    )
    # A pre-upgrade row without a known descriptor is not evidence of staleness.
    state.complete_change(
        "notes:main",
        observed["unknown.pdf"]["document_id"],
        hashlib.sha256(b"unknown").hexdigest(),
        "projection_unknown",
    )

    queued = state.enqueue_outdated_analyzers(
        {
            "txt": {
                "adapter_id": "document-files.process.txt",
                "adapter_version": "2",
                "config_hash": "a" * 64,
            },
            "md": {
                "adapter_id": "document-files.process.md",
                "adapter_version": "2",
                "config_hash": "c" * 64,
            },
            "pdf": {
                "adapter_id": "document-files.process.pdf",
                "adapter_version": "2",
                "config_hash": "d" * 64,
            },
        }
    )

    assert queued == 1
    changes = state.due_changes()
    assert [(row["relative_path_nfc"], row["event_kind"]) for row in changes] == [
        ("stale.md", "analyzer_refresh")
    ]


def test_local_analyzer_manifest_keeps_only_durable_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Completed:
        returncode = 0
        stdout = json.dumps(
            {
                "schema_version": "document-files.descriptor.v1",
                "formats": {
                    "txt": {
                        "media_type": "text/plain",
                        "descriptor": {
                            "adapter_id": "document-files.process.txt",
                            "adapter_version": "wrapper",
                            "config_hash": "f" * 64,
                            "capabilities": {"format_ids": ["txt"]},
                        },
                        "config": {
                            "processor_implementation_sha256": "e" * 64,
                            "route": {
                                "adapter_id": "document-files.builtin.text",
                                "adapter_version": "2",
                                "config_hash": "a" * 64,
                                "capabilities": {"format_ids": ["txt"]},
                            },
                        },
                    }
                },
            }
        )

    monkeypatch.setattr(
        analysis_module.subprocess, "run", lambda *args, **kwargs: Completed()
    )

    assert analysis_module.local_analyzer_manifest(tmp_path / "python") == {
        "txt": {
            "adapter_id": "document-files.builtin.text",
            "adapter_version": "2",
            "config_hash": "a" * 64,
        }
    }


def test_deleted_unknown_remote_document_completes_idempotently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    document = root / "retired.txt"
    document.write_text("temporary", encoding="utf-8")
    daemon = SyncDaemon(load_config(write_config(tmp_path, root)), "test-token")
    reconcile_all(daemon.state)
    document.unlink()
    reconcile_all(daemon.state)
    change = daemon.state.due_changes()[0]
    assert change["event_kind"] == "deleted"

    async def missing(*args: object, **kwargs: object) -> dict[str, object]:
        raise SyncError("document_not_found", "document is already absent")

    monkeypatch.setattr(daemon.remote, "update_source_state", missing)

    async def exercise() -> None:
        await daemon._process_change(change)
        await daemon.close()

    asyncio.run(exercise())
    assert daemon.state.due_changes() == []


def test_unsupported_file_version_does_not_retry_until_it_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    document = root / "opaque.bin"
    document.write_bytes(b"opaque")
    daemon = SyncDaemon(load_config(write_config(tmp_path, root)), "test-token")
    reconcile_all(daemon.state)
    change = daemon.state.due_changes()[0]

    async def unsupported(*args: object, **kwargs: object) -> dict[str, object]:
        raise SyncError("unsupported_format", "format is not supported")

    monkeypatch.setattr(daemon_module, "select_analyzer", unsupported)

    async def exercise() -> None:
        await daemon._process_change(change)
        await daemon.close()

    asyncio.run(exercise())
    assert daemon.state.due_changes() == []
    assert reconcile_all(daemon.state)[0]["changed"] == 0

    document.write_bytes(b"changed")
    assert reconcile_all(daemon.state)[0]["changed"] == 1
    assert len(daemon.state.due_changes()) == 1


def test_metadata_only_change_reuses_the_committed_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    document = root / "stable.txt"
    document.write_text("same bytes", encoding="utf-8")
    daemon = SyncDaemon(load_config(write_config(tmp_path, root)), "test-token")
    reconcile_all(daemon.state)
    initial = daemon.state.due_changes()[0]
    digest = hashlib.sha256(b"same bytes").hexdigest()
    daemon.state.complete_change(
        initial["connection_key"],
        initial["document_id"],
        digest,
        "projection_existing",
    )
    metadata = document.stat()
    os.utime(document, ns=(metadata.st_atime_ns, metadata.st_mtime_ns + 1_000_000))
    reconcile_all(daemon.state)
    change = daemon.state.due_changes()[0]
    observed: list[tuple[object, ...]] = []

    async def update(*args: object, **kwargs: object) -> dict[str, object]:
        observed.append(args)
        return {"changed": True}

    async def should_not_analyze(*args: object, **kwargs: object) -> dict[str, object]:
        raise AssertionError("unchanged bytes must not be analyzed again")

    monkeypatch.setattr(daemon.remote, "update_source_state", update)
    monkeypatch.setattr(daemon_module, "select_analyzer", should_not_analyze)

    async def exercise() -> None:
        await daemon._process_change(change)

    asyncio.run(exercise())
    assert observed and observed[0][2] == "available"
    assert daemon.state.due_changes() == []

    analysis_calls: list[str] = []
    maintenance_calls: list[dict[str, object]] = []
    resolution_calls: list[tuple[object, ...]] = []

    async def analyze(
        _state: object,
        _remote: object,
        _change: dict[str, object],
        snapshot: Snapshot,
        selected_format: str,
    ) -> dict[str, object]:
        analysis_calls.append(snapshot.sha256)
        descriptor = {
            "adapter_id": "document-files.text",
            "adapter_version": "2",
            "config_hash": "a" * 64,
            "capabilities": {"format_ids": ["txt"], "supports_ocr": False},
        }
        return {
            "input": {
                "format_id": selected_format,
                "byte_size": snapshot.byte_size,
                "sha256": snapshot.sha256,
            },
            "analyzer": descriptor,
            "extraction": {
                "descriptor": descriptor,
                "completeness": "complete",
                "coverage": {"text_content": "complete"},
                "units": [
                    {
                        "unit_type": "paragraph",
                        "structure_path": {"paragraph": 1},
                        "content": "same bytes",
                        "derivation_method": "native_text",
                        "geometry": {},
                        "confidence": 1,
                        "quality_flags": [],
                        "issues": [],
                    }
                ],
                "issues": [],
                "manifest_hash": "b" * 64,
            },
        }

    async def upload(
        _corpus_id: str, header: dict[str, object], _units: list[dict[str, object]]
    ) -> dict[str, object]:
        revision = header["revision"]
        projection = header["projection"]
        assert isinstance(revision, dict)
        assert isinstance(projection, dict)
        if revision["sha256"] == digest:
            assert revision["revisionId"] == "rev_migrated"
        return {"projectionId": projection["projectionId"]}

    async def resolve(*args: object) -> str:
        resolution_calls.append(args)
        return "rev_migrated"

    async def maintain(_corpus_id: str, **options: object) -> dict[str, object]:
        maintenance_calls.append(options)
        return {"removed": {"projections": 1}}

    monkeypatch.setattr(daemon_module, "select_analyzer", analyze)
    monkeypatch.setattr(daemon.remote, "resolve_revision", resolve)
    monkeypatch.setattr(daemon.remote, "upload_projection", upload)
    monkeypatch.setattr(daemon.remote, "maintain_corpus", maintain)

    job = {
        "jobId": f"job_{'d' * 32}",
        "operation": "source.refresh",
        "scope": {"spaceId": "notes", "connectionId": "main", "generation": 3},
        "request": {
            "space_id": "notes",
            "connection_id": "main",
            "document_id": initial["document_id"],
            "expected_revision_sha256": digest,
        },
        "maximumResponseBytes": 1024 * 1024,
        "expiresAt": "2099-01-01T00:00:00+00:00",
    }

    async def refresh_and_change() -> tuple[dict[str, object], dict[str, object]]:
        response = await daemon._execute_job(job)
        refresh_result = response["result"]
        assert isinstance(refresh_result, dict)
        document.write_text("changed bytes", encoding="utf-8")
        reconcile_all(daemon.state)
        await daemon._process_change(daemon.state.due_changes()[0])
        changed_result = daemon.state.refresh_result(
            "notes", "main", initial["document_id"]
        )
        await daemon.close()
        return response, changed_result

    response, changed_result = asyncio.run(refresh_and_change())
    assert response["ok"] is True
    result = response["result"]
    assert isinstance(result, dict)
    assert result["completed"] is True
    assert result["revision_sha256"] == digest
    assert result["projection_id"] != "projection_existing"
    changed_digest = hashlib.sha256(b"changed bytes").hexdigest()
    assert changed_result["revision_sha256"] == changed_digest
    assert changed_result["projection_id"] != result["projection_id"]
    assert analysis_calls == [digest, changed_digest]
    assert resolution_calls == [
        ("notes", initial["document_id"], digest, len(b"same bytes"))
    ]
    assert maintenance_calls == [
        {
            "remove_projection_ids": ["projection_existing"],
            "remove_document_ids": [],
            "remove_upload_ids": [],
        },
        {
            "remove_projection_ids": [result["projection_id"]],
            "remove_document_ids": [],
            "remove_upload_ids": [],
        },
    ]
    assert daemon.state.due_changes() == []


def test_root_move_is_recovered_by_current_identity_when_locator_is_updated(
    tmp_path: Path,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    identity = root.stat().st_dev, root.stat().st_ino
    moved = tmp_path / "moved-source"
    root.rename(moved)
    # A known current locator always proves the identity check. macOS can also
    # recover the moved path from the stale locator through the volume identity.
    assert resolve_moved_root(moved, *identity) == moved
    assert resolve_moved_root(root, *identity) == (
        moved if sys.platform == "darwin" else None
    )


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS file identity recovery")
def test_restart_recovers_root_after_configured_locator_moves(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "note.txt").write_text("durable", encoding="utf-8")
    config_path = write_config(tmp_path, root)
    config = load_config(config_path)
    state = SyncState(config)
    reconcile_all(state)

    moved = tmp_path / "renamed-source"
    root.rename(moved)

    restarted = SyncState(load_config(config_path))
    results = reconcile_all(restarted)
    row = restarted.connection_row("notes:main")

    assert results[0]["state"] == "available"
    assert Path(row["root_path"]) == moved
    assert row["location_state"] == "available"


def test_capture_is_version_pinned_and_never_follows_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "note.txt").write_text("captured", encoding="utf-8")
    staging = tmp_path / "staging"
    identity = root.stat().st_dev, root.stat().st_ino
    with capture_snapshot(root, identity, "note.txt", staging, 1000) as snapshot:
        assert snapshot.path.read_text(encoding="utf-8") == "captured"
        assert snapshot.sha256 == hashlib.sha256(b"captured").hexdigest()
        temporary = snapshot.path
    assert not temporary.exists()

    (root / "link.txt").symlink_to(root / "note.txt")
    with (
        pytest.raises(SyncError, match="regular file"),
        capture_snapshot(root, identity, "link.txt", staging, 1000),
    ):
        pass


def test_projection_mapping_contains_no_absolute_local_path(tmp_path: Path) -> None:
    source = tmp_path / "capture"
    source.write_text("hello", encoding="utf-8")
    digest = hashlib.sha256(b"hello").hexdigest()
    snapshot = Snapshot(
        path=source,
        byte_size=5,
        sha256=digest,
        modified_ns=1,
        changed_ns=1,
        device=1,
        inode=2,
    )
    descriptor = {
        "adapter_id": "document-files.text",
        "adapter_version": "2",
        "config_hash": "a" * 64,
        "capabilities": {"format_ids": ["txt"], "supports_ocr": False},
    }
    result = {
        "input": {"format_id": "txt", "byte_size": 5, "sha256": digest},
        "analyzer": descriptor,
        "extraction": {
            "descriptor": descriptor,
            "completeness": "complete",
            "coverage": {"text_content": "complete"},
            "units": [
                {
                    "unit_type": "paragraph",
                    "structure_path": {"paragraph": 1},
                    "content": "hello",
                    "derivation_method": "native_text",
                    "geometry": {},
                    "confidence": 1,
                    "quality_flags": [],
                    "issues": [],
                }
            ],
            "issues": [],
            "manifest_hash": "b" * 64,
        },
    }
    header, units = build_projection(
        change={
            "document_id": "doc_test",
            "corpus_id": "notes",
            "relative_path_nfc": "folder/note.txt",
        },
        snapshot=snapshot,
        selected_format="txt",
        result=result,
        revision_id="rev_migrated",
    )
    serialized = json.dumps({"header": header, "units": units})
    assert str(tmp_path) not in serialized
    assert header["document"]["extension"] == "txt"
    assert header["revision"]["revisionId"] == "rev_migrated"
    assert header["projection"]["projectionId"].startswith("projection_")
    assert units[0]["sourceAnchor"] == {
        "relative_path": "folder/note.txt",
        "structure_path": {"paragraph": 1},
    }


def test_configuration_rejects_http_and_root_rebind(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    config_path = write_config(tmp_path, root)
    text = config_path.read_text().replace("https://", "http://")
    config_path.write_text(text)
    with pytest.raises(SyncError, match="HTTPS"):
        load_config(config_path)

    config_path.write_text(text.replace("http://", "https://"))
    config = load_config(config_path)
    state = SyncState(config)
    root.rmdir()
    root.mkdir()
    state = SyncState(load_config(config_path))
    assert state.connection_row("notes:main")["location_state"] == "unavailable"


def test_completed_job_replay_is_identity_checked_and_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    state = SyncState(load_config(write_config(tmp_path, root)))
    monkeypatch.setattr(state_module, "MAX_COMPLETED_JOBS", 3)

    for index in range(4):
        request = {"operation": "work.file.list", "sequence": index}
        response = {"ok": True, "result": {"sequence": index}}
        state.remember_job(f"job_{index}", request, response)

    with state.connect() as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM completed_jobs").fetchone()[0] == 3
        )
    assert state.completed_job(
        "job_3", {"operation": "work.file.list", "sequence": 3}
    ) == {"ok": True, "result": {"sequence": 3}}
    with pytest.raises(SyncError, match="different request"):
        state.completed_job("job_3", {"operation": "work.file.list", "sequence": 99})


def test_broker_session_keeps_connection_after_job_ack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    config = load_config(write_config(tmp_path, root))

    class FakeWebSocket:
        def __init__(self) -> None:
            self.sent: list[str] = []
            self.closed: list[tuple[int, str]] = []

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_args: object) -> None:
            pass

        async def send(self, value: str) -> None:
            self.sent.append(value)

        async def close(self, code: int, reason: str) -> None:
            self.closed.append((code, reason))

        def __aiter__(self):
            async def messages():
                yield json.dumps({"type": "hello_ack"})
                yield json.dumps(
                    {"type": "job_ack", "jobId": "job_test", "accepted": True}
                )

            return messages()

    websocket = FakeWebSocket()
    monkeypatch.setattr(daemon_module, "connect", lambda *_args, **_kwargs: websocket)

    async def run() -> None:
        daemon = SyncDaemon(config, "token")
        try:
            await daemon._broker_session()
        finally:
            await daemon.close()

    asyncio.run(run())
    assert websocket.closed == []
    assert json.loads(websocket.sent[0])["type"] == "hello"


def test_work_jobs_recheck_scope_generation_and_write_permission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    source = root / "note.txt"
    source.write_text("source", encoding="utf-8")
    config_path = write_config(tmp_path, root)
    config_path.write_text(
        config_path.read_text().replace(
            'permission = "read_write"', 'permission = "read_only"'
        )
    )
    config = load_config(config_path)
    state = SyncState(config)
    reconcile_all(state)
    source_change = state.due_changes()[0]
    digest = hashlib.sha256(b"source").hexdigest()
    state.complete_change(
        source_change["connection_key"],
        source_change["document_id"],
        digest,
        "projection_source",
    )
    executor = WorkExecutor(config, state)
    monkeypatch.setattr(
        executor,
        "_invoke",
        lambda operation, space_id, connection_id, request: {
            "operation": operation,
            "space_id": space_id,
            "connection_id": connection_id,
        },
    )

    scope = {"spaceId": "notes", "connectionId": "main", "generation": 3}
    assert (
        executor.execute(
            "work.file.list", scope, {"space_id": "notes", "connection_id": "main"}
        )["operation"]
        == "work.file.list"
    )
    with pytest.raises(PolicyDenied, match="read-only"):
        executor.execute(
            "work.file.write",
            scope,
            {"space_id": "notes", "connection_id": "main"},
        )
    with pytest.raises(SyncError, match="binding changed"):
        executor.execute(
            "work.file.list",
            {**scope, "generation": 2},
            {"space_id": "notes", "connection_id": "main"},
        )
    with pytest.raises(SyncError, match="escaped"):
        executor.execute(
            "work.file.list",
            scope,
            {"space_id": "another-space", "connection_id": "main"},
        )
    refreshed = executor.execute(
        "source.refresh",
        scope,
        {
            "space_id": "notes",
            "connection_id": "main",
            "document_id": source_change["document_id"],
            "expected_revision_sha256": digest,
        },
    )
    assert refreshed["requested"] is True
    assert state.due_changes()[0]["event_kind"] == "refresh"


def test_broker_advertises_only_executable_local_operations() -> None:
    assert WORK_OPERATIONS == (
        "work.file.list",
        "work.file.read",
        "work.file.write",
        "work.file.delete",
        "work.file.select_current",
        "work.file.restore",
    )
    assert all(operation.startswith("work.file.") for operation in WORK_OPERATIONS)
    assert SYNC_OPERATIONS == (*WORK_OPERATIONS, "source.refresh")


def test_work_helper_pins_sync_managed_document_files_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    runtime_bin = tmp_path / "runtimes" / "document-files" / "bin"
    runtime_bin.mkdir(parents=True)
    runtime_python = runtime_bin / "python"
    runtime_python.write_text("", encoding="utf-8")
    runtime_python.chmod(0o700)
    executable = runtime_bin / "document-files"
    executable.write_text("", encoding="utf-8")
    executable.chmod(0o700)
    config = replace(
        load_config(write_config(tmp_path, root)),
        corpus_data_root=tmp_path / "corpus-data",
        corpus_python=runtime_python,
        document_files_python=runtime_python,
    )
    observed: dict[str, object] = {}

    def fake_run(
        args: list[str], **options: object
    ) -> subprocess.CompletedProcess[str]:
        observed["args"] = args
        observed["env"] = options.get("env")
        return subprocess.CompletedProcess(
            args,
            0,
            stdout='{"ok":true,"result":{"listed":true}}',
            stderr="",
        )

    monkeypatch.setattr(work_module.subprocess, "run", fake_run)
    result = WorkExecutor(config, SyncState(config))._invoke(
        "work.file.list", "notes", "main", {"relative_path": "."}
    )

    assert result == {"listed": True}
    environment = observed["env"]
    assert isinstance(environment, dict)
    assert environment["DOCUMENT_FILES_EXECUTABLE"] == str(executable)


def test_remote_analysis_requires_exact_revision_approval_and_transfer_budget(
    tmp_path: Path,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    source = root / "note.txt"
    source.write_text("private draft", encoding="utf-8")
    config = load_config(write_config(tmp_path, root, route="approval_required"))
    state = SyncState(config)
    reconcile_all(state)
    change = state.due_changes()[0]
    content = source.read_bytes()
    snapshot = Snapshot(
        path=source,
        byte_size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        modified_ns=source.stat().st_mtime_ns,
        changed_ns=source.stat().st_ctime_ns,
        device=source.stat().st_dev,
        inode=source.stat().st_ino,
    )

    class RemoteAnalyzer:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def analyze_remote(self, **kwargs: object) -> dict[str, object]:
            self.calls.append(kwargs)
            return {"accepted": True}

    remote = RemoteAnalyzer()
    with pytest.raises(PolicyDenied, match="requires owner approval"):
        asyncio.run(select_analyzer(state, remote, change, snapshot, "txt"))  # type: ignore[arg-type]
    assert remote.calls == []

    state.approve_remote(
        change["connection_key"],
        change["document_id"],
        snapshot.sha256,
        snapshot.byte_size,
    )
    assert asyncio.run(  # type: ignore[arg-type]
        select_analyzer(state, remote, change, snapshot, "txt")
    ) == {"accepted": True}
    assert remote.calls[0]["sha256"] == snapshot.sha256

    too_large = Snapshot(
        path=source,
        byte_size=change["max_transfer_bytes"] + 1,
        sha256="f" * 64,
        modified_ns=snapshot.modified_ns,
        changed_ns=snapshot.changed_ns,
        device=snapshot.device,
        inode=snapshot.inode,
    )
    state.approve_remote(
        change["connection_key"],
        change["document_id"],
        too_large.sha256,
        too_large.byte_size,
    )
    with pytest.raises(PolicyDenied, match="transfer limit"):
        asyncio.run(select_analyzer(state, remote, change, too_large, "txt"))  # type: ignore[arg-type]
