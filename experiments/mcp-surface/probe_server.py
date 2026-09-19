#!/usr/bin/env python3
"""Instrumented MCP server for the surface experiment.

Modes (PROBE_MODE):
  BASE   only `surface_probe`
  FULL   the frozen toolkit manifest plus `surface_probe`
  PAGED  the same list served 20 tools per page, `surface_probe` last

Product tools carry their real definitions but no behaviour: calling one
returns `probe_only`. Only `surface_probe` executes, returning a fresh receipt
so a client's call can be matched to a trial.

The log records what the client asked for, never credentials, and never goes to
stdout because stdio transports carry protocol traffic there.
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

MODE = os.environ.get("PROBE_MODE", "FULL").upper()
TRIAL = os.environ.get("PROBE_TRIAL", uuid.uuid4().hex[:8])
ROOT = Path(__file__).resolve().parent
LOG = Path(os.environ.get("PROBE_LOG", ROOT / "protocol" / f"{MODE}-{TRIAL}.jsonl"))
MANIFEST = json.loads((ROOT / "manifest.full.json").read_text(encoding="utf-8"))
PAGE_SIZE = int(os.environ.get("PROBE_PAGE_SIZE", "20"))
CONNECTION = uuid.uuid4().hex[:12]

PROBE = {
    "name": "surface_probe",
    "title": "Surface probe",
    "description": "Return a fresh receipt string. Used to mark a measurement trial.",
    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    "outputSchema": {"type": "object", "properties": {}, "additionalProperties": True},
}


def tools() -> list[dict]:
    if MODE == "BASE":
        return [PROBE]
    return [*MANIFEST["tools"], PROBE]


def record(event: str, detail: dict) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    line = {
        "trial_id": TRIAL,
        "mode": MODE,
        "connection": CONNECTION,
        "at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "event": event,
        **detail,
    }
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(line, ensure_ascii=False) + "\n")


def send(message: dict) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def page(cursor: str | None) -> tuple[list[dict], str | None]:
    everything = tools()
    if MODE != "PAGED":
        return everything, None
    start = int(cursor) if cursor else 0
    chunk = everything[start : start + PAGE_SIZE]
    following = start + PAGE_SIZE
    return chunk, (str(following) if following < len(everything) else None)


def main() -> None:
    record("start", {"pid": os.getpid(), "tool_count": len(tools())})
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            request = json.loads(raw)
        except json.JSONDecodeError:
            continue
        method = request.get("method")
        identifier = request.get("id")
        params = request.get("params") or {}
        if method == "initialize":
            record(
                "initialize",
                {
                    "request_id": identifier,
                    "protocol_version": params.get("protocolVersion"),
                    "client_info": params.get("clientInfo"),
                    "client_capabilities": params.get("capabilities"),
                },
            )
            send(
                {
                    "jsonrpc": "2.0",
                    "id": identifier,
                    "result": {
                        "protocolVersion": params.get("protocolVersion", "2025-06-18"),
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {"name": f"surface-{MODE.lower()}", "version": "1"},
                    },
                }
            )
        elif method == "tools/list":
            cursor = params.get("cursor")
            listed, following = page(cursor)
            body = {"tools": listed}
            if following is not None:
                body["nextCursor"] = following
            record(
                "tools/list",
                {
                    "request_id": identifier,
                    "cursor": cursor,
                    "returned": len(listed),
                    "names": [tool["name"] for tool in listed],
                    "bytes": len(json.dumps(listed, ensure_ascii=False).encode("utf-8")),
                    "next_cursor": following,
                },
            )
            send({"jsonrpc": "2.0", "id": identifier, "result": body})
        elif method == "tools/call":
            name = params.get("name")
            started = time.monotonic()
            if name == "surface_probe":
                receipt = uuid.uuid4().hex[:10]
                result = {
                    "content": [{"type": "text", "text": receipt}],
                    "structuredContent": {"receipt": receipt},
                    "isError": False,
                }
            else:
                result = {
                    "content": [
                        {
                            "type": "text",
                            "text": "probe_only: this surface carries definitions only",
                        }
                    ],
                    "isError": True,
                }
            record(
                "tools/call",
                {
                    "request_id": identifier,
                    "name": name,
                    "arguments": params.get("arguments"),
                    "is_error": result.get("isError", False),
                    "duration_ms": round((time.monotonic() - started) * 1000, 2),
                },
            )
            send({"jsonrpc": "2.0", "id": identifier, "result": result})
        elif method and method.startswith("notifications/"):
            record("notification", {"method": method})
        elif identifier is not None:
            record("unsupported", {"request_id": identifier, "method": method})
            send(
                {
                    "jsonrpc": "2.0",
                    "id": identifier,
                    "error": {"code": -32601, "message": f"unsupported method {method}"},
                }
            )
    record("exit", {})


if __name__ == "__main__":
    main()
