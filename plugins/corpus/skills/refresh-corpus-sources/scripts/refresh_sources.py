#!/usr/bin/env python3
"""Refresh registered local Corpus source indexes in bounded passes."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

MAX_FILES = 50
MAX_BYTES = 500 * 1024 * 1024
MAX_FILE_BYTES = 250 * 1024 * 1024
TIMEOUT_SECONDS = 600
MAX_PASSES = 4


class CorpusCommandError(RuntimeError):
    """Raised when the bundled Corpus command cannot return a successful result."""


def _short(value: str, limit: int = 1200) -> str:
    value = value.strip()
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def _run_json(launcher: Path, arguments: list[str], *, timeout: int) -> dict[str, Any]:
    command = [str(launcher), *arguments]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise CorpusCommandError(
            f"Corpus command timed out after {timeout} seconds: {' '.join(arguments)}"
        ) from exc
    except OSError as exc:
        raise CorpusCommandError(f"Corpus command could not start: {exc}") from exc

    output = completed.stdout.strip()
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        detail = _short(completed.stderr or output or "no output")
        raise CorpusCommandError(f"Corpus returned invalid JSON: {detail}") from exc

    if completed.returncode != 0 or not payload.get("ok"):
        error = payload.get("error") or completed.stderr or payload
        if not isinstance(error, str):
            error = json.dumps(error, ensure_ascii=False, sort_keys=True)
        raise CorpusCommandError(_short(error))

    result = payload.get("result")
    if not isinstance(result, dict):
        raise CorpusCommandError("Corpus returned no result object")
    return result


def _warning_counts(
    pending: dict[str, Any], coverage: dict[str, Any]
) -> dict[str, int]:
    values = {
        "pending_remote": int(pending.get("pending_remote", 0)),
        "too_large": int(pending.get("too_large", 0)),
        "current_failures": int(pending.get("failed", 0)),
        "coverage_gaps": int(pending.get("coverage_gaps", 0)),
        "partial_active_projections": int(
            coverage.get("partial_active_projections", 0)
        ),
        "supported_without_usable_projection": int(
            coverage.get("supported_documents_without_usable_projection", 0)
        ),
    }
    return {name: count for name, count in values.items() if count}


def _refresh_one(launcher: Path, corpus: dict[str, Any]) -> dict[str, Any]:
    corpus_id = str(corpus["corpus_id"])
    source_root = Path(str(corpus["source_root"])).expanduser()
    report: dict[str, Any] = {
        "corpus_id": corpus_id,
        "source_root": str(source_root),
        "ok": False,
        "passes": 0,
        "changes": {
            "added": 0,
            "changed": 0,
            "reappeared": 0,
            "deleted": 0,
            "indexed": 0,
        },
        "errors": [],
    }

    if not source_root.is_dir():
        report["errors"].append("registered source root is unavailable")
        return report

    last_sync: dict[str, Any] | None = None
    for pass_number in range(1, MAX_PASSES + 1):
        print(
            f"Refreshing {corpus_id} (pass {pass_number}/{MAX_PASSES})",
            file=sys.stderr,
            flush=True,
        )
        try:
            last_sync = _run_json(
                launcher,
                [
                    "sync",
                    "--corpus",
                    corpus_id,
                    "--max-files",
                    str(MAX_FILES),
                    "--max-bytes",
                    str(MAX_BYTES),
                    "--max-file-bytes",
                    str(MAX_FILE_BYTES),
                    "--timeout-seconds",
                    str(TIMEOUT_SECONDS),
                ],
                timeout=TIMEOUT_SECONDS + 60,
            )
        except CorpusCommandError as exc:
            report["errors"].append(f"sync failed: {exc}")
            break

        report["passes"] = pass_number
        summary = last_sync.get("summary", {})
        for key in report["changes"]:
            report["changes"][key] += int(summary.get(key, 0))
        if int(last_sync.get("pending", {}).get("refreshable", 0)) == 0:
            break

    status: dict[str, Any] | None = None
    try:
        status = _run_json(
            launcher,
            ["status", "--corpus", corpus_id],
            timeout=60,
        )
    except CorpusCommandError as exc:
        report["errors"].append(f"status check failed: {exc}")

    pending = dict((last_sync or {}).get("pending", {}))
    inventory = dict((last_sync or {}).get("inventory", {}))
    coverage = dict((status or {}).get("coverage_gaps", {}))
    latest_scan = (status or {}).get("latest_scan") or {}

    report.update(
        {
            "sync_state": (last_sync or {}).get("state"),
            "source_state": (status or last_sync or {}).get("source_state"),
            "inventory_complete": bool(inventory.get("inventory_complete")),
            "latest_scan_status": latest_scan.get("status"),
            "pending": pending,
            "coverage_gaps": coverage,
            "warnings": _warning_counts(pending, coverage),
        }
    )

    if last_sync is None:
        if not report["errors"]:
            report["errors"].append("no synchronization result was produced")
    else:
        if not report["inventory_complete"]:
            report["errors"].append("source scan did not complete")
        if int(pending.get("refreshable", 0)):
            report["errors"].append(
                f"{int(pending['refreshable'])} locally refreshable document(s) remain"
            )

    if status is not None:
        if report["latest_scan_status"] != "complete":
            report["errors"].append("latest source scan is not complete")
        outdated = int(coverage.get("outdated_active_projections", 0))
        if outdated:
            reasons = pending.get("outdated", {})
            explained = sum(
                int(reasons.get(key, 0))
                for key in ("too_large", "pending_remote", "failed")
            )
            if int(reasons.get("total", -1)) != outdated or explained != outdated:
                report["errors"].append(
                    f"{outdated} outdated active projection(s) are not fully explained by blocked documents"
                )
            else:
                report["warnings"]["outdated_blocked_projections"] = outdated

    report["ok"] = not report["errors"]
    return report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Refresh registered local Corpus source indexes in bounded passes."
    )
    parser.add_argument(
        "--corpus",
        action="append",
        default=[],
        dest="corpus_ids",
        metavar="SOURCE_ID",
        help="Refresh only this registration; repeat to select more than one.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Indent the final JSON report.",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    plugin_root = Path(__file__).resolve().parents[3]
    launcher = plugin_root / "launchers" / "corpus"

    try:
        listed = _run_json(launcher, ["corpus", "list"], timeout=60)
    except CorpusCommandError as exc:
        report = {"ok": False, "errors": [f"could not list registrations: {exc}"]}
        print(json.dumps(report, ensure_ascii=False, indent=2 if args.pretty else None))
        return 2

    corpora = listed.get("corpora", [])
    if not isinstance(corpora, list):
        report = {
            "ok": False,
            "errors": ["Corpus returned an invalid registration list"],
        }
        print(json.dumps(report, ensure_ascii=False, indent=2 if args.pretty else None))
        return 2

    by_id = {
        str(item.get("corpus_id")): item for item in corpora if item.get("corpus_id")
    }
    requested = list(dict.fromkeys(args.corpus_ids))
    missing = [corpus_id for corpus_id in requested if corpus_id not in by_id]
    if missing:
        report = {
            "ok": False,
            "errors": ["unknown Corpus registration(s): " + ", ".join(missing)],
            "available_corpora": sorted(by_id),
        }
        print(json.dumps(report, ensure_ascii=False, indent=2 if args.pretty else None))
        return 2

    selected = [by_id[corpus_id] for corpus_id in requested] if requested else corpora
    results = [_refresh_one(launcher, corpus) for corpus in selected]

    totals = {
        "corpora_checked": len(results),
        "corpora_completed": sum(bool(item["ok"]) for item in results),
        "corpora_failed": sum(not bool(item["ok"]) for item in results),
        "passes": sum(int(item["passes"]) for item in results),
        "added": sum(int(item["changes"]["added"]) for item in results),
        "changed": sum(int(item["changes"]["changed"]) for item in results),
        "reappeared": sum(int(item["changes"]["reappeared"]) for item in results),
        "deleted": sum(int(item["changes"]["deleted"]) for item in results),
        "indexed": sum(int(item["changes"]["indexed"]) for item in results),
    }
    report = {
        "ok": totals["corpora_failed"] == 0,
        "selection": requested or "all",
        "limits": {
            "max_files_per_pass": MAX_FILES,
            "max_bytes_per_pass": MAX_BYTES,
            "max_file_bytes": MAX_FILE_BYTES,
            "timeout_seconds": TIMEOUT_SECONDS,
            "max_passes_per_corpus": MAX_PASSES,
            "include_remote": False,
        },
        "summary": totals,
        "corpora": results,
    }
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2 if args.pretty else None,
            sort_keys=args.pretty,
        )
    )
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
