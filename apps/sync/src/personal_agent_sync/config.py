"""Strict local configuration; absolute Finder locators never leave this module."""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
import tomllib
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from .errors import SyncError

AnalyzerRoute = Literal["local", "remote", "approval_required"]
AccessScope = Literal["remote_allowed", "local_only"]
Permission = Literal["read_only", "read_write"]


@dataclass(frozen=True)
class ConnectionConfig:
    space_id: str
    connection_id: str
    root: Path
    roles: frozenset[str]
    access_scope: AccessScope
    permission: Permission
    corpus_id: str | None
    analyzer_route: AnalyzerRoute
    max_transfer_bytes: int
    generation: int = 1
    include_hidden: bool = False
    exclude_directory_names: frozenset[str] = frozenset()
    exclude_path_prefixes: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        return f"{self.space_id}:{self.connection_id}"


@dataclass(frozen=True)
class SyncConfig:
    service_url: str
    device_id: str
    display_name: str
    data_root: Path
    corpus_data_root: Path | None
    corpus_python: Path | None
    document_files_python: Path | None
    reconcile_seconds: float
    full_reconcile_seconds: float
    event_debounce_seconds: float
    connections: tuple[ConnectionConfig, ...]

    @property
    def websocket_url(self) -> str:
        parsed = urlparse(self.service_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        return f"{scheme}://{parsed.netloc}{parsed.path.rstrip('/')}/sync/v1/connect"

    @property
    def source_watchers(self) -> tuple[ConnectionConfig, ...]:
        """Return one filesystem watcher for each shared durable Corpus."""

        seen: set[str] = set()
        selected: list[ConnectionConfig] = []
        for connection in self.connections:
            if "source" not in connection.roles:
                continue
            identity = connection.corpus_id or connection.key
            if identity in seen:
                continue
            seen.add(identity)
            selected.append(connection)
        return tuple(selected)


def _identifier(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 64:
        raise SyncError("invalid_configuration", f"{field} is invalid")
    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789._-")
    if value[0] not in set("abcdefghijklmnopqrstuvwxyz0123456789") or any(
        character not in allowed for character in value
    ):
        raise SyncError("invalid_configuration", f"{field} is invalid")
    return value


def _private_directory(path: Path) -> Path:
    expanded = Path(os.path.abspath(path.expanduser()))
    if expanded.exists():
        metadata = expanded.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise SyncError("unsafe_storage", "Sync data location must be a directory")
        if stat.S_IMODE(metadata.st_mode) != 0o700:
            os.chmod(expanded, 0o700)
    else:
        expanded.mkdir(parents=True, mode=0o700)
    return expanded


def default_config_path() -> Path:
    configured = os.environ.get("PERSONAL_AGENT_SYNC_CONFIG")
    if configured:
        return Path(configured).expanduser()
    return (
        Path.home()
        / "Library"
        / "Application Support"
        / "Personal Agent Sync"
        / "config.toml"
    )


def rewrite_connection_roots(
    path: Path | None, connection_keys: set[str], root: Path
) -> dict[str, object]:
    """Atomically replace local-only root locators for selected Connections.

    The generated Sync configuration is intentionally kept human-readable.  A
    targeted text edit preserves comments and unrelated settings instead of
    round-tripping the whole TOML document through a serializer.
    """

    source = (path or default_config_path()).expanduser()
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise SyncError(
            "invalid_configuration", "Sync configuration could not be read"
        ) from exc
    starts = [
        match.start() for match in re.finditer(r"(?m)^\[\[connections\]\]\s*$", text)
    ]
    if not starts:
        raise SyncError(
            "invalid_configuration", "Sync configuration has no Connections"
        )
    starts.append(len(text))
    replacement = unicodedata.normalize("NFC", str(root))
    chunks: list[str] = [text[: starts[0]]]
    updated: set[str] = set()
    for index in range(len(starts) - 1):
        chunk = text[starts[index] : starts[index + 1]]
        try:
            parsed = tomllib.loads(chunk)["connections"][0]
        except (KeyError, IndexError, TypeError, tomllib.TOMLDecodeError) as exc:
            raise SyncError(
                "invalid_configuration", "a Connection configuration is invalid"
            ) from exc
        key = f"{parsed.get('space_id')}:{parsed.get('connection_id')}"
        if key in connection_keys:
            root_line = re.compile(r"(?m)^(\s*root\s*=\s*).*$")
            if root_line.search(chunk) is None:
                raise SyncError("invalid_configuration", "a Connection root is missing")
            chunk = root_line.sub(
                lambda match: (
                    match.group(1) + json.dumps(replacement, ensure_ascii=False)
                ),
                chunk,
                count=1,
            )
            updated.add(key)
        chunks.append(chunk)
    if updated != connection_keys:
        raise SyncError(
            "connection_not_found", "not every rebound Connection exists in config"
        )
    rewritten = "".join(chunks)
    mode = stat.S_IMODE(source.stat().st_mode)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{source.name}.", suffix=".tmp", dir=source.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(rewritten)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, source)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "updated_connections": sorted(updated),
        "root": replacement,
    }


def load_config(path: Path | None = None) -> SyncConfig:
    source = (path or default_config_path()).expanduser()
    try:
        raw = tomllib.loads(source.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise SyncError(
            "invalid_configuration", "Sync configuration could not be read"
        ) from exc

    service_url = raw.get("service_url")
    if not isinstance(service_url, str):
        raise SyncError("invalid_configuration", "service_url is required")
    parsed = urlparse(service_url)
    allow_http = os.environ.get("PERSONAL_AGENT_SYNC_ALLOW_HTTP") == "1"
    if (
        parsed.scheme not in ({"https", "http"} if allow_http else {"https"})
        or not parsed.netloc
    ):
        raise SyncError("invalid_configuration", "service_url must be an HTTPS origin")
    if parsed.query or parsed.fragment:
        raise SyncError(
            "invalid_configuration", "service_url cannot contain query or fragment"
        )

    device_id = _identifier(raw.get("device_id"), field="device_id")
    display_name = raw.get("display_name", device_id)
    if (
        not isinstance(display_name, str)
        or not display_name.strip()
        or len(display_name) > 160
    ):
        raise SyncError("invalid_configuration", "display_name is invalid")
    data_value = raw.get(
        "data_root",
        str(Path.home() / "Library" / "Application Support" / "Personal Agent Sync"),
    )
    if not isinstance(data_value, str):
        raise SyncError("invalid_configuration", "data_root is invalid")
    data_root = _private_directory(Path(data_value))
    corpus_data_value = raw.get("corpus_data_root")
    corpus_data_root = (
        Path(os.path.abspath(Path(corpus_data_value).expanduser()))
        if isinstance(corpus_data_value, str)
        else None
    )
    corpus_python = _runtime_path(raw.get("corpus_python"), "corpus_python")
    document_files_python = _runtime_path(
        raw.get("document_files_python"), "document_files_python"
    )
    reconcile_seconds = raw.get("reconcile_seconds", 15.0)
    if (
        isinstance(reconcile_seconds, bool)
        or not isinstance(reconcile_seconds, (int, float))
        or not 2 <= float(reconcile_seconds) <= 3600
    ):
        raise SyncError(
            "invalid_configuration", "reconcile_seconds must be between 2 and 3600"
        )
    full_reconcile_seconds = raw.get("full_reconcile_seconds", 900.0)
    if (
        isinstance(full_reconcile_seconds, bool)
        or not isinstance(full_reconcile_seconds, (int, float))
        or not 60 <= float(full_reconcile_seconds) <= 86_400
    ):
        raise SyncError(
            "invalid_configuration",
            "full_reconcile_seconds must be between 60 and 86400",
        )
    event_debounce_seconds = raw.get("event_debounce_seconds", 2.0)
    if (
        isinstance(event_debounce_seconds, bool)
        or not isinstance(event_debounce_seconds, (int, float))
        or not 0.25 <= float(event_debounce_seconds) <= 30
    ):
        raise SyncError(
            "invalid_configuration",
            "event_debounce_seconds must be between 0.25 and 30",
        )

    raw_connections = raw.get("connections", [])
    if not isinstance(raw_connections, list):
        raise SyncError("invalid_configuration", "connections must be an array")
    connections: list[ConnectionConfig] = []
    keys: set[str] = set()
    for value in raw_connections:
        if not isinstance(value, dict):
            raise SyncError(
                "invalid_configuration", "Connection configuration is invalid"
            )
        space_id = _identifier(value.get("space_id"), field="space_id")
        connection_id = _identifier(value.get("connection_id"), field="connection_id")
        root_value = value.get("root")
        if not isinstance(root_value, str):
            raise SyncError("invalid_configuration", "Connection root is required")
        root = Path(os.path.abspath(Path(root_value).expanduser()))
        try:
            metadata = root.lstat()
        except FileNotFoundError:
            metadata = None
        except OSError as exc:
            raise SyncError(
                "source_unavailable", "a configured Connection root cannot be inspected"
            ) from exc
        if metadata is not None and (
            not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode)
        ):
            raise SyncError(
                "invalid_configuration", "Connection root must be a real directory"
            )
        roles_raw = value.get("roles")
        if (
            not isinstance(roles_raw, list)
            or not roles_raw
            or not all(role in {"source", "work"} for role in roles_raw)
        ):
            raise SyncError("invalid_configuration", "Connection roles are invalid")
        roles = frozenset(roles_raw)
        access_scope = value.get("access_scope", "local_only")
        permission = value.get("permission", "read_only")
        analyzer_route = value.get("analyzer_route", "local")
        if access_scope not in {"remote_allowed", "local_only"}:
            raise SyncError(
                "invalid_configuration", "Connection access scope is invalid"
            )
        if permission not in {"read_only", "read_write"}:
            raise SyncError("invalid_configuration", "Connection permission is invalid")
        if analyzer_route not in {"local", "remote", "approval_required"}:
            raise SyncError(
                "invalid_configuration", "Connection analyzer route is invalid"
            )
        corpus_id = value.get("corpus_id")
        if "source" in roles:
            corpus_id = _identifier(corpus_id, field="corpus_id")
        elif corpus_id is not None:
            raise SyncError(
                "invalid_configuration", "a Work-only Connection cannot name a Corpus"
            )
        max_transfer = value.get("max_transfer_bytes", 256 * 1024 * 1024)
        if (
            isinstance(max_transfer, bool)
            or not isinstance(max_transfer, int)
            or not 1 <= max_transfer <= 2 * 1024**3
        ):
            raise SyncError("invalid_configuration", "max_transfer_bytes is invalid")
        generation = value.get("generation", 1)
        if (
            isinstance(generation, bool)
            or not isinstance(generation, int)
            or generation < 1
        ):
            raise SyncError("invalid_configuration", "Connection generation is invalid")
        include_hidden = value.get("include_hidden", False)
        if type(include_hidden) is not bool:
            raise SyncError("invalid_configuration", "include_hidden must be a boolean")
        excluded_names_raw = value.get("exclude_directory_names", [])
        excluded_prefixes_raw = value.get("exclude_path_prefixes", [])
        if not isinstance(excluded_names_raw, list) or not all(
            isinstance(item, str)
            and item
            and item not in {".", ".."}
            and "/" not in item
            and "\\" not in item
            for item in excluded_names_raw
        ):
            raise SyncError(
                "invalid_configuration", "excluded directory names are invalid"
            )
        if not isinstance(excluded_prefixes_raw, list) or not all(
            isinstance(item, str)
            and item
            and not item.startswith("/")
            and all(
                part not in {"", ".", ".."}
                for part in item.replace("\\", "/").split("/")
            )
            for item in excluded_prefixes_raw
        ):
            raise SyncError(
                "invalid_configuration", "excluded path prefixes are invalid"
            )
        connection = ConnectionConfig(
            space_id=space_id,
            connection_id=connection_id,
            root=root,
            roles=roles,
            access_scope=access_scope,
            permission=permission,
            corpus_id=corpus_id,
            analyzer_route=analyzer_route,
            max_transfer_bytes=max_transfer,
            generation=generation,
            include_hidden=include_hidden,
            exclude_directory_names=frozenset(excluded_names_raw),
            exclude_path_prefixes=tuple(
                sorted(item.replace("\\", "/") for item in excluded_prefixes_raw)
            ),
        )
        if connection.key in keys:
            raise SyncError(
                "invalid_configuration", "Connection ids must be unique within a Space"
            )
        keys.add(connection.key)
        connections.append(connection)
    if (
        any("work" in connection.roles for connection in connections)
        and corpus_python is None
    ):
        raise SyncError(
            "invalid_configuration", "corpus_python is required for Work Connections"
        )
    if (
        any(
            "work" in connection.roles
            or ("source" in connection.roles and connection.analyzer_route == "local")
            for connection in connections
        )
        and document_files_python is None
    ):
        raise SyncError(
            "invalid_configuration",
            "document_files_python is required for Work Connections and local Source analysis",
        )
    return SyncConfig(
        service_url=service_url.rstrip("/"),
        device_id=device_id,
        display_name=display_name.strip(),
        data_root=data_root,
        corpus_data_root=corpus_data_root,
        corpus_python=corpus_python,
        document_files_python=document_files_python,
        reconcile_seconds=float(reconcile_seconds),
        full_reconcile_seconds=float(full_reconcile_seconds),
        event_debounce_seconds=float(event_debounce_seconds),
        connections=tuple(connections),
    )


def _runtime_path(value: object, field: str) -> Path | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise SyncError("invalid_configuration", f"{field} is invalid")
    configured = Path(os.path.abspath(Path(value).expanduser()))
    try:
        path = configured.resolve(strict=True)
        metadata = path.lstat()
    except OSError as exc:
        raise SyncError("invalid_configuration", f"{field} is unavailable") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise SyncError(
            "invalid_configuration", f"{field} must be a regular executable"
        )
    if not os.access(path, os.X_OK):
        raise SyncError("invalid_configuration", f"{field} must be executable")
    return configured
