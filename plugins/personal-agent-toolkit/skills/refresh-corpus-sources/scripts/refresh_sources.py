#!/usr/bin/env python3
"""Refresh registered local Corpus source indexes in bounded passes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MAX_FILES = 50
MAX_BYTES = 500 * 1024 * 1024
MAX_FILE_BYTES = 250 * 1024 * 1024
TIMEOUT_SECONDS = 600
MAX_PASSES = 4
WARNING_STATE_SCHEMA_VERSION = 1
MAX_WARNING_STATE_BYTES = 64 * 1024 * 1024


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


def _warning_key(corpus_id: str, item: dict[str, Any]) -> str:
    identity = {
        "corpus_id": corpus_id,
        "document_id": item.get("document_id"),
        "revision_id": item.get("revision_id"),
        "adapter_id": item.get("adapter_id"),
        "adapter_version": item.get("adapter_version"),
        "config_hash": item.get("config_hash"),
        "issue_code": item.get("issue_code"),
        "impact": item.get("impact"),
        "severity": item.get("severity"),
    }
    encoded = json.dumps(
        identity,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _current_warning_items(
    corpus_id: str, items: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    current: dict[str, dict[str, Any]] = {}
    for source_item in items:
        item = dict(source_item)
        key = _warning_key(corpus_id, item)
        occurrence_count = max(1, int(item.get("occurrence_count", 1)))
        if key in current:
            current[key]["occurrence_count"] += occurrence_count
        else:
            item["occurrence_count"] = occurrence_count
            current[key] = item
    return current


def _warning_delta(
    previous: dict[str, Any],
    results: list[dict[str, Any]],
    *,
    reset: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    previous_corpora = previous.get("corpora", {})
    if not isinstance(previous_corpora, dict):
        previous_corpora = {}
    next_corpora = {
        corpus_id: dict(value)
        for corpus_id, value in previous_corpora.items()
        if isinstance(corpus_id, str) and isinstance(value, dict)
    }
    changes: dict[str, list[dict[str, Any]]] = {
        "new": [],
        "increased": [],
        "reappeared": [],
        "resolved": [],
        "decreased": [],
    }
    initialized: list[str] = []
    for result in results:
        corpus_id = str(result["corpus_id"])
        source_items = result.get("warning_items", [])
        if not isinstance(source_items, list):
            source_items = []
        current = _current_warning_items(corpus_id, source_items)
        old_entry = previous_corpora.get(corpus_id)
        old_current = (
            old_entry.get("current", {}) if isinstance(old_entry, dict) else {}
        )
        old_seen = (
            set(old_entry.get("seen", [])) if isinstance(old_entry, dict) else set()
        )
        if reset:
            old_seen = set()
        if not isinstance(old_current, dict):
            old_current = {}
        establish_baseline = reset or not isinstance(old_entry, dict)
        if establish_baseline:
            initialized.append(corpus_id)
        else:
            for key, item in current.items():
                if key not in old_current:
                    change_type = "reappeared" if key in old_seen else "new"
                    changes[change_type].append({"corpus_id": corpus_id, **item})
                    continue
                previous_count = max(
                    1, int(old_current[key].get("occurrence_count", 1))
                )
                current_count = int(item["occurrence_count"])
                if current_count > previous_count:
                    changes["increased"].append(
                        {
                            "corpus_id": corpus_id,
                            **item,
                            "previous_occurrence_count": previous_count,
                        }
                    )
                elif current_count < previous_count:
                    changes["decreased"].append(
                        {
                            "corpus_id": corpus_id,
                            **item,
                            "previous_occurrence_count": previous_count,
                        }
                    )
            for key, item in old_current.items():
                if key not in current:
                    changes["resolved"].append({"corpus_id": corpus_id, **item})
        next_corpora[corpus_id] = {
            "current": current,
            "seen": sorted(old_seen | set(current)),
        }

    state = {
        "schema_version": WARNING_STATE_SCHEMA_VERSION,
        "updated_at": datetime.now(UTC).isoformat(),
        "corpora": next_corpora,
    }
    summary = {name: len(items) for name, items in changes.items()}
    summary["active"] = sum(
        len(entry.get("current", {}))
        for entry in next_corpora.values()
        if isinstance(entry, dict)
    )
    summary["alerting_changes"] = (
        summary["new"] + summary["increased"] + summary["reappeared"]
    )
    return state, {
        "baseline_initialized": initialized,
        "reset": reset,
        "summary": summary,
        "changes": changes,
    }


def _load_warning_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": WARNING_STATE_SCHEMA_VERSION, "corpora": {}}
    if path.is_symlink() or not path.is_file():
        raise CorpusCommandError("warning state path must be a regular file")
    if path.stat().st_size > MAX_WARNING_STATE_BYTES:
        raise CorpusCommandError("warning state file exceeds the supported size")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CorpusCommandError(f"warning state could not be read: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise CorpusCommandError("warning state has an unsupported schema")
    return payload


def _write_warning_state(path: Path, state: dict[str, Any]) -> None:
    path = path.expanduser()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.exists() and path.is_symlink():
        raise CorpusCommandError("warning state path must not be a symbolic link")
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        encoded = json.dumps(
            state,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        path.chmod(0o600)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise


def _update_warning_state(
    path: Path,
    results: list[dict[str, Any]],
    *,
    reset: bool,
    refresh_ok: bool,
) -> dict[str, Any]:
    expanded = path.expanduser()
    if not refresh_ok:
        return {
            "path": str(expanded),
            "updated": False,
            "reason": "refresh_failed",
        }
    previous_state = _load_warning_state(expanded)
    next_state, delta = _warning_delta(previous_state, results, reset=reset)
    _write_warning_state(expanded, next_state)
    return {
        "path": str(expanded),
        "updated": True,
        **delta,
    }


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
    approved_large: dict[str, Any] | None = None
    if last_sync is not None:
        try:
            approved_large = _run_json(
                launcher,
                [
                    "large-document",
                    "sync",
                    "--corpus",
                    corpus_id,
                    "--max-files",
                    "1",
                    "--max-bytes",
                    str(1024 * 1024 * 1024),
                    "--timeout-seconds",
                    str(TIMEOUT_SECONDS),
                ],
                timeout=TIMEOUT_SECONDS + 60,
            )
        except CorpusCommandError as exc:
            report["errors"].append(f"approved large-document sync failed: {exc}")
    try:
        status = _run_json(
            launcher,
            [
                "status",
                "--corpus",
                corpus_id,
                "--max-file-bytes",
                str(MAX_FILE_BYTES),
                "--include-warning-items",
            ],
            timeout=60,
        )
    except CorpusCommandError as exc:
        report["errors"].append(f"status check failed: {exc}")

    pending = dict((status or last_sync or {}).get("pending", {}))
    pending.pop("items", None)
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
            "partial_extraction": dict((status or {}).get("partial_extraction", {})),
            "approved_large_documents": approved_large,
            "warning_items": list((status or {}).get("warning_items", [])),
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
    if approved_large is not None and int(
        approved_large.get("summary", {}).get("failed", 0)
    ):
        report["errors"].append("an approved large-document refresh failed")

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
    parser.add_argument(
        "--warning-state",
        type=Path,
        help="Private JSON file used to compare document-level warnings between runs.",
    )
    parser.add_argument(
        "--reset-warning-baseline",
        action="store_true",
        help="Replace the selected warning baseline without reporting current items as new.",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    if args.reset_warning_baseline and args.warning_state is None:
        report = {
            "ok": False,
            "errors": ["--reset-warning-baseline requires --warning-state"],
        }
        print(json.dumps(report, ensure_ascii=False, indent=2 if args.pretty else None))
        return 2
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
    if args.warning_state is not None:
        try:
            report["warning_state"] = _update_warning_state(
                args.warning_state,
                results,
                reset=args.reset_warning_baseline,
                refresh_ok=bool(report["ok"]),
            )
        except (CorpusCommandError, OSError, ValueError) as exc:
            report["ok"] = False
            report["warning_state"] = {
                "path": str(args.warning_state.expanduser()),
                "updated": False,
                "error": str(exc),
            }
    for corpus_report in results:
        warning_items = corpus_report.pop("warning_items", [])
        corpus_report["warning_item_count"] = len(warning_items)
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
