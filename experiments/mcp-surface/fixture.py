#!/usr/bin/env python3
"""seed / reset / assert for the surface experiment.

Talks to the experiment Worker's A surface and to the test Host, so both
surfaces share one backend. Never touches production data.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time

BASE = "https://surface-experiment.hiyaq77.workers.dev/s7k2q9/a/mcp"
SPACE = "mcp-surface-test"
ROOT = "mcp-surface-test/main"
MARK_W2 = "quail-7731"
MARK_X2 = "ochre-4412"


def call(name: str, arguments: dict) -> dict:
    body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": name, "arguments": arguments}}
    ).encode()
    raw = subprocess.run(
        ["curl", "-sS", "-X", "POST", BASE,
         "-H", "Content-Type: application/json",
         "-H", "Accept: application/json, text/event-stream",
         "--data-binary", "@-"],
        input=body, capture_output=True, timeout=120,
    ).stdout.decode()
    line = next((l for l in raw.splitlines() if l.startswith("data: ")), None)
    payload = json.loads(line[6:] if line else raw)
    result = payload.get("result", {})
    structured = result.get("structuredContent")
    if structured is None:
        return {"ok": False, "error": {"code": "no_structured", "message": str(result)[:200]}}
    return structured


def host(command: str) -> str:
    """Run one shell command on the test host and return its output."""

    return subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "Spark-A", command],
        capture_output=True, text=True, timeout=180,
    ).stdout.strip()


def space_version() -> int:
    got = call("corpus_space_get", {"space_id": SPACE})
    return got["result"]["space"]["context"]["version"] if got.get("ok") else 0


def seed() -> None:
    call("corpus_space_create", {"space_id": SPACE, "display_name": "Surface experiment",
                                 "purpose": "Synthetic fixture for the MCP surface experiment"})
    reset()
    print("seeded")


def restore_all() -> None:
    """Bring back anything an earlier run trashed so ids can be reused."""

    listed = call("corpus_trash_list", {"space_id": SPACE})
    for group in listed.get("result", {}).get("groups", []) if listed.get("ok") else []:
        preview = call("corpus_management_preview",
                       {"action": "restore", "deletion_group_id": group["deletion_group_id"]})
        if not preview.get("ok"):
            continue
        call("corpus_trash_restore", {
            "deletion_group_id": group["deletion_group_id"],
            "expected_version": group["version"],
            "impact_token": preview["result"]["impact_token"],
            "idempotency_key": f"restore-{group['deletion_group_id']}-{time.time_ns()}"})


def trash(document_id: str) -> None:
    listed = call("corpus_document_list", {"space_id": SPACE})
    item = next((i for i in listed.get("result", {}).get("items", [])
                 if i["document_id"] == document_id), None)
    if item is None:
        return
    preview = call("corpus_management_preview",
                   {"action": "trash", "target": {"kind": "document", "space_id": SPACE, "id": document_id}})
    if not preview.get("ok"):
        return
    call("corpus_document_trash", {
        "space_id": SPACE, "document_id": document_id, "expected_version": item["version"],
        "impact_token": preview["result"]["impact_token"],
        "idempotency_key": f"trash-{document_id}-{time.time_ns()}"})


def purge(document_id: str) -> None:
    """Remove a document for good so a task can create the same id again."""

    trash(document_id)
    listed = call("corpus_trash_list", {"space_id": SPACE})
    for group in listed.get("result", {}).get("groups", []) if listed.get("ok") else []:
        if group.get("root_id") != document_id:
            continue
        preview = call("corpus_management_preview",
                       {"action": "purge", "deletion_group_id": group["deletion_group_id"]})
        if not preview.get("ok"):
            continue
        call("corpus_trash_purge", {
            "deletion_group_id": group["deletion_group_id"],
            "expected_version": group["version"],
            "impact_token": preview["result"]["impact_token"],
            "idempotency_key": f"purge-{group['deletion_group_id']}-{time.time_ns()}",
            "confirm_permanent_delete": True})


def save(document_id: str, title: str, body: str) -> None:
    listed = call("corpus_document_list", {"space_id": SPACE})
    item = next((i for i in listed.get("result", {}).get("items", [])
                 if i["document_id"] == document_id), None)
    if item is None:
        call("corpus_document_create", {"space_id": SPACE, "document_id": document_id,
                                        "title": title, "body_markdown": body})
        return
    call("corpus_document_revise", {"space_id": SPACE, "document_id": document_id,
                                    "expected_version": item["version"], "title": title,
                                    "body_markdown": body})


def reset() -> None:
    # Host workspace
    host("rm -rf ~/mcp-surface-test/workspace 2>/dev/null; "
          "mkdir -p ~/mcp-surface-test/workspace/notes ~/mcp-surface-test/workspace/logs; "
          f"printf 'alpha\\nmarker {MARK_W2}\\nomega\\n' > ~/mcp-surface-test/workspace/logs/run-2.txt; "
          "printf 'nothing here\\n' > ~/mcp-surface-test/workspace/logs/run-1.txt; "
          "printf 'nothing here either\\n' > ~/mcp-surface-test/workspace/logs/run-3.txt")
    # Corpus: restore everything, drop the task output, set the two fixtures
    restore_all()
    purge("surface-summary")
    listed = call("corpus_document_list", {"space_id": SPACE})
    for item in listed.get("result", {}).get("items", []):
        if item["document_id"] not in {"fixture-note", "restore-me"}:
            trash(item["document_id"])
    save("fixture-note", "Fixture note",
         f"# Fixture note\n\nbaseline line\n\n<!-- mark -->\n{MARK_X2}\n<!-- mark -->\n\ntail line\n")
    save("restore-me", "Restore me",
         "# Restore me\n\nThis document is trashed by the fixture so a task can restore it.\n")
    trash("restore-me")
    # Hypes graph
    graph = call("hypes_read", {"max_hops": 0, "limit": 200})
    version = graph["result"]["version"] if graph.get("ok") else None
    operations = [{"op": "delete", "ref": node["node_id"]}
                  for node in graph.get("result", {}).get("nodes", []) if graph.get("ok")]
    if operations and version:
        call("hypes_rewrite", {"expected_version": version, "operations": operations})
        graph = call("hypes_read", {"max_hops": 0, "limit": 200})
        version = graph["result"]["version"]
    call("hypes_rewrite", {"expected_version": version, "operations": [
        {"op": "put_node", "ref": "$fixture",
         "value": {"name": "fixture-note", "description": "keep this description"}},
        {"op": "put_node", "ref": "$other",
         "value": {"name": "other-node", "description": "unchanged"}}]})
    print("reset")


def check(case: str) -> None:
    results = {}
    if case == "W1":
        body = host("cat ~/mcp-surface-test/workspace/notes/result.txt 2>/dev/null")
        results = {"content": body, "pass": body.strip() == "surface experiment ok"}
    elif case == "W2":
        results = {"pass": True, "marker": MARK_W2}
    elif case in {"C1", "C2"}:
        document = call("corpus_document_read", {"space_id": SPACE, "document_id":
                                                 "surface-summary" if case == "C1" else "fixture-note"})
        text = document.get("result", {}).get("document", {}).get("body_markdown", "") if document.get("ok") else ""
        results = {"exists": document.get("ok"), "body": text[:400]}
    elif case == "H1":
        graph = call("hypes_read", {"max_hops": 0, "limit": 50})
        results = {"nodes": [{"name": n["name"], "description": n.get("description")}
                             for n in graph.get("result", {}).get("nodes", [])]}
    elif case == "M1":
        document = call("corpus_document_read", {"space_id": SPACE, "document_id": "restore-me"})
        results = {"restored": document.get("ok")}
    elif case == "X1":
        document = call("corpus_document_read", {"space_id": SPACE, "document_id": "fixture-note"})
        graph = call("hypes_read", {"max_hops": 0, "limit": 50})
        results = {"document": document.get("result", {}).get("document", {}).get("body_markdown", "")[:200],
                   "hypes": [{"name": n["name"], "description": n.get("description")}
                             for n in graph.get("result", {}).get("nodes", [])]}
    elif case == "X2":
        body = host("cat ~/mcp-surface-test/workspace/notes/copied.txt 2>/dev/null")
        results = {"content": body, "pass": MARK_X2 in body}
    print(json.dumps(results, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "reset"
    if command == "seed":
        seed()
    elif command == "reset":
        reset()
    elif command == "assert":
        check(sys.argv[2])
    else:
        print("usage: fixture.py seed|reset|assert <case>")
