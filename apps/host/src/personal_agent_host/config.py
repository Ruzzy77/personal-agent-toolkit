"""Host configuration: the Sync configuration plus a [host] table and root policies."""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from personal_agent_sync.config import ConnectionConfig, SyncConfig, load_config
from personal_agent_sync.errors import SyncError

Execute = Literal["none", "host"]

DEFAULT_PREFIX = Path("~/.local/share/personal-agent-host").expanduser()
LIMITS = {
    "read_bytes": 2 * 1024 * 1024,
    "write_bytes": 2 * 1024 * 1024,
    "job_output_bytes": 256 * 1024,
    "timeout_s": 21_600,
    "wait_s": 50,
}

ROOT_ID = re.compile(r"[a-z0-9][a-z0-9._-]*")
PERMISSIONS = ("read_only", "create_only", "read_write")


@dataclass(frozen=True)
class RootPolicy:
    """One exposed root: either a Host-only root or a Corpus Connection root."""

    id: str
    root: Path
    permission: str
    execute: Execute
    sources: tuple[str, ...]
    connection: ConnectionConfig | None = None


@dataclass(frozen=True)
class BackupConfig:
    target: Path
    paths: tuple[Path, ...]
    time: str


@dataclass(frozen=True)
class HostConfig:
    sync: SyncConfig
    listen_host: str
    listen_port: int
    allowed_hosts: tuple[str, ...]
    max_concurrent_jobs: int
    token_path: Path
    backup: BackupConfig | None
    roots: tuple[RootPolicy, ...]
    read_only_paths: tuple[Path, ...]

    @property
    def prefix(self) -> Path:
        return self.token_path.parent.parent

    def protects(self, target: Path) -> bool:
        """Report whether target sits in a protected source, wherever it is reached."""

        resolved = target.resolve(strict=False)
        return any(
            resolved == guard or guard in resolved.parents
            for guard in self.read_only_paths
        )

    def protected_within(self, root: Path) -> tuple[Path, ...]:
        """Return protected sources nested inside root, shallowest first."""

        base = root.resolve(strict=False)
        nested = [guard for guard in self.read_only_paths if base in guard.parents]
        return tuple(sorted(nested, key=lambda item: len(item.parts)))

    def root(self, root_id: str) -> RootPolicy:
        for item in self.roots:
            if item.id == root_id:
                return item
        raise SyncError("root_not_found", "root is not registered on this host")


def default_config_path() -> Path:
    override = os.environ.get("PERSONAL_AGENT_HOST_CONFIG")
    if override:
        return Path(override).expanduser()
    return DEFAULT_PREFIX / "config" / "host.toml"


def _execute(value: object, *, field: str) -> Execute:
    if value is None:
        return "none"
    if value == "sandbox":
        raise SyncError(
            "invalid_configuration",
            f"{field}=sandbox was retired; explicitly migrate this root to execute=host",
        )
    if value not in ("none", "host"):
        raise SyncError("invalid_configuration", f"{field} must be none or host")
    return value  # type: ignore[return-value]


def _guards(value: object) -> tuple[Path, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise SyncError(
            "invalid_configuration", "host.read_only_paths must be non-empty strings"
        )
    return tuple(
        sorted({Path(item).expanduser().resolve(strict=False) for item in value})
    )


def _protected(root: Path, guards: tuple[Path, ...]) -> bool:
    return any(root == guard or guard in root.parents for guard in guards)


def _host_roots(value: object, guards: tuple[Path, ...]) -> list[RootPolicy]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise SyncError("invalid_configuration", "[[host.roots]] must be a list")
    roots: list[RootPolicy] = []
    for entry in value:
        if not isinstance(entry, dict):
            raise SyncError("invalid_configuration", "[[host.roots]] entry is invalid")
        identifier = entry.get("id")
        if not isinstance(identifier, str) or not ROOT_ID.fullmatch(identifier):
            raise SyncError("invalid_configuration", "host.roots.id is invalid")
        raw_path = entry.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            raise SyncError(
                "invalid_configuration", f"{identifier}: host.roots.path is required"
            )
        root = Path(raw_path).expanduser().resolve(strict=False)
        if not root.is_dir():
            raise SyncError(
                "invalid_configuration", f"{identifier}: root is not a directory"
            )
        permission = entry.get("permission", "read_write")
        if permission not in PERMISSIONS:
            raise SyncError(
                "invalid_configuration", f"{identifier}: permission is invalid"
            )
        execute = _execute(entry.get("execute"), field=f"{identifier}.execute")
        sources = entry.get("sources", [])
        if not isinstance(sources, list) or not all(
            isinstance(item, str) for item in sources
        ):
            raise SyncError(
                "invalid_configuration", f"{identifier}: sources are invalid"
            )
        if _protected(root, guards):
            permission, execute = "read_only", "none"
        if execute == "host" and permission != "read_write":
            raise SyncError(
                "invalid_configuration",
                f"{identifier}: execute=host requires a read_write root",
            )
        roots.append(
            RootPolicy(
                id=identifier,
                root=root,
                permission=permission,
                execute=execute,
                sources=tuple(sources),
            )
        )
    return roots


def _backup(value: object) -> BackupConfig | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise SyncError("invalid_configuration", "[host.backup] must be a table")
    target = value.get("target")
    paths = value.get("paths", [])
    time = value.get("time", "03:30")
    if not isinstance(target, str) or not target:
        raise SyncError("invalid_configuration", "host.backup.target is required")
    if not isinstance(paths, list) or not all(isinstance(v, str) for v in paths):
        raise SyncError("invalid_configuration", "host.backup.paths must be strings")
    if not isinstance(time, str) or not re.fullmatch(r"[0-2]\d:[0-5]\d", time):
        raise SyncError("invalid_configuration", "host.backup.time must be HH:MM")
    return BackupConfig(
        target=Path(target).expanduser(),
        paths=tuple(Path(v).expanduser() for v in paths),
        time=time,
    )


def load_host_config(path: Path | None = None) -> HostConfig:
    source = (path or default_config_path()).expanduser()
    sync = load_config(source)
    try:
        raw = tomllib.loads(source.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise SyncError(
            "invalid_configuration", "Host configuration could not be read"
        ) from exc

    host = raw.get("host", {})
    if not isinstance(host, dict):
        raise SyncError("invalid_configuration", "[host] must be a table")
    retired = {
        "https_host_allowlist",
        "egress_proxy_image",
        "sandbox_image",
        "execution_profiles",
    } & set(host)
    if retired:
        raise SyncError(
            "invalid_configuration",
            f"host.{min(retired)} was retired; migrate to direct host execution configuration",
        )
    listen = host.get("listen", "127.0.0.1:18790")
    if not isinstance(listen, str) or ":" not in listen:
        raise SyncError("invalid_configuration", "host.listen must be host:port")
    listen_host, _, port_text = listen.rpartition(":")
    try:
        listen_port = int(port_text)
    except ValueError as exc:
        raise SyncError("invalid_configuration", "host.listen port is invalid") from exc
    if listen_host not in ("127.0.0.1", "localhost", "::1"):
        raise SyncError("invalid_configuration", "host.listen must be loopback")
    allowed = host.get("allowed_hosts", [])
    if not isinstance(allowed, list) or not all(isinstance(v, str) for v in allowed):
        raise SyncError("invalid_configuration", "host.allowed_hosts must be strings")
    max_jobs = host.get("max_concurrent_jobs", 4)
    if (
        isinstance(max_jobs, bool)
        or not isinstance(max_jobs, int)
        or not 1 <= max_jobs <= 64
    ):
        raise SyncError(
            "invalid_configuration", "host.max_concurrent_jobs must be 1..64"
        )
    token_value = host.get("token_path", str(source.parent / "host-upstream.token"))
    if not isinstance(token_value, str):
        raise SyncError("invalid_configuration", "host.token_path is invalid")
    backup = _backup(host.get("backup"))
    guards = _guards(host.get("read_only_paths"))

    raw_connections = raw.get("connections", [])
    policies: dict[str, tuple[Execute, tuple[str, ...]]] = {}
    for value in raw_connections if isinstance(raw_connections, list) else []:
        if not isinstance(value, dict):
            continue
        key = f"{value.get('space_id')}:{value.get('connection_id')}"
        sources = value.get("sources", [])
        if not isinstance(sources, list) or not all(
            isinstance(v, str) for v in sources
        ):
            raise SyncError("invalid_configuration", "connection sources are invalid")
        policies[key] = (
            _execute(value.get("execute"), field="execute"),
            tuple(sources),
        )

    roots: list[RootPolicy] = _host_roots(host.get("roots"), guards)
    for connection in sync.connections:
        execute, sources = policies.get(connection.key, ("none", ()))
        permission = connection.permission
        if _protected(connection.root.resolve(strict=False), guards):
            permission, execute = "read_only", "none"
        elif permission == "read_write" and any(
            connection.root.resolve(strict=False) in guard.parents for guard in guards
        ):
            raise SyncError(
                "invalid_configuration",
                f"{connection.key}: a writable Connection cannot contain a protected source",
            )
        if execute == "host" and (
            "work" not in connection.roles or permission != "read_write"
        ):
            raise SyncError(
                "invalid_configuration",
                f"{connection.key}: execute=host requires a read_write work root",
            )
        roots.append(
            RootPolicy(
                id=f"{connection.space_id}/{connection.connection_id}",
                root=connection.root,
                permission=permission,
                execute=execute,
                sources=sources,
                connection=connection,
            )
        )
    ids: set[str] = set()
    for root in roots:
        if root.id in ids:
            raise SyncError("invalid_configuration", f"{root.id}: duplicate root id")
        ids.add(root.id)
    for root in roots:
        for source_id in root.sources:
            if source_id not in ids:
                raise SyncError(
                    "invalid_configuration",
                    f"{root.id}: unknown source {source_id}",
                )

    return HostConfig(
        sync=sync,
        listen_host=listen_host,
        listen_port=listen_port,
        allowed_hosts=tuple(allowed),
        max_concurrent_jobs=max_jobs,
        token_path=Path(token_value).expanduser(),
        backup=backup,
        roots=tuple(roots),
        read_only_paths=guards,
    )


def read_token(config: HostConfig) -> str:
    environment = os.environ.get("PERSONAL_AGENT_HOST_TOKEN")
    if environment:
        return environment.strip()
    try:
        token = config.token_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise SyncError("credentials_unavailable", "host token is unavailable") from exc
    if len(token) < 32:
        raise SyncError("credentials_unavailable", "host token is too short")
    return token
