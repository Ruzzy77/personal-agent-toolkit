#!/usr/bin/env python3
"""Trial driver: prepare a run, then judge it from the stored state."""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import time

spec = importlib.util.spec_from_file_location("fixture", pathlib.Path(__file__).with_name("fixture.py"))
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
RUNS = pathlib.Path(__file__).with_name("runs.jsonl")
MARK_W2 = fixture.MARK_W2
MARK_X2 = fixture.MARK_X2


def judge(case: str, answer: str) -> tuple[bool, dict]:
    space, doc = fixture.SPACE, fixture.call
    if case == "W1":
        body = fixture.host("cat ~/mcp-surface-test/workspace/notes/result.txt 2>/dev/null")
        return body.strip() == "surface experiment ok", {"content": body}
    if case == "W2":
        return MARK_W2 in answer, {"answer": answer[:200]}
    if case == "C1":
        got = doc("corpus_document_read", {"space_id": space, "document_id": "surface-summary"})
        body = got.get("result", {}).get("document", {}).get("body_markdown", "")
        title = got.get("result", {}).get("document", {}).get("title", "")
        return got.get("ok") and "one paragraph about the experiment" in body and title == "Surface summary", {"body": body[:200]}
    if case == "C2":
        got = doc("corpus_document_read", {"space_id": space, "document_id": "fixture-note"})
        body = got.get("result", {}).get("document", {}).get("body_markdown", "")
        ok = "revised-value" in body and "baseline line" in body and "tail line" in body and MARK_X2 not in body
        return ok, {"body": body[:220]}
    if case == "H1":
        graph = doc("hypes_read", {"max_hops": 0, "limit": 50})
        nodes = {n["name"]: n.get("description") for n in graph.get("result", {}).get("nodes", [])}
        ok = nodes.get("other-node") == "changed by task" and nodes.get("fixture-note") == "keep this description"
        return ok, {"nodes": nodes}
    if case == "M1":
        got = doc("corpus_document_read", {"space_id": space, "document_id": "restore-me"})
        other = doc("corpus_document_read", {"space_id": space, "document_id": "fixture-note"})
        return got.get("ok") and other.get("ok"), {"restored": got.get("ok"), "fixture_note": other.get("ok")}
    if case == "X1":
        got = doc("corpus_document_read", {"space_id": space, "document_id": "fixture-note"})
        body = got.get("result", {}).get("document", {}).get("body_markdown", "")
        graph = doc("hypes_read", {"max_hops": 0, "limit": 50})
        nodes = {n["name"]: n.get("description") for n in graph.get("result", {}).get("nodes", [])}
        ok = "edited line" in body and nodes.get("fixture-note") == "keep this description"
        return ok, {"body": body[:160], "hypes": nodes}
    if case == "X2":
        body = fixture.host("cat ~/mcp-surface-test/workspace/notes/copied.txt 2>/dev/null")
        return MARK_X2 in body, {"content": body[:120]}
    return False, {"error": "unknown case"}


if __name__ == "__main__":
    command = sys.argv[1]
    if command == "pre":
        fixture.reset()
        print(json.dumps({"started": time.time()}))
    elif command == "post":
        case, surface, seconds = sys.argv[2], sys.argv[3], float(sys.argv[4])
        answer = sys.argv[5] if len(sys.argv) > 5 else ""
        passed, detail = judge(case, answer)
        record = {"case": case, "surface": surface, "seconds": round(seconds, 1),
                  "passed": passed, "detail": detail, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        with RUNS.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(json.dumps({"case": case, "surface": surface, "passed": passed, "seconds": record["seconds"], "detail": detail}, ensure_ascii=False))
