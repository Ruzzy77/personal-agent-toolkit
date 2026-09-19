from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from personal_agent_host.files import ToolError
from personal_agent_host.workspace_files import WorkspaceFiles
from test_host import _workspace_config
from test_host import config as make_host_config


@pytest.fixture
def host_config(tmp_path: Path):
    return make_host_config.__wrapped__(tmp_path)

def _files(host_config) -> WorkspaceFiles:
    return WorkspaceFiles(host_config)


def test_korean_list_move_trash_restore(host_config) -> None:
    policy = host_config.root("demo/main")
    (policy.root / "가나다.txt").write_text("one", encoding="utf-8")
    api = _files(host_config)
    listed = api.dispatch("demo/main", "list")
    assert "가나다.txt" in [entry["name"] for entry in listed["entries"]]
    version = api.dispatch("demo/main", "stat", path="가나다.txt")["version"]
    moved = api.dispatch("demo/main", "move", path="가나다.txt", destination="새이름.txt",
                         expected_version=version)
    assert moved["path"] == "새이름.txt"
    version = api.dispatch("demo/main", "stat", path="새이름.txt")["version"]
    trashed = api.dispatch("demo/main", "trash", path="새이름.txt", expected_version=version)
    assert not (policy.root / "새이름.txt").exists()
    restored = api.dispatch("demo/main", "restore", trash_id=trashed["trash_id"])
    assert restored["path"] == "새이름.txt"


def test_conflict_readonly_protected_and_symlink(host_config, tmp_path: Path) -> None:
    api = _files(host_config)
    policy = host_config.root("demo/main")
    (policy.root / "a.txt").write_text("a")
    with pytest.raises(ToolError, match="version_conflict"):
        api.dispatch("demo/main", "trash", path="a.txt", expected_version="sha256:wrong")
    with pytest.raises(ToolError, match="policy_denied"):
        api.dispatch("demo/frozen", "mkdir", path="no")
    protected_base = tmp_path / "protected"
    protected = _workspace_config(
        protected_base, guard=str(protected_base / "workspace" / "work" / "regulations")
    )
    protected_api = _files(protected)
    with pytest.raises(ToolError, match="policy_denied"):
        protected_api.dispatch("workspace", "trash", path="work",
                               expected_version=protected_api.dispatch("workspace", "stat", path="work")["version"])
    if hasattr(os, "symlink"):
        actual = policy.root / "actual.txt"
        actual.write_text("actual")
        link = policy.root / "link"
        link.symlink_to("actual.txt")
        version = api.dispatch("demo/main", "stat", path="actual.txt")["version"]
        with pytest.raises(ToolError):
            api.dispatch("demo/main", "move", path="link", destination="moved.txt",
                         expected_version=version)
        with pytest.raises(ToolError):
            api.dispatch("demo/main", "trash", path="link", expected_version=version)
        assert actual.exists()


def test_expire_only_removes_valid_expired_record(host_config) -> None:
    policy = host_config.root("demo/main")
    (policy.root / "expired.txt").write_text("x")
    api = _files(host_config)
    version = api.dispatch("demo/main", "stat", path="expired.txt")["version"]
    item = api.dispatch("demo/main", "trash", path="expired.txt", expected_version=version)
    metadata = api._trash_root(policy) / item["trash_id"] / "metadata.json"
    value = json.loads(metadata.read_text())
    value["expires"] = 0
    metadata.write_text(json.dumps(value))
    assert api.expire() == {"expired": 1}
    assert not metadata.parent.exists()



def test_listing_skips_upload_staging_without_losing_next_page(host_config) -> None:
    root = host_config.root("demo/main").root
    (root / ".host-transfer-unit-test").write_bytes(b"partial")
    (root / "first.txt").write_text("first")
    (root / "second.txt").write_text("second")
    api = _files(host_config)
    first = api.dispatch("demo/main", "list", limit=1)
    assert first["entries"][0]["name"] == "first.txt"
    second = api.dispatch("demo/main", "list", limit=1, cursor=first["next_cursor"])
    assert second["entries"][0]["name"] == "second.txt"
