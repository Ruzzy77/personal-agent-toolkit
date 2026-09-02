"""Single-worker automatic Source reconciliation and bounded queue processing."""

from __future__ import annotations

import argparse
import json
import signal
import threading
import time
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any

from .config import default_data_root
from .locking import maintenance_worker_lock
from .service import CorpusService


class _PendingEvents:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._paths: dict[str, set[str]] = defaultdict(set)
        self.changed = threading.Event()

    def add(self, corpus_id: str, relative_path: str) -> None:
        with self._lock:
            self._paths[corpus_id].add(relative_path)
        self.changed.set()

    def drain(self) -> dict[str, set[str]]:
        with self._lock:
            drained = dict(self._paths)
            self._paths.clear()
            self.changed.clear()
        return drained


def _relative_event_path(root: Path, raw_path: str) -> str:
    try:
        relative = Path(raw_path).relative_to(root).as_posix()
    except ValueError:
        return "."
    if relative in {"", "."}:
        return "."
    return unicodedata.normalize("NFC", relative)


def _start_observer(
    service: CorpusService,
    pending: _PendingEvents,
) -> Any | None:
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
    except ImportError:
        return None

    class Handler(FileSystemEventHandler):
        def __init__(self, corpus_id: str, root: Path) -> None:
            super().__init__()
            self.corpus_id = corpus_id
            self.root = root

        def on_any_event(self, event: Any) -> None:
            if event.event_type in {"opened", "closed", "closed_no_write"}:
                return
            paths = [getattr(event, "src_path", None)]
            if event.event_type == "moved":
                paths.append(getattr(event, "dest_path", None))
            for raw_path in paths:
                if isinstance(raw_path, str):
                    pending.add(
                        self.corpus_id,
                        _relative_event_path(self.root, raw_path),
                    )

    observer = Observer()
    scheduled = 0
    for corpus in service.corpora():
        if corpus.get("location_state") != "available":
            continue
        root = Path(corpus["source_root"])
        if not root.is_dir():
            continue
        observer.schedule(Handler(corpus["corpus_id"], root), str(root), recursive=True)
        scheduled += 1
    if scheduled == 0:
        return None
    observer.start()
    return observer


def _stop_observer(observer: Any | None) -> None:
    if observer is None:
        return
    observer.stop()
    observer.join(timeout=10)


def run_once(
    service: CorpusService,
    *,
    enqueue_reconcile: bool = True,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for corpus in service.corpora():
        corpus_id = str(corpus["corpus_id"])
        try:
            if enqueue_reconcile:
                service.enqueue_source_changes(
                    corpus_id,
                    ["."],
                    event_kind="reconcile",
                )
            results.append(service.process_source_change_queue(corpus_id))
        except Exception as exc:  # noqa: BLE001
            results.append(
                {
                    "corpus_id": corpus_id,
                    "state": "failed",
                    "error": {
                        "code": getattr(exc, "code", "unexpected_error"),
                        "message": str(exc),
                    },
                }
            )
    return {
        "state": (
            "complete"
            if all(result["state"] in {"complete", "idle"} for result in results)
            else "attention_required"
        ),
        "corpora": results,
        "count": len(results),
    }


def watch(
    service: CorpusService,
    *,
    settle_seconds: float = 2,
    reconcile_interval_seconds: float = 900,
) -> None:
    if settle_seconds < 0.25:
        raise ValueError("settle_seconds must be at least 0.25")
    if reconcile_interval_seconds < 10:
        raise ValueError("reconcile_interval_seconds must be at least 10")
    stopping = threading.Event()

    def stop(_signum: int, _frame: Any) -> None:
        stopping.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    pending = _PendingEvents()
    observer: Any | None = None
    with maintenance_worker_lock(service.data_root):
        print(json.dumps(run_once(service), ensure_ascii=False), flush=True)
        observer = _start_observer(service, pending)
        last_reconcile = time.monotonic()
        try:
            while not stopping.is_set():
                elapsed = time.monotonic() - last_reconcile
                timeout = min(
                    settle_seconds,
                    max(0.25, reconcile_interval_seconds - elapsed),
                )
                pending.changed.wait(timeout)
                for corpus_id, relative_paths in pending.drain().items():
                    service.enqueue_source_changes(
                        corpus_id,
                        sorted(relative_paths),
                        event_kind="changed",
                    )
                for corpus in service.corpora():
                    corpus_id = str(corpus["corpus_id"])
                    if service.source_change_queue_status(corpus_id)[
                        "pending_change_count"
                    ]:
                        print(
                            json.dumps(
                                service.process_source_change_queue(corpus_id),
                                ensure_ascii=False,
                            ),
                            flush=True,
                        )
                if time.monotonic() - last_reconcile >= reconcile_interval_seconds:
                    _stop_observer(observer)
                    observer = None
                    print(json.dumps(run_once(service), ensure_ascii=False), flush=True)
                    observer = _start_observer(service, pending)
                    last_reconcile = time.monotonic()
        finally:
            _stop_observer(observer)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="corpus-maintenance",
        description="Keep Corpus Sources reconciled without retaining change history.",
    )
    parser.add_argument("--data-root", type=Path, default=default_data_root())
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--settle-seconds", type=float, default=2)
    parser.add_argument("--reconcile-interval-seconds", type=float, default=900)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    service = CorpusService(args.data_root)
    if args.once:
        print(json.dumps(run_once(service), ensure_ascii=False))
        return 0
    watch(
        service,
        settle_seconds=args.settle_seconds,
        reconcile_interval_seconds=args.reconcile_interval_seconds,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
