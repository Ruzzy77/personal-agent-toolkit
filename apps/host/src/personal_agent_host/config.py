"""Host configuration: the Sync configuration plus a [host] table and root policies."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from personal_agent_sync.config import ConnectionConfig, SyncConfig, load_config
from personal_agent_sync.errors import SyncError

Execute = Literal["none", "sandbox"]

DEFAULT_PREFIX = Path("~/.local/share/personal-agent-host").expanduser()
LIMITS = {
    "read_bytes": 2 * 1024 * 1024,
    "write_bytes": 2 * 1024 * 1024,
    "job_output_bytes": 256 * 1024,
    "timeout_s": 21_600,
    "wait_s": 50,
}


@dataclass(frozen=True)
class RootPolicy:
    connection: ConnectionConfig
    execute: Execute
    sources: tuple[str, ...]

    @property
    def id(self) -> str:
        return f"{self.connection.space_id}/{self.connection.connection_id}"

    @property
    def permission(self) -> str:
        return self.connection.permission

    @property
    def root(self) -> Path:
        return self.connection.root


@dataclass(frozen=True)
class HostConfig:
    sync: SyncConfig
    listen_host: str
    listen_port: int
    allowed_hosts: tuple[str, ...]
    sandbox_image: str
    max_concurrent_jobs: int
    token_path: Path
    roots: tuple[RootPolicy, ...]

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
    if value not in ("none", "sandbox"):
        raise SyncError("invalid_configuration", f"{field} must be none or sandbox")
    return value  # type: ignore[return-value]


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
    sandbox_image = host.get("sandbox_image", "personal-agent-host-sandbox:1")
    if not isinstance(sandbox_image, str) or not sandbox_image:
        raise SyncError("invalid_configuration", "host.sandbox_image is invalid")
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

    roots: list[RootPolicy] = []
    for connection in sync.connections:
        execute, sources = policies.get(connection.key, ("none", ()))
        if execute == "sandbox" and (
            "work" not in connection.roles or connection.permission != "read_write"
        ):
            raise SyncError(
                "invalid_configuration",
                f"{connection.key}: execute=sandbox requires a read_write work root",
            )
        roots.append(RootPolicy(connection, execute, sources))
    ids = {root.id for root in roots}
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
        sandbox_image=sandbox_image,
        max_concurrent_jobs=max_jobs,
        token_path=Path(token_value).expanduser(),
        roots=tuple(roots),
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
