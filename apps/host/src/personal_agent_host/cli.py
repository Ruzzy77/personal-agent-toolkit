"""personal-agent-host command line: run, status, install, uninstall, backup."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from personal_agent_sync.errors import SyncError

from personal_agent_host.config import HostConfig, default_config_path, load_host_config

# The NAS export squashes ownership, so keep contents and times but not owners/modes.
RSYNC = ("-rltD", "--delete")
UNITS = (
    "personal-agent-host.service",
    "personal-agent-tunnel.service",
    "personal-agent-backup.service",
    "personal-agent-backup.timer",
)


def _config_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help=f"configuration file (default {default_config_path()})",
    )


def _emit(payload: dict) -> int:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


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
    return _emit(
        {
            "listen": f"{config.listen_host}:{config.listen_port}",
            "allowed_hosts": list(config.allowed_hosts),
            "backup": str(config.backup.target) if config.backup else None,
            "roots": [
                {
                    "id": root.id,
                    "permission": root.permission,
                    "execute": root.execute,
                }
                for root in config.roots
            ],
        }
    )


def _systemctl(*arguments: str) -> None:
    try:
        subprocess.run(
            ["systemctl", "--user", *arguments],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise SyncError(
            "systemd_failed",
            f"systemctl --user {' '.join(arguments)}: {detail.strip()}",
        ) from exc


def _unit_files(config: HostConfig, config_path: Path) -> dict[str, str]:
    prefix = config.prefix
    launcher = prefix / "bin" / "personal-agent-host"
    logs = prefix / "logs"
    units = {
        "personal-agent-host.service": f"""[Unit]
Description=Personal Agent Host (workspace files and sandboxed execution)
After=network-online.target docker.service

[Service]
ExecStart={launcher} run --config {config_path}
Restart=on-failure
RestartSec=3
StandardOutput=append:{logs}/host.log
StandardError=append:{logs}/host.log

[Install]
WantedBy=default.target
""",
    }
    tunnel_token = prefix / "config" / "tunnel.token"
    if tunnel_token.exists():
        units["personal-agent-tunnel.service"] = f"""[Unit]
Description=Personal Agent Host tunnel (cloudflared, outbound only)
After=network-online.target

[Service]
ExecStart={prefix / "bin" / "cloudflared"} --no-autoupdate tunnel run --token-file {tunnel_token}
Restart=on-failure
RestartSec=5
StandardOutput=append:{logs}/tunnel.log
StandardError=append:{logs}/tunnel.log

[Install]
WantedBy=default.target
"""
    if config.backup is not None:
        units["personal-agent-backup.service"] = f"""[Unit]
Description=Personal Agent Host backup to the NAS

[Service]
Type=oneshot
ExecStart={launcher} backup --config {config_path}
StandardOutput=append:{logs}/backup.log
StandardError=append:{logs}/backup.log
"""
        units["personal-agent-backup.timer"] = f"""[Unit]
Description=Daily Personal Agent Host backup

[Timer]
OnCalendar=*-*-* {config.backup.time}:00
Persistent=true

[Install]
WantedBy=timers.target
"""
    return units


def command_install(args: argparse.Namespace) -> int:
    if sys.platform == "darwin":
        raise SyncError("unsupported_platform", "install targets a Linux host")
    config_path = (args.config or default_config_path()).expanduser().resolve()
    config = load_host_config(config_path)
    launcher = config.prefix / "bin" / "personal-agent-host"
    if not launcher.exists():
        raise SyncError(
            "launcher_missing", f"{launcher} is missing; run install-linux.sh"
        )
    for name in ("logs", "state", "jobs"):
        (config.prefix / name).mkdir(mode=0o700, parents=True, exist_ok=True)
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    units = _unit_files(config, config_path)
    for name, body in units.items():
        (unit_dir / name).write_text(body, encoding="utf-8")
    for name in UNITS:
        if name not in units and (unit_dir / name).exists():
            _systemctl("disable", "--now", name)
            (unit_dir / name).unlink()
    _systemctl("daemon-reload")
    enabled = [name for name in units if not name.endswith("backup.service")]
    _systemctl("enable", "--now", *enabled)
    for name in enabled:
        if name.endswith(".service"):
            _systemctl("restart", name)
    subprocess.run(
        ["loginctl", "enable-linger", os.environ.get("USER", "")],
        check=False,
        capture_output=True,
        timeout=30,
    )
    return _emit({"installed": sorted(units), "prefix": str(config.prefix)})


def command_uninstall(args: argparse.Namespace) -> int:
    if sys.platform == "darwin":
        raise SyncError("unsupported_platform", "uninstall targets a Linux host")
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    removed: list[str] = []
    for name in UNITS:
        unit = unit_dir / name
        if unit.exists():
            _systemctl("disable", "--now", name)
            unit.unlink()
            removed.append(name)
    _systemctl("daemon-reload")
    prefix = (args.config or default_config_path()).expanduser().parent.parent
    if args.purge and prefix.is_dir():
        shutil.rmtree(prefix)
    return _emit(
        {
            "removed": removed,
            "prefix": str(prefix),
            "purged": bool(args.purge),
            "note": (
                "detach this device from the service with "
                "`personal-agent-sync detach-device` before purging if it is registered"
            ),
        }
    )


def _rsync(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["rsync", *RSYNC, f"{source}/", f"{destination}/"],
        check=True,
        capture_output=True,
        text=True,
        timeout=6 * 3600,
    )


def _copy_sqlite(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    origin = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    copy = sqlite3.connect(destination)
    try:
        origin.backup(copy)
    finally:
        copy.close()
        origin.close()


def _on_mount(path: Path) -> bool:
    current = path
    while True:
        if os.path.ismount(current):
            return current != Path("/")
        if current.parent == current:
            return False
        current = current.parent


def command_backup(args: argparse.Namespace) -> int:
    config = load_host_config(args.config)
    if config.backup is None:
        raise SyncError("backup_not_configured", "[host.backup] is not configured")
    target = config.backup.target
    if not target.is_dir() or not _on_mount(target):
        raise SyncError("backup_unavailable", f"{target} is not a mounted directory")
    started = time.time()
    destination = target / config.sync.device_id
    copied: list[str] = []
    for source in config.backup.paths:
        if not source.is_dir():
            continue
        _rsync(source, destination / "paths" / source.name)
        copied.append(str(source))
    state = config.sync.data_root
    subprocess.run(
        [
            "rsync",
            *RSYNC,
            "--exclude=*.db",
            "--exclude=*.db-*",
            "--exclude=*.sqlite*",
            "--exclude=runtime.lock",
            f"{state}/",
            f"{destination / 'state'}/",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=6 * 3600,
    )
    databases: list[str] = []
    for db in sorted(state.rglob("*")):
        if db.is_file() and db.suffix in (".db", ".sqlite", ".sqlite3"):
            relative = db.relative_to(state)
            _copy_sqlite(db, destination / "state" / relative)
            databases.append(str(relative))
    config_dir = config.prefix / "config"
    subprocess.run(
        [
            "rsync",
            *RSYNC,
            "--exclude=*.token",
            "--exclude=*.json",
            f"{config_dir}/",
            f"{destination / 'config'}/",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=600,
    )
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    (destination / "last-success").write_text(stamp + "\n", encoding="utf-8")
    return _emit(
        {
            "destination": str(destination),
            "paths": copied,
            "databases": databases,
            "seconds": round(time.time() - started, 1),
            "finished": stamp,
        }
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="personal-agent-host")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="serve the Host MCP endpoint")
    _config_argument(run)
    run.set_defaults(handler=command_run)
    status = subparsers.add_parser("status", help="print the effective configuration")
    _config_argument(status)
    status.set_defaults(handler=command_status)
    install = subparsers.add_parser(
        "install", help="write and enable the systemd user units for this host"
    )
    _config_argument(install)
    install.set_defaults(handler=command_install)
    uninstall = subparsers.add_parser(
        "uninstall", help="disable and remove the systemd user units"
    )
    _config_argument(uninstall)
    uninstall.add_argument(
        "--purge", action="store_true", help="also delete the prefix directory"
    )
    uninstall.set_defaults(handler=command_uninstall)
    backup = subparsers.add_parser("backup", help="copy workspace and state to the NAS")
    _config_argument(backup)
    backup.set_defaults(handler=command_backup)
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except SyncError as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as exc:
        print(f"command_failed: {exc.cmd[0]} exited {exc.returncode}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
