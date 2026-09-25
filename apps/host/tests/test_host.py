"""Host file tools: path safety, versions, marker replacement, and the HTTP guard."""

from __future__ import annotations

import asyncio
import base64
import json
import shutil
import sys
from pathlib import Path
from dataclasses import replace
from unittest.mock import AsyncMock, patch

import pytest
from mcp.server.mcpserver.exceptions import ToolError as McpToolError
from personal_agent_host.config import HostConfig, FlowPolicy, load_host_config
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
execute = "host"

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
    assert config.root("demo/main").execute == "host"
    assert config.root("demo/frozen").permission == "read_only"
    with pytest.raises(SyncError):
        config.root("demo/missing")


def test_host_service_stops_only_the_main_process(config: HostConfig) -> None:
    from personal_agent_host.cli import _unit_files

    unit = _unit_files(config, config.prefix / "config" / "host.toml")[
        "personal-agent-host.service"
    ]
    assert "KillMode=process" in unit


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
        "files": [
            {
                "path": "empty.md",
                "content": "",
                "version": created["version"],
                "start_line": 1,
                "end_line": 0,
            }
        ],
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


def _workspace_config(
    tmp_path: Path, *, guard: str, permission: str = "read_write"
) -> HostConfig:
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
execute = "host"
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
    assert (root.permission, root.execute) == ("read_write", "host")
    written = write_file(config, root, "new-project/notes.md", content="one\n")
    assert written["path"] == "new-project/notes.md"


def test_protected_source_stays_read_only_inside_a_writable_root(
    tmp_path: Path,
) -> None:
    guard = tmp_path / "workspace" / "work" / "regulations" / "current"
    config = _workspace_config(tmp_path, guard=str(guard))
    root = config.root("workspace")
    assert (
        read_files(root, [{"path": "work/regulations/current/rule.txt"}], 1024)[
            "files"
        ][0]["content"]
        == "fixed\n"
    )
    for attempt in (
        {"content": "changed\n"},
        {"delete": True},
    ):
        with pytest.raises(ToolError) as failure:
            write_file(config, root, "work/regulations/current/rule.txt", **attempt)
        assert failure.value.code == "policy_denied"
    with pytest.raises(ToolError) as created:
        write_file(config, root, "work/regulations/current/added.txt", content="x")
    assert created.value.code == "policy_denied"


def test_root_inside_a_protected_source_is_read_only(tmp_path: Path) -> None:
    config = _workspace_config(tmp_path, guard=str(tmp_path / "workspace"))
    root = config.root("workspace")
    assert (root.permission, root.execute) == ("read_only", "none")


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

    with (
        patch("personal_agent_host.jobs.JobManager.start", new=AsyncMock()),
        TestClient(build_app(config)) as client,
    ):
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



def test_flow_image_import_stays_in_registered_writable_root(config: HostConfig, monkeypatch) -> None:
    import asyncio
    from personal_agent_host import flow
    from personal_agent_host.files import file_version

    configured = replace(config, flows=(FlowPolicy("main", "demo/main", "http://127.0.0.1:4176"),))
    calls = []
    png = b"\x89PNG\r\n\x1a\n" + b"image bytes"
    photo = configured.root("demo/main").root / "photos" / "sample.png"
    photo.parent.mkdir(parents=True, exist_ok=True)
    photo.write_bytes(png)
    version = file_version(png)

    class Reply:
        def __enter__(self): return self
        def __exit__(self, *_): return None
        def read(self, _limit): return b'{"src":"/api/flow/assets/' + b"a" * 64 + b'.png"}'

    class Opener:
        def open(self, request, timeout):
            calls.append((request.full_url, request.get_method(), request.data,
                          request.get_header("Content-type"),
                          request.get_header("X-toolkit-flow-workspace-id"), timeout))
            return Reply()

    monkeypatch.setattr(flow, "_OPENER", Opener())
    registered = flow.list_registered(configured)["workspaces"][0]
    assert registered["root_id"] == "demo/main"
    with pytest.raises(ToolError) as denied:
        asyncio.run(flow.import_asset(configured, "main", "demo/frozen", "photos/sample.png", version))
    assert denied.value.code == "policy_denied"
    with pytest.raises(ToolError) as hidden:
        asyncio.run(flow.import_asset(configured, "main", "demo/main", ".ssh/sample.png", version))
    assert hidden.value.code == "invalid_path"
    assert calls == []
    with pytest.raises(ToolError) as stale:
        asyncio.run(flow.import_asset(configured, "main", "demo/main", "photos/sample.png", "sha256:" + "0" * 64))
    assert stale.value.code == "version_conflict"
    assert calls == []
    result = asyncio.run(flow.import_asset(configured, "main", "demo/main", "photos/sample.png", version))
    assert result["src"].startswith("/api/flow/assets/")
    assert calls == [("http://127.0.0.1:4176/api/flow/assets", "POST", png, "image/png", "main", 15)]
    frozen = replace(configured, flows=(FlowPolicy("frozen", "demo/frozen", "http://127.0.0.1:4176"),))
    with pytest.raises(ToolError) as denied:
        asyncio.run(flow.import_asset(frozen, "frozen", "demo/frozen", "photos/sample.png", version))
    assert denied.value.code == "policy_denied"
    assert len(calls) == 1



def test_flow_work_create_accepts_saved_source_without_client_artifact(config: HostConfig, monkeypatch) -> None:
    from personal_agent_host import server as host_server

    configured = replace(config, flows=(FlowPolicy("main", "demo/main", "http://127.0.0.1:4176"),))
    calls = []

    async def fake_call_flow(config, workspace_id, method, path, payload=None, query=None):
        calls.append((workspace_id, method, path, payload))
        return {"work": {"id": "new-work"}}

    monkeypatch.setattr(host_server, "call_flow", fake_call_flow)
    server = host_server.create_server(configured, jobs=object())

    async def check() -> None:
        result = await server.call_tool("flow_work_create", {
            "workspace_id": "main", "name": "새 작업", "source_id": "snapshot-1",
            "idempotency_key": "create-source-1",
        })
        assert result.structured_content == {"work": {"id": "new-work"}}
        assert calls == [("main", "POST", "works", {
            "workspaceId": "main", "name": "새 작업", "idempotencyKey": "create-source-1",
            "sourceId": "snapshot-1",
        })]
        reference = {"kind": "journal-item", "id": "123e4567-e89b-42d3-a456-426614174000"}
        await server.call_tool("flow_work_create", {
            "workspace_id": "main", "name": "기록 검토",
            "artifact": {"kind": "content"}, "linked_resources": [reference],
            "idempotency_key": "create-resource-1",
        })
        assert calls[1] == ("main", "POST", "works", {
            "workspaceId": "main", "name": "기록 검토", "idempotencyKey": "create-resource-1",
            "artifact": {"kind": "content"}, "linkedResources": [reference],
        })
        for invalid in ({"artifact": {"kind": "document"}, "source_id": "snapshot-1"},):
            with pytest.raises(McpToolError) as failure:
                await server.call_tool("flow_work_create", {
                    "workspace_id": "main", "name": "새 작업",
                    "idempotency_key": "create-source-2", **invalid,
                })
            assert isinstance(failure.value.__cause__, ToolError)
            assert failure.value.__cause__.code == "invalid_request"
        assert len(calls) == 2

    asyncio.run(check())

def test_flow_saved_work_reads_use_registered_workspace(config: HostConfig, monkeypatch) -> None:
    from personal_agent_host import server as host_server

    configured = replace(config, flows=(FlowPolicy("main", "demo/main", "http://127.0.0.1:4176"),))
    calls = []

    async def fake_call_flow(config, workspace_id, method, path, payload=None, query=None):
        calls.append((workspace_id, method, path, query))
        return {"sources": [], "nextOffset": None} if path == "snapshots" else {"source": {"id": "snapshot:one"}}

    monkeypatch.setattr(host_server, "call_flow", fake_call_flow)
    server = host_server.create_server(configured, jobs=object())

    async def check() -> None:
        listed = await server.call_tool("flow_snapshot_list", {
            "workspace_id": "main", "query": "검사", "offset": 10, "limit": 20,
        })
        assert listed.structured_content == {"sources": [], "nextOffset": None}
        saved = await server.call_tool("flow_snapshot_read", {
            "workspace_id": "main", "source_id": "snapshot:one",
        })
        assert saved.structured_content == {"source": {"id": "snapshot:one"}}
        assert calls == [
            ("main", "GET", "snapshots", {"workspaceId": "main", "query": "검사", "offset": 10, "limit": 20}),
            ("main", "GET", "snapshots/snapshot%3Aone", {"workspaceId": "main"}),
        ]
        with pytest.raises(McpToolError):
            await server.call_tool("flow_snapshot_read", {"workspace_id": "main", "source_id": "../outside"})
        assert len(calls) == 2

    asyncio.run(check())


def test_flow_uses_only_registered_local_service_and_respects_root_permission(config: HostConfig, monkeypatch) -> None:
    from personal_agent_host import flow

    configured = replace(config, flows=(FlowPolicy("main", "demo/main", "http://127.0.0.1:4176"),))
    calls = []

    class Reply:
        def __enter__(self): return self
        def __exit__(self, *_): return None
        def read(self, _limit): return b'{"works":[]}'

    class Opener:
        def open(self, request, timeout):
            calls.append((request.full_url, request.get_method(), request.data, timeout))
            return Reply()

    monkeypatch.setattr(flow, "_OPENER", Opener())
    result = flow._request(configured, "main", "GET", "works", query={"workspaceId": "main"})
    assert result == {"works": []}
    assert calls[0][0] == "http://127.0.0.1:4176/api/flow/works?workspaceId=main"
    assert flow.list_registered(configured)["workspaces"][0]["permission"] == "read_write"
    flow._request(configured, "main", "POST", "changes/change-1/apply",
                  payload={"workspaceId": "main"})
    assert calls[1][0] == "http://127.0.0.1:4176/api/flow/changes/change-1/apply"
    assert calls[1][1] == "POST"
    assert json.loads(calls[1][2]) == {"workspaceId": "main"}
    links = [{"kind": "context", "locator": {"product": "corpus", "spaceId": "research", "documentId": "notes"}},
             {"kind": "host-file", "root": "research-note/main", "path": "notes/한글.md"}]
    flow._request(configured, "main", "PATCH", "works/work-1",
                  payload={"workspaceId": "main", "linkedResources": links})
    assert calls[2][1] == "PATCH"
    assert json.loads(calls[2][2])["linkedResources"] == links
    flow._request(configured, "main", "POST", "snapshots", payload={
        "workspaceId": "main", "workId": "work-1", "artifactId": "artifact-1",
        "expectedRevision": 2, "idempotencyKey": "save-attempt-1",
    })
    assert calls[3][0] == "http://127.0.0.1:4176/api/flow/snapshots"
    assert json.loads(calls[3][2])["expectedRevision"] == 2
    with pytest.raises(ToolError):
        flow.path_id("other/change")
    with pytest.raises(ToolError):
        flow._request(configured, "unknown", "GET", "works")
    frozen = replace(configured, flows=(FlowPolicy("frozen", "demo/frozen", "http://127.0.0.1:4176"),))
    with pytest.raises(ToolError) as denied:
        flow._request(frozen, "frozen", "POST", "changes/change-1/apply", payload={})
    assert denied.value.code == "policy_denied"
    assert len(calls) == 4

def test_flow_library_calls_keep_scope_and_revision(config: HostConfig, monkeypatch) -> None:
    from personal_agent_host.server import create_server
    calls = []
    async def fake_call_flow(config, workspace_id, method, path, payload=None, query=None):
        calls.append((workspace_id, method, path, payload, query))
        return {"ok": True}
    monkeypatch.setattr("personal_agent_host.server.call_flow", fake_call_flow)
    server = create_server(config, jobs=object())
    async def check():
        await server.call_tool("flow_library_list", {"workspace_id":"main","work_id":"work","query":"보고서","limit":20})
        await server.call_tool("flow_library_read", {"workspace_id":"main","entry_id":"entry:one"})
        entry={"id":"entry:one","title":"자료","body":"참고 내용","scope":{"kind":"work","workId":"work"}}
        await server.call_tool("flow_library_upsert", {"workspace_id":"main","entry":entry,"expected_revision":3,"idempotency_key":"library-update-1"})
        assert calls[0]==("main","GET","library",None,{"workspaceId":"main","workId":"work","query":"보고서","offset":0,"limit":20})
        assert calls[1]==("main","GET","library/entry%3Aone",None,{"workspaceId":"main"})
        assert calls[2]==("main","POST","library",{"workspaceId":"main","entry":entry,"expectedRevision":3,"idempotencyKey":"library-update-1"},None)
        with pytest.raises(McpToolError):
            await server.call_tool("flow_library_read", {"workspace_id":"main","entry_id":"../outside"})
    asyncio.run(check())
