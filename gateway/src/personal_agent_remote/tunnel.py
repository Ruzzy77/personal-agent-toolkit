"""Build Secure MCP Tunnel profiles for the local gateway."""

from __future__ import annotations

import argparse
import json
import re
import shlex
from typing import Any
from urllib.parse import urlparse

PRODUCTS = ("sense", "corpus", "hypes")
TUNNEL_PLAN_FORMAT = "personal-agent-secure-mcp-tunnel-plan"
TUNNEL_PLAN_SCHEMA_VERSION = 4
TUNNEL_ID_RE = re.compile(r"^tunnel_[0-9A-Za-z_-]{16,256}$")
DEFAULT_GATEWAY_BASE_URL = "http://127.0.0.1:18180"
HEALTH_LISTEN_ADDRS = {
    "sense": "127.0.0.1:18081",
    "corpus": "127.0.0.1:18082",
    "hypes": "127.0.0.1:18083",
}


class TunnelPlanError(ValueError):
    """A requested tunnel profile is invalid."""


def _validate_tunnel_id(value: object, *, product: str) -> str:
    if not isinstance(value, str) or TUNNEL_ID_RE.fullmatch(value) is None:
        raise TunnelPlanError(
            f"{product} tunnel id must use the tunnel_ technical-id format"
        )
    return value


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
    tunnel_client: str = "tunnel-client",
    gateway_base_url: str = DEFAULT_GATEWAY_BASE_URL,
) -> dict[str, Any]:
    unknown = set(tunnel_ids).difference(PRODUCTS)
    if unknown:
        raise TunnelPlanError("tunnel ids must contain only Sense, Corpus, and Hypes")
    selected = tuple(product for product in PRODUCTS if product in tunnel_ids)
    if not selected:
        raise TunnelPlanError("at least one product tunnel id is required")
    ids = {
        product: _validate_tunnel_id(tunnel_ids[product], product=product)
        for product in selected
    }
    if len(set(ids.values())) != len(selected):
        raise TunnelPlanError("each product must use a distinct tunnel id")
    if not tunnel_client or any(ord(character) < 32 for character in tunnel_client):
        raise TunnelPlanError("tunnel client command is invalid")
    gateway = _validate_gateway_base_url(gateway_base_url)

    profiles = []
    for product in selected:
        profile = f"personal-agent-gateway-{product}"
        server_url = f"{gateway}/{product}/mcp"
        profiles.append(
            {
                "product": product,
                "profile": profile,
                "tunnel_id": ids[product],
                "mcp_server_url": server_url,
                "health_listen_addr": HEALTH_LISTEN_ADDRS[product],
                "init_argv": [
                    tunnel_client,
                    "init",
                    "--profile",
                    profile,
                    "--tunnel-id",
                    ids[product],
                    "--health-listen-addr",
                    HEALTH_LISTEN_ADDRS[product],
                    "--mcp-server-url",
                    server_url,
                ],
                "doctor_argv": [
                    tunnel_client,
                    "doctor",
                    "--profile",
                    profile,
                    "--explain",
                ],
                "run_argv": [tunnel_client, "run", "--profile", profile],
            }
        )
    return {
        "format": TUNNEL_PLAN_FORMAT,
        "schema_version": TUNNEL_PLAN_SCHEMA_VERSION,
        "transport": "streamable-http",
        "products": profiles,
        "requirements": {
            "runtime_api_key_environment": "CONTROL_PLANE_API_KEY",
            "one_active_host_per_product": True,
            "moves_application_data": False,
            "gateway_loopback_only": True,
        },
    }


def _shell_plan(plan: dict[str, Any]) -> str:
    commands: list[str] = []
    for product in plan["products"]:
        commands.extend(
            (
                shlex.join(product["init_argv"]),
                shlex.join(product["doctor_argv"]),
                shlex.join(product["run_argv"]),
            )
        )
    return "\n".join(commands)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare Secure MCP Tunnel profiles for the local personal-agent gateway"
    )
    parser.add_argument("--sense-tunnel-id")
    parser.add_argument("--corpus-tunnel-id")
    parser.add_argument("--hypes-tunnel-id")
    parser.add_argument("--tunnel-client", default="tunnel-client")
    parser.add_argument("--gateway-base-url", default=DEFAULT_GATEWAY_BASE_URL)
    parser.add_argument("--format", choices=("json", "shell"), default="json")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    tunnel_ids = {
        product: value
        for product, value in (
            ("sense", args.sense_tunnel_id),
            ("corpus", args.corpus_tunnel_id),
            ("hypes", args.hypes_tunnel_id),
        )
        if value is not None
    }
    try:
        plan = build_plan(
            tunnel_ids=tunnel_ids,
            tunnel_client=args.tunnel_client,
            gateway_base_url=args.gateway_base_url,
        )
    except TunnelPlanError as exc:
        print(str(exc))
        return 1
    print(_shell_plan(plan) if args.format == "shell" else json.dumps(plan, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
