"""Filesystem notifications that wake bounded Source reconciliation."""

from __future__ import annotations

import asyncio
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from .reconcile import current_root
from .state import SyncState


class _SourceEventHandler(FileSystemEventHandler):
    def __init__(self, loop: asyncio.AbstractEventLoop, changed: asyncio.Event) -> None:
        self.loop = loop
        self.changed = changed

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.event_type not in {"created", "deleted", "modified", "moved"}:
            return
        self.loop.call_soon_threadsafe(self.changed.set)


class SourceEventMonitor:
    """Watch current roots and restart only when a recovered root path changes."""

    def __init__(self, loop: asyncio.AbstractEventLoop, changed: asyncio.Event) -> None:
        self.loop = loop
        self.changed = changed
        self.observer: Observer | None = None
        self.signature: tuple[str, ...] = ()

    @staticmethod
    def _roots(state: SyncState) -> tuple[Path, ...]:
        roots: set[Path] = set()
        for connection in state.config.source_watchers:
            root = current_root(state, connection)
            if root is not None:
                roots.add(root)
        return tuple(sorted(roots, key=lambda value: str(value)))

    def refresh(self, state: SyncState) -> bool:
        roots = self._roots(state)
        signature = tuple(str(root) for root in roots)
        if signature == self.signature and (
            (not signature and self.observer is None)
            or (self.observer is not None and self.observer.is_alive())
        ):
            return False
        self.close()
        self.signature = signature
        if not roots:
            return True
        observer = Observer()
        handler = _SourceEventHandler(self.loop, self.changed)
        started = False
        try:
            for root in roots:
                observer.schedule(handler, str(root), recursive=True)
            observer.start()
            started = True
        except Exception:
            observer.stop()
            if started or observer.is_alive():
                observer.join(timeout=5)
            self.signature = ()
            raise
        self.observer = observer
        return True

    def close(self) -> None:
        observer = self.observer
        self.observer = None
        self.signature = ()
        if observer is None:
            return
        observer.stop()
        observer.join(timeout=5)
