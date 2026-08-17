"""Loopback proxy for independently installed personal-product MCP servers."""

from __future__ import annotations

import argparse
import ipaddress
import os
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from personal_agent_remote.installed_products import normalize_products

PRODUCTS = ("sense", "corpus", "hypes")
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 18180
MAX_PROXY_BODY_BYTES = 16 * 1024 * 1024


class TunnelGatewayError(ValueError):
    """The local gateway configuration is invalid."""


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

        backends = dict(self.backend_urls)
        if len(backends) != len(self.backend_urls) or set(backends) != set(products):
            raise TunnelGatewayError(
                "tunnel gateway backends must exactly match the selected products"
            )
        object.__setattr__(
            self,
            "backend_urls",
            tuple(
                (product, _validate_backend_url(backends[product], product=product))
                for product in products
            ),
        )

    @property
    def backends(self) -> dict[str, str]:
        return dict(self.backend_urls)

    @property
    def trusted_hosts(self) -> tuple[str, ...]:
        if self.allowed_hosts:
            return tuple(host.split(":", 1)[0] for host in self.allowed_hosts)
        return (self.host, "127.0.0.1", "localhost", "[::1]")


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
    return StreamingResponse(
        _proxy_response_body(response),
        status_code=response.status_code,
        headers=_proxy_response_headers(response),
    )


def create_gateway_app(
    settings: TunnelGatewaySettings,
    *,
    http_transport: httpx.AsyncBaseTransport | None = None,
) -> Starlette:
    backends = settings.backends
    client_holder: dict[str, httpx.AsyncClient] = {}

    @asynccontextmanager
    async def lifespan(_: Starlette):
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(660.0, connect=5.0),
            trust_env=False,
            transport=http_transport,
        ) as client:
            client_holder["client"] = client
            try:
                yield
            finally:
                client_holder.clear()

    async def health(_: Request) -> JSONResponse:
        return JSONResponse(
            {
                "ok": True,
                "mode": "personal-secure-mcp-tunnel-gateway",
                "products": list(settings.products),
                "product_runtime": "installed-plugin",
            }
        )

    routes = [Route("/healthz", health, methods=["GET"])]
    for product in settings.products:
        target = backends[product]

        async def proxy(request: Request, *, backend: str = target) -> Response:
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
                    backend,
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
                Route(f"/{product}/mcp", proxy, methods=["GET", "POST", "DELETE"]),
                Route(f"/{product}/mcp/", proxy, methods=["GET", "POST", "DELETE"]),
            )
        )

    app = Starlette(
        routes=routes,
        lifespan=lifespan,
        middleware=[
            Middleware(
                TrustedHostMiddleware,
                allowed_hosts=list(settings.trusted_hosts),
            )
        ],
    )
    app.state.settings = settings
    return app


def _parse_backends(values: list[str]) -> tuple[tuple[str, str], ...]:
    parsed: dict[str, str] = {}
    for value in values:
        product, separator, url = value.partition("=")
        if not separator or product not in PRODUCTS or product in parsed:
            raise TunnelGatewayError(
                "backends must use unique product=http://loopback:port/mcp"
            )
        parsed[product] = url
    return tuple((product, parsed[product]) for product in PRODUCTS if product in parsed)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the loopback gateway for selected personal Secure MCP Tunnels"
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
    parser.add_argument("--product", action="append", choices=PRODUCTS, dest="products")
    parser.add_argument(
        "--backend",
        action="append",
        default=[],
        metavar="PRODUCT=URL",
        required=True,
    )
    return parser.parse_args(argv)


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
    except (TunnelGatewayError, ValueError) as exc:
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
