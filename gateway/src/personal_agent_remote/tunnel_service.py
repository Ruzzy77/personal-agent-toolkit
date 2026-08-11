"""Run personal Secure MCP Tunnel profiles from macOS launchd without storing the API key."""

from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import NoReturn

from personal_agent_remote.installed_products import (
    InstalledProduct,
    InstalledProductError,
    discover_codex_installations,
    installation_from_root,
    normalize_products,
    parse_product_roots,
)
from personal_agent_remote.tunnel import CONNECTION_MODES, PRODUCTS
from personal_agent_remote.tunnel_gateway import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    validate_loopback_host,
)

DEFAULT_KEYCHAIN_SERVICE = "personal-agent-tunnel-control-plane"
DEFAULT_KEYCHAIN_ACCOUNT = "user"
LABEL_PREFIX = "com.ruzzy77.personal-agent-tunnel"
PRODUCT_MCP_PORTS = {
    "sense": 18181,
    "corpus": 18182,
    "hypes": 18183,
}


class TunnelServiceError(ValueError):
    """The local tunnel service configuration is unsafe or unavailable."""


def _owned_regular_file(path: Path, *, executable: bool, description: str) -> Path:
    expanded = path.expanduser()
    if not expanded.is_absolute():
        raise TunnelServiceError(f"{description} must be an absolute path")
    try:
        metadata = expanded.lstat()
        canonical = expanded.resolve(strict=True)
    except OSError as exc:
        raise TunnelServiceError(f"{description} is unavailable") from exc
    if (
        canonical != expanded
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_mode & 0o022
        or (executable and not metadata.st_mode & stat.S_IXUSR)
    ):
        raise TunnelServiceError(
            f"{description} must be an owned, non-writable regular file"
        )
    return canonical


def _profile_name(product: str, *, connection_mode: str) -> str:
    if product not in PRODUCTS:
        raise TunnelServiceError("product must be sense, corpus, or hypes")
    if connection_mode not in CONNECTION_MODES:
        raise TunnelServiceError("connection mode must be direct or gateway")
    if connection_mode == "direct":
        return f"personal-agent-{product}"
    return f"personal-agent-gateway-{product}"


def _profile_path(
    product: str,
    *,
    connection_mode: str = "direct",
    home: Path | None = None,
) -> Path:
    base = (home or Path.home()).expanduser()
    return base / ".config" / "tunnel-client" / (
        f"{_profile_name(product, connection_mode=connection_mode)}.yaml"
    )


def _read_keychain_secret(
    *,
    service: str,
    account: str,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> str:
    if not service or not account or any(ord(char) < 32 for char in service + account):
        raise TunnelServiceError("Keychain identity is invalid")
    completed = runner(
        [
            "/usr/bin/security",
            "find-generic-password",
            "-w",
            "-s",
            service,
            "-a",
            account,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    secret = completed.stdout.rstrip("\r\n") if completed.returncode == 0 else ""
    if not secret or len(secret) > 8192 or any(ord(char) < 32 for char in secret):
        raise TunnelServiceError("the tunnel runtime key is unavailable in Keychain")
    return secret


def run_profile(
    *,
    product: str,
    tunnel_client: Path,
    connection_mode: str = "direct",
    keychain_service: str = DEFAULT_KEYCHAIN_SERVICE,
    keychain_account: str = DEFAULT_KEYCHAIN_ACCOUNT,
    home: Path | None = None,
    secret_reader: Callable[..., str] = _read_keychain_secret,
    execve: Callable[[str, Sequence[str], dict[str, str]], object] = os.execve,
) -> NoReturn:
    client = _owned_regular_file(
        tunnel_client,
        executable=True,
        description="tunnel client",
    )
    profile = _owned_regular_file(
        _profile_path(product, connection_mode=connection_mode, home=home),
        executable=False,
        description=f"{product} tunnel profile",
    )
    if stat.S_IMODE(profile.stat().st_mode) != 0o600:
        raise TunnelServiceError(f"{product} tunnel profile must use mode 0600")
    secret = secret_reader(service=keychain_service, account=keychain_account)
    environment = dict(os.environ)
    environment["CONTROL_PLANE_API_KEY"] = secret
    argv = [
        str(client),
        "run",
        "--profile",
        _profile_name(product, connection_mode=connection_mode),
    ]
    execve(str(client), argv, environment)
    raise AssertionError("execve returned unexpectedly")


def _gateway_profile_command(product: str, *, tunnel_client: Path) -> list[str]:
    return [
        str(tunnel_client),
        "run",
        "--profile",
        _profile_name(product, connection_mode="gateway"),
    ]


def _resolve_uv_program(path: Path | None = None) -> Path:
    candidate = path
    if candidate is None:
        discovered = shutil.which("uv")
        if discovered is None:
            raise TunnelServiceError("uv is required to install the gateway LaunchAgent")
        candidate = Path(discovered).resolve(strict=True)
    return _owned_regular_file(
        candidate,
        executable=True,
        description="uv program",
    )


def _product_server_environment(
    product: str,
    *,
    host: str,
    port: int,
) -> dict[str, str]:
    environment = dict(os.environ)
    prefix = product.upper()
    environment[f"{prefix}_MCP_TRANSPORT"] = "streamable-http"
    environment[f"{prefix}_MCP_HOST"] = host
    environment[f"{prefix}_MCP_PORT"] = str(port)
    environment[f"{prefix}_MCP_PATH"] = "/mcp"
    environment.pop("CONTROL_PLANE_API_KEY", None)
    return environment


def _product_backend_url(product: str, *, host: str) -> str:
    url_host = f"[{host}]" if ":" in host else host
    return f"http://{url_host}:{PRODUCT_MCP_PORTS[product]}/mcp"


def _product_mcp_ready(*, product: str, host: str) -> bool:
    request = urllib.request.Request(
        _product_backend_url(product, host=host),
        data=(
            b'{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
        ),
        headers={
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=1.0) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def _gateway_health_ready(*, host: str, port: int) -> bool:
    url_host = f"[{host}]" if ":" in host else host
    try:
        with urllib.request.urlopen(
            f"http://{url_host}:{port}/healthz",
            timeout=1.0,
        ) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def _stop_children(children: Sequence[subprocess.Popen[bytes]]) -> None:
    running = [child for child in children if child.poll() is None]
    for child in reversed(running):
        child.terminate()
    deadline = time.monotonic() + 5.0
    while running and time.monotonic() < deadline:
        running = [child for child in running if child.poll() is None]
        if running:
            time.sleep(0.05)
    for child in reversed(running):
        child.kill()
    for child in children:
        with suppress(subprocess.TimeoutExpired):
            child.wait(timeout=1.0)


def run_gateway_bundle(
    *,
    gateway_program: Path,
    tunnel_client: Path,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    keychain_service: str = DEFAULT_KEYCHAIN_SERVICE,
    keychain_account: str = DEFAULT_KEYCHAIN_ACCOUNT,
    home: Path | None = None,
    products: Sequence[str] = PRODUCTS,
    product_installations: Mapping[str, InstalledProduct] | None = None,
    secret_reader: Callable[..., str] = _read_keychain_secret,
    process_factory: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
    product_ready: Callable[..., bool] = _product_mcp_ready,
    health_ready: Callable[..., bool] = _gateway_health_ready,
    stop_requested: Callable[[], bool] = lambda: False,
) -> int:
    """Supervise installed product MCPs, one gateway, and selected tunnel clients."""

    gateway = _owned_regular_file(
        gateway_program,
        executable=True,
        description="tunnel gateway program",
    )
    client = _owned_regular_file(
        tunnel_client,
        executable=True,
        description="tunnel client",
    )
    try:
        loopback = validate_loopback_host(host)
    except ValueError as exc:
        raise TunnelServiceError(str(exc)) from exc
    if not 1 <= port <= 65535:
        raise TunnelServiceError("tunnel gateway port must be between 1 and 65535")
    try:
        selected = normalize_products(products)
    except InstalledProductError as exc:
        raise TunnelServiceError(str(exc)) from exc
    installations = dict(product_installations or {})
    if set(installations) != set(selected):
        raise TunnelServiceError(
            "installed product set must exactly match the selected gateway products"
        )
    for product in selected:
        profile = _owned_regular_file(
            _profile_path(product, connection_mode="gateway", home=home),
            executable=False,
            description=f"{product} gateway tunnel profile",
        )
        if stat.S_IMODE(profile.stat().st_mode) != 0o600:
            raise TunnelServiceError(f"{product} gateway tunnel profile must use mode 0600")

    children: list[subprocess.Popen[bytes]] = []
    try:
        for product in selected:
            installation = installations[product]
            product_port = PRODUCT_MCP_PORTS[product]
            product_child = process_factory(
                [str(installation.launcher)],
                env=_product_server_environment(
                    product,
                    host=loopback,
                    port=product_port,
                ),
            )
            children.append(product_child)
            deadline = time.monotonic() + 120.0
            while not product_ready(product=product, host=loopback):
                return_code = product_child.poll()
                if return_code is not None:
                    return return_code or 1
                if stop_requested():
                    return 0
                if time.monotonic() >= deadline:
                    raise TunnelServiceError(f"{product} installed MCP did not become ready")
                time.sleep(0.1)

        gateway_argv = [str(gateway), "--host", loopback, "--port", str(port)]
        for product in selected:
            gateway_argv.extend(("--product", product))
            gateway_argv.extend(
                ("--backend", f"{product}={_product_backend_url(product, host=loopback)}")
            )
        gateway_environment = {
            key: value
            for key, value in os.environ.items()
            if key != "CONTROL_PLANE_API_KEY"
        }
        gateway_child = process_factory(gateway_argv, env=gateway_environment)
        children.append(gateway_child)
        deadline = time.monotonic() + 30.0
        while not health_ready(host=loopback, port=port):
            return_code = gateway_child.poll()
            if return_code is not None:
                return return_code or 1
            if stop_requested():
                return 0
            if time.monotonic() >= deadline:
                raise TunnelServiceError("tunnel gateway did not become ready")
            time.sleep(0.1)

        secret = secret_reader(service=keychain_service, account=keychain_account)
        tunnel_environment = dict(os.environ)
        tunnel_environment["CONTROL_PLANE_API_KEY"] = secret
        for product in selected:
            children.append(
                process_factory(
                    _gateway_profile_command(product, tunnel_client=client),
                    env=tunnel_environment,
                )
            )

        while not stop_requested():
            for child in children:
                return_code = child.poll()
                if return_code is not None:
                    return return_code or 1
            time.sleep(0.25)
        return 0
    finally:
        _stop_children(children)


def _absolute_owned_directory(
    path: Path,
    *,
    description: str,
    require_private: bool,
) -> Path:
    expanded = path.expanduser()
    if not expanded.is_absolute():
        raise TunnelServiceError(f"{description} must be an absolute path")
    existed = expanded.exists()
    expanded.mkdir(parents=True, exist_ok=True, mode=0o700)
    if require_private and not existed:
        expanded.chmod(0o700)
    metadata = expanded.lstat()
    mode = stat.S_IMODE(metadata.st_mode)
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or mode & 0o022
        or (require_private and mode != 0o700)
        or expanded.resolve(strict=True) != expanded
    ):
        qualifier = "private " if require_private else "non-writable "
        raise TunnelServiceError(f"{description} must be an owned {qualifier}directory")
    return expanded


def launch_agent_payload(
    *,
    product: str,
    runtime_program: Path,
    tunnel_client: Path,
    log_directory: Path,
    keychain_service: str = DEFAULT_KEYCHAIN_SERVICE,
    keychain_account: str = DEFAULT_KEYCHAIN_ACCOUNT,
    connection_mode: str = "direct",
) -> dict[str, object]:
    _profile_name(product, connection_mode=connection_mode)
    program = _owned_regular_file(
        runtime_program,
        executable=True,
        description="tunnel service program",
    )
    client = _owned_regular_file(
        tunnel_client,
        executable=True,
        description="tunnel client",
    )
    logs = _absolute_owned_directory(
        log_directory,
        description="tunnel log directory",
        require_private=True,
    )
    return {
        "Label": (
            f"{LABEL_PREFIX}.{product}"
            if connection_mode == "direct"
            else f"{LABEL_PREFIX}.gateway.{product}"
        ),
        "ProgramArguments": [
            str(program),
            "run",
            "--product",
            product,
            "--connection-mode",
            connection_mode,
            "--tunnel-client",
            str(client),
            "--keychain-service",
            keychain_service,
            "--keychain-account",
            keychain_account,
        ],
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Background",
        "ThrottleInterval": 10,
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": str(
            logs
            / (
                f"{product}.stderr.log"
                if connection_mode == "direct"
                else f"gateway-{product}.stderr.log"
            )
        ),
    }


def gateway_launch_agent_payload(
    *,
    runtime_program: Path,
    gateway_program: Path,
    tunnel_client: Path,
    log_directory: Path,
    keychain_service: str = DEFAULT_KEYCHAIN_SERVICE,
    keychain_account: str = DEFAULT_KEYCHAIN_ACCOUNT,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    products: Sequence[str] = PRODUCTS,
    product_installations: Mapping[str, InstalledProduct] | None = None,
    uv_program: Path | None = None,
) -> dict[str, object]:
    runtime = _owned_regular_file(
        runtime_program,
        executable=True,
        description="tunnel service program",
    )
    program = _owned_regular_file(
        gateway_program,
        executable=True,
        description="tunnel gateway program",
    )
    client = _owned_regular_file(
        tunnel_client,
        executable=True,
        description="tunnel client",
    )
    uv = _resolve_uv_program(uv_program)
    try:
        loopback = validate_loopback_host(host)
    except ValueError as exc:
        raise TunnelServiceError(str(exc)) from exc
    if not 1 <= port <= 65535:
        raise TunnelServiceError("tunnel gateway port must be between 1 and 65535")
    try:
        selected = normalize_products(products)
    except InstalledProductError as exc:
        raise TunnelServiceError(str(exc)) from exc
    installations = dict(product_installations or {})
    if set(installations) != set(selected):
        raise TunnelServiceError(
            "installed product set must exactly match the selected gateway products"
        )
    logs = _absolute_owned_directory(
        log_directory,
        description="tunnel log directory",
        require_private=True,
    )
    arguments = [
        str(runtime),
        "run-gateway",
        "--gateway-program",
        str(program),
        "--tunnel-client",
        str(client),
        "--keychain-service",
        keychain_service,
        "--keychain-account",
        keychain_account,
        "--host",
        loopback,
        "--port",
        str(port),
    ]
    for product in selected:
        arguments.extend(("--product", product))
        arguments.extend(("--product-root", f"{product}={installations[product].root}"))
    return {
        "Label": f"{LABEL_PREFIX}.gateway",
        "ProgramArguments": arguments,
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Background",
        "ThrottleInterval": 10,
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": str(logs / "gateway.stderr.log"),
        "EnvironmentVariables": {"UV": str(uv)},
    }


def _write_launch_agent(target: Path, payload: dict[str, object]) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            plistlib.dump(payload, handle, fmt=plistlib.FMT_XML, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()


def install_launch_agents(
    *,
    runtime_program: Path,
    tunnel_client: Path,
    launch_agents_directory: Path,
    log_directory: Path,
    keychain_service: str = DEFAULT_KEYCHAIN_SERVICE,
    keychain_account: str = DEFAULT_KEYCHAIN_ACCOUNT,
    connection_mode: str = "direct",
) -> list[Path]:
    agents = _absolute_owned_directory(
        launch_agents_directory,
        description="LaunchAgents directory",
        require_private=False,
    )
    written: list[Path] = []
    for product in PRODUCTS:
        payload = launch_agent_payload(
            product=product,
            runtime_program=runtime_program,
            tunnel_client=tunnel_client,
            log_directory=log_directory,
            keychain_service=keychain_service,
            keychain_account=keychain_account,
            connection_mode=connection_mode,
        )
        label = payload["Label"]
        assert isinstance(label, str)
        target = agents / f"{label}.plist"
        _write_launch_agent(target, payload)
        written.append(target)
    return written


def install_gateway_launch_agent(
    *,
    runtime_program: Path,
    gateway_program: Path,
    tunnel_client: Path,
    launch_agents_directory: Path,
    log_directory: Path,
    keychain_service: str = DEFAULT_KEYCHAIN_SERVICE,
    keychain_account: str = DEFAULT_KEYCHAIN_ACCOUNT,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    products: Sequence[str] = PRODUCTS,
    product_roots: Mapping[str, Path] | None = None,
    uv_program: Path | None = None,
) -> list[Path]:
    agents = _absolute_owned_directory(
        launch_agents_directory,
        description="LaunchAgents directory",
        require_private=False,
    )
    try:
        selected = normalize_products(products)
        installations = (
            {
                product: installation_from_root(product, product_roots[product])
                for product in selected
            }
            if product_roots is not None
            else discover_codex_installations(products=selected)
        )
    except (InstalledProductError, KeyError) as exc:
        raise TunnelServiceError(str(exc)) from exc
    gateway_payload = gateway_launch_agent_payload(
        runtime_program=runtime_program,
        gateway_program=gateway_program,
        tunnel_client=tunnel_client,
        log_directory=log_directory,
        keychain_service=keychain_service,
        keychain_account=keychain_account,
        host=host,
        port=port,
        products=selected,
        product_installations=installations,
        uv_program=uv_program,
    )
    gateway_target = agents / f"{LABEL_PREFIX}.gateway.plist"
    _write_launch_agent(gateway_target, gateway_payload)
    return [gateway_target]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run or install personal Secure MCP Tunnel launch agents"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--product", choices=PRODUCTS, required=True)
    run_parser.add_argument(
        "--connection-mode",
        choices=CONNECTION_MODES,
        default="direct",
    )
    run_parser.add_argument("--tunnel-client", type=Path, required=True)
    run_parser.add_argument("--keychain-service", default=DEFAULT_KEYCHAIN_SERVICE)
    run_parser.add_argument("--keychain-account", default=DEFAULT_KEYCHAIN_ACCOUNT)

    run_gateway_parser = subparsers.add_parser("run-gateway")
    run_gateway_parser.add_argument(
        "--gateway-program",
        type=Path,
        required=True,
    )
    run_gateway_parser.add_argument("--tunnel-client", type=Path, required=True)
    run_gateway_parser.add_argument("--keychain-service", default=DEFAULT_KEYCHAIN_SERVICE)
    run_gateway_parser.add_argument("--keychain-account", default=DEFAULT_KEYCHAIN_ACCOUNT)
    run_gateway_parser.add_argument("--host", default=DEFAULT_HOST)
    run_gateway_parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    run_gateway_parser.add_argument(
        "--product",
        action="append",
        choices=PRODUCTS,
        dest="products",
    )
    run_gateway_parser.add_argument(
        "--product-root",
        action="append",
        default=[],
        metavar="PRODUCT=/ABSOLUTE/PATH",
    )

    install_parser = subparsers.add_parser("install-launch-agents")
    install_parser.add_argument("--runtime-program", type=Path, default=Path(sys.argv[0]))
    install_parser.add_argument("--tunnel-client", type=Path, required=True)
    install_parser.add_argument(
        "--launch-agents-directory",
        type=Path,
        default=Path.home() / "Library" / "LaunchAgents",
    )
    install_parser.add_argument(
        "--log-directory",
        type=Path,
        default=Path.home() / "Library" / "Logs" / "PersonalAgentTunnel",
    )
    install_parser.add_argument("--keychain-service", default=DEFAULT_KEYCHAIN_SERVICE)
    install_parser.add_argument("--keychain-account", default=DEFAULT_KEYCHAIN_ACCOUNT)

    gateway_parser = subparsers.add_parser("install-gateway-launch-agent")
    gateway_parser.add_argument("--runtime-program", type=Path, default=Path(sys.argv[0]))
    gateway_parser.add_argument(
        "--gateway-program",
        type=Path,
        default=Path(sys.argv[0]).with_name("personal-agent-tunnel-gateway"),
    )
    gateway_parser.add_argument("--tunnel-client", type=Path, required=True)
    gateway_parser.add_argument(
        "--launch-agents-directory",
        type=Path,
        default=Path.home() / "Library" / "LaunchAgents",
    )
    gateway_parser.add_argument(
        "--log-directory",
        type=Path,
        default=Path.home() / "Library" / "Logs" / "PersonalAgentTunnel",
    )
    gateway_parser.add_argument("--keychain-service", default=DEFAULT_KEYCHAIN_SERVICE)
    gateway_parser.add_argument("--keychain-account", default=DEFAULT_KEYCHAIN_ACCOUNT)
    gateway_parser.add_argument("--host", default=DEFAULT_HOST)
    gateway_parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    gateway_parser.add_argument(
        "--uv-program",
        type=Path,
        help="absolute uv executable recorded for the restricted LaunchAgent environment",
    )
    gateway_parser.add_argument(
        "--product",
        action="append",
        choices=PRODUCTS,
        dest="products",
    )
    gateway_parser.add_argument(
        "--product-root",
        action="append",
        default=[],
        metavar="PRODUCT=/ABSOLUTE/PATH",
        help="override Codex installed-plugin discovery",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "run":
            run_profile(
                product=args.product,
                tunnel_client=args.tunnel_client,
                connection_mode=args.connection_mode,
                keychain_service=args.keychain_service,
                keychain_account=args.keychain_account,
            )
        elif args.command == "run-gateway":
            stop_event = threading.Event()

            def request_stop(_signum: int, _frame: object) -> None:
                stop_event.set()

            signal.signal(signal.SIGTERM, request_stop)
            signal.signal(signal.SIGINT, request_stop)
            try:
                selected = normalize_products(args.products or PRODUCTS)
                roots = parse_product_roots(args.product_root)
                if set(roots) != set(selected):
                    raise InstalledProductError(
                        "product roots must exactly match the selected gateway products"
                    )
                installations = {
                    product: installation_from_root(product, roots[product])
                    for product in selected
                }
            except InstalledProductError as exc:
                raise TunnelServiceError(str(exc)) from exc
            return run_gateway_bundle(
                gateway_program=args.gateway_program,
                tunnel_client=args.tunnel_client,
                host=args.host,
                port=args.port,
                keychain_service=args.keychain_service,
                keychain_account=args.keychain_account,
                products=selected,
                product_installations=installations,
                stop_requested=stop_event.is_set,
            )
        elif args.command == "install-launch-agents":
            paths = install_launch_agents(
                runtime_program=args.runtime_program,
                tunnel_client=args.tunnel_client,
                launch_agents_directory=args.launch_agents_directory,
                log_directory=args.log_directory,
                keychain_service=args.keychain_service,
                keychain_account=args.keychain_account,
            )
            print("\n".join(str(path) for path in paths))
            return 0
        else:
            selected = normalize_products(args.products or PRODUCTS)
            roots = parse_product_roots(args.product_root) if args.product_root else None
            paths = install_gateway_launch_agent(
                runtime_program=args.runtime_program,
                gateway_program=args.gateway_program,
                tunnel_client=args.tunnel_client,
                launch_agents_directory=args.launch_agents_directory,
                log_directory=args.log_directory,
                keychain_service=args.keychain_service,
                keychain_account=args.keychain_account,
                host=args.host,
                port=args.port,
                products=selected,
                product_roots=roots,
                uv_program=args.uv_program,
            )
            print("\n".join(str(path) for path in paths))
            return 0
    except (TunnelServiceError, InstalledProductError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
