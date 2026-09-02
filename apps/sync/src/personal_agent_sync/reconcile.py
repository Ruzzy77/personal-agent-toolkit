"""Bounded local reconciliation; no external scheduler is required."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from .config import ConnectionConfig
from .paths import resolve_moved_root
from .state import SyncState


def current_root(state: SyncState, connection: ConnectionConfig) -> Path | None:
    row = state.connection_row(connection.key)
    root = resolve_moved_root(
        Path(row["root_path"]), int(row["root_device"]), int(row["root_inode"])
    )
    if root is None:
        state.set_location_state(connection.key, "unavailable")
        return None
    if str(root) != row["root_path"]:
        state.update_root_path(connection.key, root)
    return root


def reconcile_connection(
    state: SyncState, connection: ConnectionConfig
) -> dict[str, int | str]:
    root = current_root(state, connection)
    if root is None:
        return {"state": "unavailable", "observed": 0, "changed": 0}
    observed = 0
    changed = 0
    seen: set[str] = set()
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        if not connection.include_hidden:
            directory_names[:] = [
                name for name in directory_names if not name.startswith(".")
            ]
            file_names = [name for name in file_names if not name.startswith(".")]
        base = Path(directory)
        for name in file_names:
            path = base / name
            try:
                metadata = path.lstat()
            except OSError:
                continue
            if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                continue
            relative = path.relative_to(root).as_posix()
            document_id, event = state.observe_file(connection.key, relative, metadata)
            seen.add(document_id)
            observed += 1
            if event != "unchanged":
                changed += 1
    state.mark_missing(connection.key, seen)
    return {"state": "available", "observed": observed, "changed": changed}


def reconcile_all(state: SyncState) -> list[dict[str, int | str]]:
    return [
        reconcile_connection(state, connection)
        for connection in state.config.connections
    ]
