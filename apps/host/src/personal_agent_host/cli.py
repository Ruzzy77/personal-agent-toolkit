"""personal-agent-host command line."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from personal_agent_sync.errors import SyncError

from personal_agent_host.config import default_config_path, load_host_config


def _config_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help=f"configuration file (default {default_config_path()})",
    )


def command_run(args: argparse.Namespace) -> int:
    import uvicorn

    from personal_agent_host.app import build_app

    config = load_host_config(args.config)
    uvicorn.run(
        build_app(config),
        host=config.listen_host,
        port=config.listen_port,
        log_level="info",
        access_log=False,
    )
    return 0


def command_status(args: argparse.Namespace) -> int:
    config = load_host_config(args.config)
    print(
        json.dumps(
            {
                "listen": f"{config.listen_host}:{config.listen_port}",
                "allowed_hosts": list(config.allowed_hosts),
                "roots": [
                    {
                        "id": root.id,
                        "permission": root.permission,
                        "execute": root.execute,
                    }
                    for root in config.roots
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="personal-agent-host")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="serve the Host MCP endpoint")
    _config_argument(run)
    run.set_defaults(handler=command_run)
    status = subparsers.add_parser("status", help="print the effective configuration")
    _config_argument(status)
    status.set_defaults(handler=command_status)
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except SyncError as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
