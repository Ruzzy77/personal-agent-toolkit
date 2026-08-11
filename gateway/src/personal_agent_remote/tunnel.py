"""Build local Secure MCP Tunnel profiles for the three independent products."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

PRODUCTS = ("sense", "corpus", "hypes")
TUNNEL_PLAN_FORMAT = "personal-agent-secure-mcp-tunnel-plan"
TUNNEL_PLAN_SCHEMA_VERSION = 4
TUNNEL_ID_RE = re.compile(r"^tunnel_[0-9A-Za-z_-]{16,256}$")
CONNECTION_MODES = ("direct", "gateway")
DEFAULT_GATEWAY_BASE_URL = "http://127.0.0.1:18180"
HEALTH_LISTEN_ADDRS = {
    "sense": "127.0.0.1:18081",
    "corpus": "127.0.0.1:18082",
    "hypes": "127.0.0.1:18083",
}


class TunnelPlanError(ValueError):
    """The requested local tunnel profile would not be safe or reproducible."""


@dataclass(frozen=True)
class TunnelProfile:
    product: str
    profile: str
    tunnel_id: str
    mcp_command: Path | None
    mcp_server_url: str | None
    health_listen_addr: str

    def init_argv(self, *, tunnel_client: str) -> list[str]:
        argv = [
            tunnel_client,
            "init",
            "--profile",
            self.profile,
            "--tunnel-id",
            self.tunnel_id,
            "--health-listen-addr",
            self.health_listen_addr,
        ]
        if self.mcp_command is not None:
            return [*argv, "--mcp-command", str(self.mcp_command)]
        if self.mcp_server_url is not None:
            return [*argv, "--mcp-server-url", self.mcp_server_url]
        raise AssertionError("tunnel profile target is missing")

    def doctor_argv(self, *, tunnel_client: str) -> list[str]:
        return [
            tunnel_client,
            "doctor",
            "--profile",
            self.profile,
            "--explain",
        ]

    def run_argv(self, *, tunnel_client: str) -> list[str]:
        return [tunnel_client, "run", "--profile", self.profile]


def _workspace_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _validate_tunnel_id(value: object, *, product: str) -> str:
    if not isinstance(value, str) or TUNNEL_ID_RE.fullmatch(value) is None:
        raise TunnelPlanError(
            f"{product} tunnel id must use the tunnel_ technical-id format"
        )
    return value


def _validate_launcher(path: Path, *, product: str) -> Path:
    expanded = path.expanduser()
    if not expanded.is_absolute():
        raise TunnelPlanError(f"{product} MCP launcher must be an absolute path")
    try:
        metadata = expanded.lstat()
        canonical = expanded.resolve(strict=True)
    except OSError as exc:
        raise TunnelPlanError(f"{product} MCP launcher is unavailable") from exc
    if (
        canonical != expanded
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_mode & 0o022
        or not metadata.st_mode & stat.S_IXUSR
    ):
        raise TunnelPlanError(
            f"{product} MCP launcher must be an owned, non-writable executable regular file"
        )
    return canonical


def _validate_gateway_base_url(value: str) -> str:
    raw = value.strip().rstrip("/")
    parsed = urlparse(raw)
    try:
        port = parsed.port
    except ValueError as exc:
        raise TunnelPlanError("gateway base URL contains an invalid port") from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or port is None
        or not 1 <= port <= 65535
        or parsed.path not in {"", "/"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise TunnelPlanError(
            "gateway base URL must be an explicit loopback HTTP origin with a port"
        )
    return raw


def build_plan(
    *,
    tunnel_ids: dict[str, str],
    workspace_root: Path | None = None,
    tunnel_client: str = "tunnel-client",
    connection_mode: str = "direct",
    gateway_base_url: str = DEFAULT_GATEWAY_BASE_URL,
) -> dict[str, Any]:
    unknown = set(tunnel_ids).difference(PRODUCTS)
    if unknown:
        raise TunnelPlanError("tunnel ids must contain only Sense, Corpus, and Hypes")
    selected = tuple(product for product in PRODUCTS if product in tunnel_ids)
    if not selected:
        raise TunnelPlanError("at least one product tunnel id is required")
    normalized_ids = {
        product: _validate_tunnel_id(tunnel_ids[product], product=product)
        for product in selected
    }
    if len(set(normalized_ids.values())) != len(selected):
        raise TunnelPlanError("each product must use a distinct tunnel id")
    if not tunnel_client or any(ord(character) < 32 for character in tunnel_client):
        raise TunnelPlanError("tunnel client command is invalid")
    if connection_mode not in CONNECTION_MODES:
        raise TunnelPlanError("connection mode must be direct or gateway")
    normalized_gateway_url = (
        _validate_gateway_base_url(gateway_base_url)
        if connection_mode == "gateway"
        else None
    )

    canonical_root = None
    if connection_mode == "direct":
        root = (workspace_root or _workspace_root()).expanduser()
        if not root.is_absolute():
            raise TunnelPlanError("workspace root must be absolute")
        try:
            canonical_root = root.resolve(strict=True)
        except OSError as exc:
            raise TunnelPlanError("workspace root is unavailable") from exc

    profiles = []
    for product in selected:
        launcher = None
        server_url = None
        if connection_mode == "direct":
            assert canonical_root is not None
            launcher = _validate_launcher(
                canonical_root / "owners" / product / "bin" / f"{product}-mcp",
                product=product,
            )
        else:
            server_url = f"{normalized_gateway_url}/{product}/mcp"
        profile = TunnelProfile(
            product=product,
            profile=(
                f"personal-agent-{product}"
                if connection_mode == "direct"
                else f"personal-agent-gateway-{product}"
            ),
            tunnel_id=normalized_ids[product],
            mcp_command=launcher,
            mcp_server_url=server_url,
            health_listen_addr=HEALTH_LISTEN_ADDRS[product],
        )
        item = {
            "product": profile.product,
            "profile": profile.profile,
            "tunnel_id": profile.tunnel_id,
            "health_listen_addr": profile.health_listen_addr,
            "init_argv": profile.init_argv(tunnel_client=tunnel_client),
            "doctor_argv": profile.doctor_argv(tunnel_client=tunnel_client),
            "run_argv": profile.run_argv(tunnel_client=tunnel_client),
        }
        if profile.mcp_command is not None:
            item["mcp_command"] = str(profile.mcp_command)
        else:
            item["mcp_server_url"] = profile.mcp_server_url
        profiles.append(item)
    return {
        "format": TUNNEL_PLAN_FORMAT,
        "schema_version": TUNNEL_PLAN_SCHEMA_VERSION,
        "connection_mode": connection_mode,
        "transport": "stdio" if connection_mode == "direct" else "streamable-http",
        "products": profiles,
        "requirements": {
            "runtime_api_key_environment": "CONTROL_PLANE_API_KEY",
            "one_active_host_per_product": True,
            "moves_application_data": False,
            "gateway_loopback_only": connection_mode == "gateway",
        },
    }


def _shell_plan(plan: dict[str, Any]) -> str:
    commands: list[str] = []
    for product in plan["products"]:
        commands.extend(
            [
                shlex.join(product["init_argv"]),
                shlex.join(product["doctor_argv"]),
                shlex.join(product["run_argv"]),
            ]
        )
    return "\n".join(commands)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare independent OpenAI Secure MCP Tunnel profiles for selected local "
            "Sense, Corpus, and Hypes products"
        )
    )
    parser.add_argument("--sense-tunnel-id")
    parser.add_argument("--corpus-tunnel-id")
    parser.add_argument("--hypes-tunnel-id")
    parser.add_argument("--workspace-root", type=Path, default=_workspace_root())
    parser.add_argument("--tunnel-client", default="tunnel-client")
    parser.add_argument(
        "--connection-mode",
        choices=CONNECTION_MODES,
        default="direct",
    )
    parser.add_argument("--gateway-base-url", default=DEFAULT_GATEWAY_BASE_URL)
    parser.add_argument(
        "--format",
        choices=("json", "shell"),
        default="json",
        dest="output_format",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        plan = build_plan(
            tunnel_ids={
                product: value
                for product, value in (
                    ("sense", args.sense_tunnel_id),
                    ("corpus", args.corpus_tunnel_id),
                    ("hypes", args.hypes_tunnel_id),
                )
                if value is not None
            },
            workspace_root=args.workspace_root,
            tunnel_client=args.tunnel_client,
            connection_mode=args.connection_mode,
            gateway_base_url=args.gateway_base_url,
        )
    except TunnelPlanError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1
    if args.output_format == "shell":
        print(_shell_plan(plan))
    else:
        print(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
