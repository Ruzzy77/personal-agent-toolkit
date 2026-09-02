from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from personal_agent_sync.analysis import build_projection
from personal_agent_sync.config import load_config
from personal_agent_sync.errors import SyncError
from personal_agent_sync.paths import Snapshot, capture_snapshot, resolve_moved_root
from personal_agent_sync.reconcile import reconcile_all
from personal_agent_sync.state import SyncState


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


def test_reconcile_coalesces_change_and_preserves_document_identity_on_rename(
    tmp_path: Path,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    document = root / "note.txt"
    document.write_text("first", encoding="utf-8")
    config = load_config(write_config(tmp_path, root))
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


def test_root_move_is_recovered_by_current_identity_when_locator_is_updated(
    tmp_path: Path,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    identity = root.stat().st_dev, root.stat().st_ino
    moved = tmp_path / "moved-source"
    root.rename(moved)
    # Linux does not expose /.vol. A known current locator still proves that the
    # identity check, which macOS supplies automatically, accepts the moved root.
    assert resolve_moved_root(moved, *identity) == moved
    assert resolve_moved_root(root, *identity) is None


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
    )
    serialized = json.dumps({"header": header, "units": units})
    assert str(tmp_path) not in serialized
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
    SyncState(config)
    root.rmdir()
    root.mkdir()
    with pytest.raises(SyncError, match="identity"):
        SyncState(load_config(config_path))
