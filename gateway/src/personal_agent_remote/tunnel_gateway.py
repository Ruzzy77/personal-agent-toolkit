"""One loopback MCP gateway for independently installed personal products."""

from __future__ import annotations

import argparse
import ipaddress
import os
import sys
from collections.abc import Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import uvicorn
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route, get_route_path
from starlette.types import ASGIApp, Receive, Scope, Send

from personal_agent_remote.installed_products import normalize_products

PRODUCTS = ("sense", "corpus", "hypes")
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 18180
MAX_PROXY_BODY_BYTES = 16 * 1024 * 1024
LOCAL_PRODUCT_TOOLS = {
    "sense": frozenset(
        {
            "sense_read",
            "sense_overview",
            "sense_revise",
            "sense_control",
            "sense_status",
        }
    ),
    "corpus": frozenset(
        {
            "corpus_list",
            "corpus_overview",
            "corpus_status",
            "corpus_inventory",
            "corpus_search_candidates",
            "corpus_read",
            "corpus_source_read",
            "corpus_source_fetch",
            "corpus_source_update",
            "context_read",
            "context_update",
            "corpus_sync",
            "corpus_scan",
            "corpus_refresh",
        }
    ),
    "hypes": frozenset(
        {
            "hypes_read",
            "hypes_mark_recheck",
            "hypes_revise",
            "hypes_overview",
            "hypes_preview_forget",
            "hypes_forget",
            "hypes_status",
        }
    ),
}


class TunnelGatewayError(ValueError):
    """The personal gateway cannot start with the requested boundary."""


class McpEndpointOnly:
    """Expose only the mounted MCP endpoint from a child MCP application."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and get_route_path(scope) not in {"/mcp", "/mcp/"}:
            await Response(status_code=404)(scope, receive, send)
            return
        await self.app(scope, receive, send)


def validate_loopback_host(value: str) -> str:
    candidate = value.strip()
    if candidate == "localhost":
        return candidate
    try:
        if ipaddress.ip_address(candidate).is_loopback:
            return candidate
    except ValueError:
        pass
    raise TunnelGatewayError("tunnel gateway host must be loopback")


def _validate_backend_url(value: str, *, product: str) -> str:
    raw = value.strip().rstrip("/")
    parsed = urlparse(raw)
    try:
        port = parsed.port
    except ValueError as exc:
        raise TunnelGatewayError(f"{product} backend URL contains an invalid port") from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or port is None
        or not 1 <= port <= 65535
        or parsed.path not in {"/mcp", f"/{product}/mcp"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise TunnelGatewayError(
            f"{product} backend URL must be an explicit loopback MCP URL"
        )
    return raw


@dataclass(frozen=True)
class TunnelGatewaySettings:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    products: tuple[str, ...] = PRODUCTS
    backend_urls: tuple[tuple[str, str], ...] = ()
    sense_data_root: Path | None = None
    corpus_data_root: Path | None = None
    hypes_data_root: Path | None = None
    allowed_hosts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "host", validate_loopback_host(self.host))
        try:
            products = normalize_products(self.products)
        except ValueError as exc:
            raise TunnelGatewayError(str(exc)) from exc
        object.__setattr__(self, "products", products)
        if not 1 <= self.port <= 65535:
            raise TunnelGatewayError("tunnel gateway port must be between 1 and 65535")
        raw_backends = dict(self.backend_urls)
        if len(raw_backends) != len(self.backend_urls):
            raise TunnelGatewayError("tunnel gateway backend products must be unique")
        if raw_backends and set(raw_backends) != set(products):
            raise TunnelGatewayError(
                "tunnel gateway backends must exactly match the selected products"
            )
        object.__setattr__(
            self,
            "backend_urls",
            tuple(
                (product, _validate_backend_url(raw_backends[product], product=product))
                for product in products
            )
            if raw_backends
            else (),
        )
        for field_name in ("sense_data_root", "corpus_data_root", "hypes_data_root"):
            root = getattr(self, field_name)
            if root is None:
                continue
            expanded = root.expanduser()
            if not expanded.is_absolute():
                raise TunnelGatewayError(f"{field_name} must be an absolute path")
            object.__setattr__(self, field_name, expanded)

    @property
    def backends(self) -> dict[str, str]:
        return dict(self.backend_urls)

    @property
    def transport_hosts(self) -> tuple[str, ...]:
        if self.allowed_hosts:
            return self.allowed_hosts
        configured_host = f"[{self.host}]:*" if ":" in self.host else f"{self.host}:*"
        return tuple(
            dict.fromkeys(
                (
                    configured_host,
                    "127.0.0.1:*",
                    "localhost:*",
                    "[::1]:*",
                )
            )
        )

    @property
    def trusted_hosts(self) -> tuple[str, ...]:
        if self.allowed_hosts:
            return tuple(host.split(":", 1)[0] for host in self.allowed_hosts)
        return (self.host, "127.0.0.1", "localhost", "[::1]")


def _build_product_servers(settings: TunnelGatewaySettings) -> dict[str, Any]:
    servers: dict[str, Any] = {}
    if "sense" in settings.products:
        from sense.mcp_server import create_server as create_sense_server

        servers["sense"] = create_sense_server(settings.sense_data_root)
    if "corpus" in settings.products:
        from corpus.mcp_server import create_server as create_corpus_server

        servers["corpus"] = create_corpus_server(settings.corpus_data_root)
    if "hypes" in settings.products:
        from hypes.mcp_server import create_server as create_hypes_server

        servers["hypes"] = create_hypes_server(settings.hypes_data_root)
    return servers


async def _validate_product_tools(servers: Mapping[str, Any]) -> None:
    try:
        products = normalize_products(servers)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    observed: dict[str, frozenset[str]] = {}
    for product in products:
        server = servers[product]
        observed[product] = frozenset(tool.name for tool in await server.list_tools())
        if observed[product] != LOCAL_PRODUCT_TOOLS[product]:
            raise RuntimeError(f"{product} local tool policy is out of sync")
    for product in products:
        others = set().union(*(observed[other] for other in products if other != product))
        if observed[product] & others:
            raise RuntimeError(f"{product} local tool names overlap another product")


async def _backend_tool_names(client: httpx.AsyncClient, url: str) -> frozenset[str]:
    response = await client.post(
        url,
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        headers={"Accept": "application/json, text/event-stream"},
    )
    response.raise_for_status()
    payload = response.json()
    tools = payload.get("result", {}).get("tools") if isinstance(payload, dict) else None
    if not isinstance(tools, list):
        raise RuntimeError("installed product MCP returned an invalid tool list")
    names = []
    for tool in tools:
        if not isinstance(tool, dict) or not isinstance(tool.get("name"), str):
            raise RuntimeError("installed product MCP returned an invalid tool")
        names.append(tool["name"])
    return frozenset(names)


async def _validate_backend_tools(
    client: httpx.AsyncClient,
    backends: Mapping[str, str],
) -> None:
    observed: dict[str, frozenset[str]] = {}
    for product, url in backends.items():
        observed[product] = await _backend_tool_names(client, url)
        if observed[product] != LOCAL_PRODUCT_TOOLS[product]:
            raise RuntimeError(f"{product} installed tool policy is out of sync")
    for product in backends:
        others = set().union(
            *(observed[other] for other in backends if other != product)
        )
        if observed[product] & others:
            raise RuntimeError(f"{product} installed tool names overlap another product")


def _proxy_headers(request: Request) -> list[tuple[str, str]]:
    allowed = {
        "accept",
        "content-type",
        "mcp-method",
        "mcp-name",
        "mcp-protocol-version",
        "mcp-session-id",
    }
    return [
        (name, value)
        for name, value in request.headers.items()
        if name.casefold() in allowed or name.casefold().startswith("mcp-param-")
    ]


def _proxy_response_headers(response: httpx.Response) -> dict[str, str]:
    allowed = {"content-type", "mcp-protocol-version", "mcp-session-id", "cache-control"}
    return {
        name: value
        for name, value in response.headers.items()
        if name.casefold() in allowed
    }


async def _proxy_response_body(response: httpx.Response):
    try:
        if response.is_stream_consumed:
            yield response.content
        else:
            async for chunk in response.aiter_raw():
                yield chunk
    finally:
        await response.aclose()


def _streaming_proxy_response(response: httpx.Response) -> StreamingResponse:
    """Forward response headers before a long-lived MCP event stream produces data."""

    return StreamingResponse(
        _proxy_response_body(response),
        status_code=response.status_code,
        headers=_proxy_response_headers(response),
    )


def create_gateway_app(
    settings: TunnelGatewaySettings | None = None,
    *,
    product_servers: Mapping[str, Any] | None = None,
    http_transport: httpx.AsyncBaseTransport | None = None,
) -> Starlette:
    """Compose or proxy selected local product MCPs without adding a tool router."""

    resolved = settings or TunnelGatewaySettings()
    backends = resolved.backends
    if backends and product_servers is not None:
        raise RuntimeError("tunnel gateway cannot embed and proxy products together")
    servers = dict(product_servers or (_build_product_servers(resolved) if not backends else {}))
    if servers and set(servers) != set(resolved.products):
        raise RuntimeError("tunnel gateway product set is incomplete")
    transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=list(resolved.transport_hosts),
        allowed_origins=[],
    )
    child_apps = {
        product: server.streamable_http_app(
            streamable_http_path="/mcp",
            json_response=True,
            stateless_http=True,
            host=resolved.host,
            transport_security=transport_security,
        )
        for product, server in servers.items()
    }

    client_holder: dict[str, httpx.AsyncClient] = {}

    @asynccontextmanager
    async def lifespan(_: Starlette):
        if backends:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(660.0, connect=5.0),
                trust_env=False,
                transport=http_transport,
            ) as client:
                await _validate_backend_tools(client, backends)
                client_holder["client"] = client
                try:
                    yield
                finally:
                    client_holder.clear()
            return
        await _validate_product_tools(servers)
        async with AsyncExitStack() as stack:
            for child in child_apps.values():
                await stack.enter_async_context(child.router.lifespan_context(child))
            yield

    async def health(_: Request) -> JSONResponse:
        return JSONResponse(
            {
                "ok": True,
                "mode": "personal-secure-mcp-tunnel-gateway",
                "transport_session_state": False,
                "products": list(resolved.products),
                "product_runtime": "installed-plugin" if backends else "embedded",
            }
        )

    routes: list[Route | Mount] = [Route("/healthz", health, methods=["GET"])]
    if backends:
        for product in resolved.products:
            backend_url = backends[product]

            async def proxy(request: Request, *, target: str = backend_url) -> Response:
                body = await request.body()
                if len(body) > MAX_PROXY_BODY_BYTES:
                    return JSONResponse(
                        {"ok": False, "error": {"code": "gateway_request_too_large"}},
                        status_code=413,
                    )
                client = client_holder.get("client")
                if client is None:
                    return JSONResponse(
                        {"ok": False, "error": {"code": "gateway_not_ready"}},
                        status_code=503,
                    )
                try:
                    upstream_request = client.build_request(
                        request.method,
                        target,
                        content=body,
                        headers=_proxy_headers(request),
                    )
                    response = await client.send(upstream_request, stream=True)
                except httpx.HTTPError:
                    return JSONResponse(
                        {"ok": False, "error": {"code": "product_unavailable"}},
                        status_code=502,
                    )
                return _streaming_proxy_response(response)

            routes.extend(
                (
                    Route(
                        f"/{product}/mcp",
                        proxy,
                        methods=["GET", "POST", "DELETE"],
                    ),
                    Route(
                        f"/{product}/mcp/",
                        proxy,
                        methods=["GET", "POST", "DELETE"],
                    ),
                )
            )
    else:
        routes.extend(
            Mount(f"/{product}", app=McpEndpointOnly(child_apps[product]))
            for product in resolved.products
        )
    app = Starlette(
        routes=routes,
        lifespan=lifespan,
        middleware=[Middleware(TrustedHostMiddleware, allowed_hosts=list(resolved.trusted_hosts))],
    )
    app.state.product_servers = servers
    app.state.settings = resolved
    return app


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one loopback MCP gateway for selected personal Secure MCP Tunnels"
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("PAT_TUNNEL_GATEWAY_HOST", DEFAULT_HOST),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=os.environ.get("PAT_TUNNEL_GATEWAY_PORT", str(DEFAULT_PORT)),
    )
    parser.add_argument(
        "--product",
        action="append",
        choices=PRODUCTS,
        dest="products",
    )
    parser.add_argument(
        "--backend",
        action="append",
        default=[],
        metavar="PRODUCT=URL",
    )
    return parser.parse_args(argv)


def _parse_backends(values: list[str]) -> tuple[tuple[str, str], ...]:
    parsed: dict[str, str] = {}
    for value in values:
        product, separator, url = value.partition("=")
        if not separator or product not in PRODUCTS or product in parsed:
            raise TunnelGatewayError("backends must use unique product=http://loopback:port/mcp")
        parsed[product] = url
    return tuple((product, parsed[product]) for product in PRODUCTS if product in parsed)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        products = normalize_products(args.products or PRODUCTS)
        settings = TunnelGatewaySettings(
            host=args.host,
            port=args.port,
            products=products,
            backend_urls=_parse_backends(args.backend),
        )
    except TunnelGatewayError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    os.umask(0o077)
    uvicorn.run(
        create_gateway_app(settings),
        host=settings.host,
        port=settings.port,
        proxy_headers=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
