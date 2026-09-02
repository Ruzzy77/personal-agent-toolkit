#!/usr/bin/env python3
"""Small local client for unattended Journal reads and monitoring ingest."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_SERVICE_URL = "https://personal-agent-journal.hiyaq77.workers.dev"
KEYCHAIN_SERVICE = "personal-agent-journal-ingest"


def _token() -> str:
    direct = os.environ.get("JOURNAL_INGEST_TOKEN", "").strip()
    if direct:
        return direct
    result = subprocess.run(
        [
            "security",
            "find-generic-password",
            "-a",
            os.environ.get("USER", ""),
            "-s",
            KEYCHAIN_SERVICE,
            "-w",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    value = result.stdout.strip()
    if result.returncode != 0 or not value:
        raise RuntimeError(
            f"Journal ingest credential is missing from Keychain service {KEYCHAIN_SERVICE}"
        )
    return value


def _request(path: str, *, method: str = "GET", body: Any | None = None) -> Any:
    base = os.environ.get("JOURNAL_SERVICE_URL", DEFAULT_SERVICE_URL).rstrip("/")
    payload = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        f"{base}{path}",
        data=payload,
        method=method,
        headers={
            "Authorization": f"Bearer {_token()}",
            "User-Agent": "PersonalAgentJournal/0.2",
            **({"Content-Type": "application/json"} if payload is not None else {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            envelope = json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Journal request failed ({error.code}): {detail}"
        ) from error
    if not envelope.get("ok"):
        raise RuntimeError(
            json.dumps(envelope.get("error", envelope), ensure_ascii=False)
        )
    return envelope.get("result")


def _read_input(path: str) -> Any:
    if path == "-":
        return json.load(sys.stdin)
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Personal Agent Journal client")
    subparsers = parser.add_subparsers(dest="command", required=True)

    board = subparsers.add_parser("board")
    board.add_argument("--week")
    board.add_argument("--include-resolved", action="store_true")

    ingest = subparsers.add_parser("ingest")
    ingest.add_argument("--input", required=True, help="JSON file or - for stdin")

    period = subparsers.add_parser("period")
    period.add_argument(
        "--kind", choices=["day", "week", "month", "quarter", "year"], required=True
    )
    period.add_argument("--anchor")

    subparsers.add_parser("health")
    args = parser.parse_args()

    if args.command == "health":
        url = os.environ.get("JOURNAL_SERVICE_URL", DEFAULT_SERVICE_URL).rstrip("/")
        request = urllib.request.Request(
            f"{url}/health", headers={"User-Agent": "PersonalAgentJournal/0.2"}
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            result = json.load(response)
    elif args.command == "board":
        query = {"include_resolved": "true" if args.include_resolved else "false"}
        if args.week:
            query["week"] = args.week
        result = _request(f"/api/v1/board?{urllib.parse.urlencode(query)}")
    elif args.command == "ingest":
        value = _read_input(args.input)
        if isinstance(value, list):
            value = {"items": value}
        result = _request("/api/v1/items:ingest", method="POST", body=value)
    else:
        query = {"kind": args.kind}
        if args.anchor:
            query["anchor"] = args.anchor
        result = _request(f"/api/v1/period?{urllib.parse.urlencode(query)}")

    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from error
