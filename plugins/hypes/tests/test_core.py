from __future__ import annotations

import asyncio
import stat
from pathlib import Path

import pytest

from hypes.errors import HypesError
from hypes.mcp_server import create_server
from hypes.service import HypesService
from hypes.store import UnsafeStorageError


def node(name: str, *, aliases: list[str] | None = None) -> dict:
    return {
        "labels": ["concept"],
        "name": name,
        "description": None,
        "aliases": aliases or [],
        "attributes": {},
    }


def predicate(name: str) -> dict:
    return {"name": name, "description": None, "aliases": []}


def create_relation(service: HypesService) -> dict:
    return service.rewrite(
        operations=[
            {"op": "put_node", "ref": "$a", "value": node("alpha", aliases=["first"])},
            {"op": "put_node", "ref": "$b", "value": node("bravo")},
            {"op": "put_predicate", "ref": "$p", "value": predicate("connects")},
            {
                "op": "put_edge",
                "ref": "$edge",
                "value": {
                    "source_ref": "$a",
                    "predicate_ref": "$p",
                    "target_ref": "$b",
                    "qualifiers": {},
                },
            },
        ]
    )


def test_rewrite_persists_a_closed_relationship_slice(tmp_path: Path) -> None:
    created = create_relation(HypesService(tmp_path))
    refs = created["ref_map"]
    assert created["change_summary"] == {
        "created": {"nodes": 2, "predicates": 1, "edges": 1},
        "updated": {"nodes": 0, "predicates": 0, "edges": 0},
        "deleted": {"nodes": 0, "predicates": 0, "edges": 0},
    }
    result = HypesService(tmp_path).read(
        seed_refs=[refs["$a"]],
        max_hops=1,
        limit=10,
    )

    assert {item["node_id"] for item in result["nodes"]} == {refs["$a"], refs["$b"]}
    assert {item["predicate_id"] for item in result["predicates"]} == {refs["$p"]}
    assert {item["edge_id"] for item in result["edges"]} == {refs["$edge"]}
    edge = result["edges"][0]
    assert edge["source_id"] in {item["node_id"] for item in result["nodes"]}
    assert edge["target_id"] in {item["node_id"] for item in result["nodes"]}
    assert edge["predicate_id"] in {item["predicate_id"] for item in result["predicates"]}


def test_failed_rewrite_rolls_back_the_whole_patch(tmp_path: Path) -> None:
    service = HypesService(tmp_path)
    with pytest.raises(HypesError):
        service.rewrite(
            operations=[
                {"op": "put_node", "ref": "$new", "value": node("rollback-marker")},
                {
                    "op": "put_edge",
                    "ref": "$edge",
                    "value": {
                        "source_ref": "$new",
                        "predicate_ref": "pred_" + "0" * 32,
                        "target_ref": "$new",
                        "qualifiers": {},
                    },
                },
            ]
        )
    assert service.read(focus="rollback-marker")["nodes"] == []
    assert service.read()["nodes"] == []


def test_replace_and_delete_keep_explicit_graph_integrity(tmp_path: Path) -> None:
    service = HypesService(tmp_path)
    refs = create_relation(service)["ref_map"]
    service.rewrite(
        operations=[
            {
                "op": "put_node",
                "ref": refs["$a"],
                "value": node("renamed", aliases=["updated"]),
            }
        ]
    )
    assert service.read(focus="updated", max_hops=0)["nodes"][0]["node_id"] == refs["$a"]

    with pytest.raises(HypesError):
        service.rewrite(operations=[{"op": "delete", "ref": refs["$a"]}])

    service.rewrite(
        operations=[
            {"op": "delete", "ref": refs["$edge"]},
            {"op": "delete", "ref": refs["$a"]},
        ]
    )
    assert service.read(focus="updated")["nodes"] == []


def test_outline_pagination_uses_one_continuation_cursor(tmp_path: Path) -> None:
    service = HypesService(tmp_path)
    service.rewrite(
        operations=[
            {"op": "put_node", "ref": f"$n{index}", "value": node(name)}
            for index, name in enumerate(("alpha", "bravo", "charlie"))
        ]
    )

    first = service.read(limit=2)
    second = service.read(limit=2, continuation=first["continuation"])
    names = {item["name"] for item in first["nodes"] + second["nodes"]}
    assert names == {"alpha", "bravo", "charlie"}
    assert second["continuation"] is None


def test_private_storage_modes_and_symlink_boundary(tmp_path: Path) -> None:
    root = tmp_path / "Hypes"
    HypesService(root).read()
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE((root / "hypes-ontology.sqlite3").stat().st_mode) == 0o600

    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    link = tmp_path / "linked-hypes"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(UnsafeStorageError, match="symbolic link"):
        HypesService(link).read()
    assert list(target.iterdir()) == []


def test_mcp_keeps_two_tools_and_returns_the_direct_read_shape(tmp_path: Path) -> None:
    server = create_server(tmp_path / "Hypes")
    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}
    assert set(tools) == {"hypes_read", "hypes_rewrite"}

    result = asyncio.run(server.call_tool("hypes_read", {"focus": "missing"}))
    assert result.structured_content["ok"] is True
    assert set(result.structured_content["result"]) == {
        "nodes",
        "predicates",
        "edges",
        "continuation",
    }

    rejected = asyncio.run(
        server.call_tool("hypes_read", {"focus": "missing", "unknown": "private-value"})
    )
    assert rejected.structured_content == {
        "ok": False,
        "error": {
            "code": "invalid_request",
            "message": "the Hypes request contains unsupported fields",
            "details": {},
        },
    }
    assert "private-value" not in str(rejected.structured_content)
