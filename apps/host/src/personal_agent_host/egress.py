"""HTTPS CONNECT policy and per-job proxy entry point."""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
import os
import socket
from dataclasses import dataclass

MAX_HEADER_BYTES = 16 * 1024
MAX_TUNNEL_BYTES = 1024 * 1024 * 1024
MAX_CONNECTIONS = 32
CONNECT_TIMEOUT_S = 15
IDLE_TIMEOUT_S = 120
LISTEN_PORT = 3128
PROXY_ALIAS = "pah-egress-proxy"


class HostnameError(ValueError):
    pass


def normalize_hostname(value: str) -> str:
    if not isinstance(value, str):
        raise HostnameError("hostname must be a string")
    hostname = value.strip().rstrip(".")
    if not hostname or len(hostname) > 253 or "/" in hostname or "@" in hostname:
        raise HostnameError("hostname is invalid")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise HostnameError("IP literals are not allowed")
    try:
        encoded = hostname.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise HostnameError("hostname is invalid") from exc
    labels = encoded.split(".")
    if len(encoded) > 253 or any(
        not label or len(label) > 63 or label[0] == "-" or label[-1] == "-"
        or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-" for char in label)
        for label in labels
    ):
        raise HostnameError("hostname is invalid")
    return encoded


def is_public_address(value: str | ipaddress._BaseAddress) -> bool:
    try:
        address = value if isinstance(value, ipaddress._BaseAddress) else ipaddress.ip_address(value)
    except ValueError:
        return False
    if address.version != 4:
        return False
    return not any((
        address.is_private, address.is_loopback, address.is_link_local,
        address.is_multicast, address.is_reserved, address.is_unspecified,
        not address.is_global,
    ))


def _resolve_public(hostname: str, port: int) -> list[tuple[int, str]]:
    infos = socket.getaddrinfo(hostname, port, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM)
    addresses: list[tuple[int, str]] = []
    seen: set[tuple[int, str]] = set()
    for family, _, _, _, sockaddr in infos:
        if family != socket.AF_INET:
            continue
        address = sockaddr[0]
        key = (family, address)
        if key not in seen and is_public_address(address):
            seen.add(key)
            addresses.append(key)
    return addresses


def _parse_connect_target(target: str) -> tuple[str, int]:
    if target.count(":") != 1:
        raise HostnameError("CONNECT target must be hostname:443")
    hostname, port_text = target.rsplit(":", 1)
    if port_text != "443":
        raise HostnameError("only HTTPS port 443 is allowed")
    return normalize_hostname(hostname), 443


@dataclass(frozen=True)
class ProxyPolicy:
    allowlist: frozenset[str]

    @classmethod
    def from_values(cls, values: list[str]) -> ProxyPolicy:
        return cls(frozenset(normalize_hostname(value) for value in values))

    def permits(self, hostname: str) -> bool:
        return hostname in self.allowlist


async def _copy(source: asyncio.StreamReader, target: asyncio.StreamWriter) -> None:
    total = 0
    while True:
        data = await asyncio.wait_for(source.read(64 * 1024), timeout=IDLE_TIMEOUT_S)
        if not data:
            with contextlib.suppress(OSError, RuntimeError):
                target.write_eof()
                await asyncio.wait_for(target.drain(), timeout=CONNECT_TIMEOUT_S)
            return
        total += len(data)
        if total > MAX_TUNNEL_BYTES:
            raise ValueError("tunnel byte limit exceeded")
        target.write(data)
        await asyncio.wait_for(target.drain(), timeout=CONNECT_TIMEOUT_S)


async def _connect_resolved(
    addresses: list[tuple[int, str]], port: int
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    last_error: OSError | None = None
    for family, address in addresses:
        try:
            return await asyncio.wait_for(
                asyncio.open_connection(address, port, family=family),
                timeout=CONNECT_TIMEOUT_S,
            )
        except OSError as exc:
            last_error = exc
    raise OSError("approved hostname has no reachable public address") from last_error


async def _read_connect_request(reader: asyncio.StreamReader) -> tuple[str, int]:
    try:
        raw = await asyncio.wait_for(
            reader.readuntil(b"\r\n\r\n"), timeout=CONNECT_TIMEOUT_S
        )
    except (asyncio.LimitOverrunError, asyncio.IncompleteReadError, TimeoutError) as exc:
        raise HostnameError("invalid proxy request") from exc
    if len(raw) > MAX_HEADER_BYTES:
        raise HostnameError("proxy request is too large")
    try:
        method, target, version = raw[:-4].split(b"\r\n", 1)[0].decode("ascii").split(" ")
    except (UnicodeDecodeError, ValueError) as exc:
        raise HostnameError("invalid proxy request") from exc
    if method != "CONNECT" or version not in ("HTTP/1.0", "HTTP/1.1"):
        raise HostnameError("only HTTPS CONNECT is allowed")
    return _parse_connect_target(target)


async def _handle_client(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, policy: ProxyPolicy
) -> None:
    remote_writer: asyncio.StreamWriter | None = None
    try:
        hostname, port = await _read_connect_request(reader)
        if not policy.permits(hostname):
            raise HostnameError("destination is not allowed")
        addresses = await asyncio.to_thread(_resolve_public, hostname, port)
        if not addresses:
            raise HostnameError("destination has no public address")
        remote_reader, remote_writer = await _connect_resolved(addresses, port)
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await asyncio.wait_for(writer.drain(), timeout=CONNECT_TIMEOUT_S)
        await asyncio.gather(_copy(reader, remote_writer), _copy(remote_reader, writer))
    except HostnameError:
        writer.write(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n")
        with contextlib.suppress(OSError, RuntimeError, TimeoutError):
            await asyncio.wait_for(writer.drain(), timeout=CONNECT_TIMEOUT_S)
    except (OSError, ValueError, TimeoutError):
        writer.write(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n")
        with contextlib.suppress(OSError, RuntimeError, TimeoutError):
            await asyncio.wait_for(writer.drain(), timeout=CONNECT_TIMEOUT_S)
    finally:
        if remote_writer is not None:
            remote_writer.close()
            with contextlib.suppress(OSError):
                await remote_writer.wait_closed()
        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()


async def serve(policy: ProxyPolicy, port: int = LISTEN_PORT) -> None:
    semaphore = asyncio.Semaphore(MAX_CONNECTIONS)

    async def limited(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        async with semaphore:
            await _handle_client(reader, writer, policy)

    server = await asyncio.start_server(
        limited, host="0.0.0.0", port=port, limit=MAX_HEADER_BYTES
    )
    async with server:
        await server.serve_forever()


def policy_from_environment() -> ProxyPolicy:
    try:
        values = json.loads(os.environ.get("PAH_EGRESS_ALLOWLIST_JSON", ""))
    except json.JSONDecodeError as exc:
        raise RuntimeError("PAH_EGRESS_ALLOWLIST_JSON is invalid") from exc
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise RuntimeError("PAH_EGRESS_ALLOWLIST_JSON must be a string list")
    return ProxyPolicy.from_values(values)


def main() -> None:
    try:
        port = int(os.environ.get("PAH_EGRESS_PORT", str(LISTEN_PORT)))
    except ValueError as exc:
        raise RuntimeError("PAH_EGRESS_PORT is invalid") from exc
    if not 1024 <= port <= 65535:
        raise RuntimeError("PAH_EGRESS_PORT is invalid")
    asyncio.run(serve(policy_from_environment(), port))


if __name__ == "__main__":
    main()
