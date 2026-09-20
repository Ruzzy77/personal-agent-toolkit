"""Host file tools: path safety, versions, marker replacement, and the HTTP guard."""

from __future__ import annotations

import asyncio
import base64
import json
import shutil
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from personal_agent_host.config import HostConfig, load_host_config
from personal_agent_host.files import ToolError, read_files, search, write_file
from personal_agent_host.server import decode_exec_stdin
from personal_agent_sync.errors import SyncError

TOKEN = "test-token-0123456789abcdef0123456789abcdef"


def test_exec_stdin_base64_preserves_json_text_exactly() -> None:
    source = '{\n  "probe": "toolkit-validation"\n}\n'
    encoded = base64.b64encode(source.encode("utf-8")).decode("ascii")
    assert decode_exec_stdin(None, encoded) == source
    with pytest.raises(ToolError):
        decode_exec_stdin(source, encoded)


@pytest.fixture
def config(tmp_path: Path) -> HostConfig:
    root = tmp_path / "workspace"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "a.txt").write_text("hello\nworld\n", encoding="utf-8")
    prefix = tmp_path / "prefix"
    (prefix / "config").mkdir(parents=True)
    (prefix / "config" / "host-upstream.token").write_text(TOKEN + "\n")
    (prefix / "config" / "host.toml").write_text(
        f"""service_url = "https://context.example.workers.dev"
device_id = "test"
data_root = "{prefix / "state"}"
corpus_data_root = "{prefix / "state" / "corpus"}"
corpus_python = {json.dumps(sys.executable)}

[host]
listen = "127.0.0.1:18790"
allowed_hosts = ["spark-host"]

[[connections]]
space_id = "demo"
connection_id = "main"
root = "{root}"
roles = ["work"]
access_scope = "remote_allowed"
permission = "read_write"
execute = "sandbox"

[[connections]]
space_id = "demo"
connection_id = "frozen"
root = "{root}"
roles = ["source"]
access_scope = "remote_allowed"
permission = "read_only"
corpus_id = "demo"
""",
        encoding="utf-8",
    )
    return load_host_config(prefix / "config" / "host.toml")


def test_roots_and_policies(config: HostConfig) -> None:
    assert [root.id for root in config.roots] == ["demo/main", "demo/frozen"]
    assert config.root("demo/main").execute == "sandbox"
    assert config.root("demo/frozen").permission == "read_only"
    with pytest.raises(SyncError):
        config.root("demo/missing")


def test_read_rejects_paths_outside_the_root(config: HostConfig) -> None:
    root = config.root("demo/main")
    for path in ("../secret", "/etc/passwd", "sub/../../x", "sub/./../../y"):
        with pytest.raises(ToolError) as failure:
            read_files(root, [{"path": path}], 1024)
        assert failure.value.code == "invalid_path"


def test_empty_file_read(config: HostConfig) -> None:
    root = config.root("demo/main")
    created = write_file(config, root, "empty.md", content="")
    read = read_files(root, [{"path": "empty.md"}], 1024)
    assert read == {
        "files": [{
            "path": "empty.md",
            "content": "",
            "version": created["version"],
            "start_line": 1,
            "end_line": 0,
        }],
        "truncated": False,
    }


def test_write_read_versions_and_conflicts(config: HostConfig) -> None:
    root = config.root("demo/main")
    created = write_file(config, root, "notes/new.md", content="one\n")
    assert created["version"].startswith("sha256:")
    read = read_files(root, [{"path": "notes/new.md"}], 1024)
    assert read["files"][0]["content"] == "one\n"
    assert read["files"][0]["version"] == created["version"]

    with pytest.raises(ToolError) as conflict:
        write_file(
            config, root, "notes/new.md", content="two\n", expected_version="sha256:0"
        )
    assert conflict.value.code == "version_conflict"
    with pytest.raises(ToolError) as exists:
        write_file(
            config, root, "notes/new.md", content="two\n", expected_version="absent"
        )
    assert exists.value.code == "version_conflict"

    updated = write_file(
        config,
        root,
        "notes/new.md",
        content="two\n",
        expected_version=created["version"],
    )
    assert updated["version"] != created["version"]
    recovery = list((config.sync.data_root / "host-recovery").glob("*.prev"))
    assert len(recovery) == 1 and recovery[0].read_text() == "one\n"


def test_marker_replacement_and_delete(config: HostConfig) -> None:
    root = config.root("demo/main")
    write_file(
        config, root, "doc.md", content="head\n<!-- a -->\nold\n<!-- b -->\ntail\n"
    )
    replaced = write_file(
        config,
        root,
        "doc.md",
        replace={
            "start_marker": "<!-- a -->\n",
            "end_marker": "\n<!-- b -->",
            "content": "new",
        },
    )
    assert read_files(root, [{"path": "doc.md"}], 1024)["files"][0]["content"] == (
        "head\n<!-- a -->\nnew\n<!-- b -->\ntail\n"
    )
    assert replaced["version"].startswith("sha256:")
    with pytest.raises(ToolError) as missing:
        write_file(
            config,
            root,
            "doc.md",
            replace={
                "start_marker": "<!-- z -->",
                "end_marker": "<!-- b -->",
                "content": "",
            },
        )
    assert missing.value.code == "marker_not_found"
    deleted = write_file(config, root, "doc.md", delete=True)
    assert deleted["version"] == "absent"
    assert not (root.root / "doc.md").exists()


def _workspace_config(tmp_path: Path, *, guard: str, permission: str = "read_write") -> HostConfig:
    root = tmp_path / "workspace"
    (root / "work" / "regulations" / "current").mkdir(parents=True)
    (root / "work" / "regulations" / "current" / "rule.txt").write_text("fixed\n")
    prefix = tmp_path / "prefix"
    (prefix / "config").mkdir(parents=True)
    (prefix / "config" / "host-upstream.token").write_text(TOKEN + "\n")
    (prefix / "config" / "host.toml").write_text(
        f"""service_url = "https://context.example.workers.dev"
device_id = "test"
data_root = "{prefix / "state"}"
corpus_data_root = "{prefix / "state" / "corpus"}"
corpus_python = {json.dumps(sys.executable)}

[host]
listen = "127.0.0.1:18790"
allowed_hosts = ["spark-host"]
read_only_paths = [{json.dumps(guard)}]

[[host.roots]]
id = "workspace"
path = "{root}"
permission = "{permission}"
execute = "sandbox"
""",
        encoding="utf-8",
    )
    return load_host_config(prefix / "config" / "host.toml")


def test_host_root_works_without_a_corpus_connection(tmp_path: Path) -> None:
    config = _workspace_config(
        tmp_path, guard=str(tmp_path / "workspace" / "work" / "regulations" / "current")
    )
    root = config.root("workspace")
    assert root.connection is None
    assert (root.permission, root.execute) == ("read_write", "sandbox")
    written = write_file(config, root, "new-project/notes.md", content="one\n")
    assert written["path"] == "new-project/notes.md"


def test_protected_source_stays_read_only_inside_a_writable_root(
    tmp_path: Path,
) -> None:
    guard = tmp_path / "workspace" / "work" / "regulations" / "current"
    config = _workspace_config(tmp_path, guard=str(guard))
    root = config.root("workspace")
    assert read_files(root, [{"path": "work/regulations/current/rule.txt"}], 1024)[
        "files"
    ][0]["content"] == "fixed\n"
    for attempt in (
        {"content": "changed\n"},
        {"delete": True},
    ):
        with pytest.raises(ToolError) as failure:
            write_file(
                config, root, "work/regulations/current/rule.txt", **attempt
            )
        assert failure.value.code == "policy_denied"
    with pytest.raises(ToolError) as created:
        write_file(config, root, "work/regulations/current/added.txt", content="x")
    assert created.value.code == "policy_denied"


def test_root_inside_a_protected_source_is_read_only(tmp_path: Path) -> None:
    config = _workspace_config(tmp_path, guard=str(tmp_path / "workspace"))
    root = config.root("workspace")
    assert (root.permission, root.execute) == ("read_only", "none")


def test_protection_mounts_pin_the_path_to_the_source(tmp_path: Path) -> None:
    from personal_agent_host.jobs import JobManager

    guard = tmp_path / "workspace" / "work" / "regulations" / "current"
    config = _workspace_config(tmp_path, guard=str(guard))
    mounts = JobManager(config)._protection_mounts(config.root("workspace"))
    destinations = [
        item.split("dst=")[1].split(",")[0] for item in mounts if item != "--mount"
    ]
    assert destinations == [
        "/workspace/work",
        "/workspace/work/regulations",
        "/workspace/work/regulations/current",
    ]
    assert "readonly,bind-recursive=readonly" in mounts[-1]
    assert "readonly" not in mounts[1]


def test_writable_connection_cannot_contain_a_protected_source(
    config: HostConfig, tmp_path: Path
) -> None:
    source = tmp_path / "prefix" / "config" / "host.toml"
    guard = tmp_path / "workspace" / "sub"
    source.write_text(
        source.read_text(encoding="utf-8").replace(
            'allowed_hosts = ["spark-host"]',
            f'allowed_hosts = ["spark-host"]\nread_only_paths = [{json.dumps(str(guard))}]',
        ),
        encoding="utf-8",
    )
    with pytest.raises(SyncError) as failure:
        load_host_config(source)
    assert failure.value.code == "invalid_configuration"


def test_read_only_root_refuses_writes(config: HostConfig) -> None:
    with pytest.raises(ToolError) as failure:
        write_file(config, config.root("demo/frozen"), "x.txt", content="x")
    assert failure.value.code == "policy_denied"


@pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep is not installed")
def test_search_returns_context(config: HostConfig) -> None:
    result = asyncio.run(
        search(
            config.root("demo/main"),
            paths=["**/*"],
            pattern="world",
            max_results=10,
            context=1,
            ignore_vcs=True,
        )
    )
    assert result["matches"][0]["path"] == "sub/a.txt"
    assert result["matches"][0]["line"] == 2
    assert result["matches"][0]["before"] == ["hello"]
    assert result["truncated"] is False


def test_bearer_guard(config: HostConfig) -> None:
    from personal_agent_host.app import build_app
    from starlette.testclient import TestClient

    with patch(
        "personal_agent_host.jobs.JobManager.start", new=AsyncMock()
    ), TestClient(build_app(config)) as client:
        assert client.post("/mcp", headers={"Host": "spark-host"}).status_code == 401
        denied = client.post(
            "/mcp",
            headers={"Authorization": "Bearer wrong", "Host": "spark-host"},
        )
        assert denied.status_code == 401
        rejected = client.post(
            "/mcp",
            headers={"Authorization": f"Bearer {TOKEN}", "Host": "evil.example"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        assert rejected.status_code == 421


def test_root_descriptors_only_use_explicit_connections(config: HostConfig) -> None:
    from personal_agent_host.server import root_descriptors
    roots = root_descriptors(config)
    assert roots[0]["corpus"] == {"space_id": "demo", "connection_id": "main"}
    assert roots[1]["corpus"] == {"space_id": "demo", "connection_id": "frozen"}
