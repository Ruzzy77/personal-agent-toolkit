"""Bounded local reconciliation; no external scheduler is required."""

from __future__ import annotations

import os
import stat
import unicodedata
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
    if "source" not in connection.roles:
        return {"state": "work_only", "observed": 0, "changed": 0}
    root = current_root(state, connection)
    if root is None:
        return {"state": "unavailable", "observed": 0, "changed": 0}
    observed = 0
    changed = 0
    seen: set[str] = set()

    def excluded(relative: str, *, directory_name: str | None = None) -> bool:
        normalized = unicodedata.normalize("NFC", relative.replace(os.sep, "/"))
        if (
            directory_name is not None
            and unicodedata.normalize("NFC", directory_name)
            in connection.exclude_directory_names
        ):
            return True
        return any(
            normalized == prefix or normalized.startswith(f"{prefix}/")
            for prefix in connection.exclude_path_prefixes
        )

    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        base = Path(directory)
        base_relative = base.relative_to(root)
        retained_directories = []
        for name in directory_names:
            relative = (base_relative / name).as_posix()
            if excluded(relative, directory_name=name):
                continue
            if not connection.include_hidden and name.startswith("."):
                continue
            retained_directories.append(name)
        directory_names[:] = retained_directories
        if not connection.include_hidden:
            file_names = [name for name in file_names if not name.startswith(".")]
        for name in file_names:
            path = base / name
            try:
                metadata = path.lstat()
            except OSError:
                continue
            if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                continue
            relative = path.relative_to(root).as_posix()
            if excluded(relative):
                continue
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
        for connection in state.config.source_watchers
    ]
