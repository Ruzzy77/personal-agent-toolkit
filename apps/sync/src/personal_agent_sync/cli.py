"""Small operator interface for the background Sync app."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import plistlib
import shutil
import sys
from pathlib import Path

from .config import default_config_path, load_config, rewrite_connection_roots
from .credentials import read_token, store_token
from .daemon import SyncDaemon
from .errors import SyncError
from .migration import migrate_local, verify_local, write_discovered_config
from .reconcile import reconcile_all
from .remote import RemoteClient
from .state import SyncState
from .storage import maintain_remote_storage, remote_storage_report
from .work import rebind_local_corpus_roots

LAUNCH_AGENT_LABEL = "dev.personal-agent.sync"


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="personal-agent-sync")
    root.add_argument("--config", type=Path, default=None)
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("run", help="run the outbound Sync daemon")
    commands.add_parser("validate", help="validate local configuration and storage")
    commands.add_parser("reconcile", help="run one local Source reconciliation")
    rebind = commands.add_parser(
        "rebind-root",
        help="authorize a copied or restored Connection root with a new identity",
    )
    rebind.add_argument("connection_key")
    rebind.add_argument("root", type=Path)
    commands.add_parser("status", help="show local queue and Connection status")
    storage_report = commands.add_parser(
        "storage-report", help="show current remote Corpus storage use"
    )
    storage_report.add_argument("--hotspot-limit", type=int, default=10)
    storage_maintain = commands.add_parser(
        "storage-maintain",
        help="clean staging and compact derived or redundant remote metadata",
    )
    storage_maintain.add_argument("--staged-min-age-hours", type=float, default=24)
    storage_maintain.add_argument("--skip-search-index", action="store_true")
    storage_maintain.add_argument("--maximum-batches", type=int, default=1000)
    storage_maintain.add_argument("--unit-metadata-batch-size", type=int, default=2000)
    storage_maintain.add_argument("--unit-metadata-batches", type=int, default=1)
    initialize = commands.add_parser(
        "init-from-corpus",
        help="create a private configuration from remote-visible local Corpus Spaces",
    )
    initialize.add_argument("--service-url", required=True)
    initialize.add_argument("--device-id", default="owner-mac")
    initialize.add_argument("--display-name", default="Owner Mac")
    initialize.add_argument("--corpus-python", type=Path, required=True)
    initialize.add_argument("--document-files-python", type=Path, required=True)
    initialize.add_argument("--corpus-data-root", type=Path, default=None)
    initialize.add_argument("--output", type=Path, default=default_config_path())
    initialize.add_argument("--replace", action="store_true")
    credential = commands.add_parser(
        "set-credential", help="store a device token in Keychain"
    )
    credential.add_argument("token")
    approve = commands.add_parser(
        "approve-remote", help="approve one revision for remote analysis"
    )
    approve.add_argument("connection_key")
    approve.add_argument("document_id")
    approve.add_argument("revision_sha256")
    approve.add_argument("--max-bytes", type=int, required=True)
    migrate = commands.add_parser("import", help="upload a prepared migration payload")
    migrate.add_argument("product", choices=["sense", "hypes", "corpus-metadata"])
    migrate.add_argument("file", type=Path)
    commands.add_parser(
        "migrate-local",
        help="resumably migrate installed Sense, Hypes, and Corpus durable records",
    )
    commands.add_parser(
        "verify-migration",
        help="compare installed durable records with the remote service",
    )
    commands.add_parser(
        "install-agent", help="install and start the per-user launch agent"
    )
    commands.add_parser(
        "uninstall-agent", help="stop and remove the per-user launch agent"
    )
    return root


async def _run(config_path: Path | None) -> None:
    config = load_config(config_path)
    daemon = SyncDaemon(config, read_token(config.device_id))
    await daemon.run()


async def _import(config_path: Path | None, product: str, source: Path) -> dict:
    config = load_config(config_path)
    token = read_token(config.device_id)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SyncError(
            "invalid_import", "migration payload could not be read"
        ) from exc
    remote = RemoteClient(config, token)
    try:
        return await remote.import_payload(product, value)
    finally:
        await remote.close()


def _status(state: SyncState) -> dict:
    with state.connect() as connection:
        connections = [
            dict(row)
            for row in connection.execute(
                """
                SELECT connection_key, location_state, access_scope, permission,
                       corpus_id, analyzer_route, updated_at
                FROM connections ORDER BY connection_key
                """
            )
        ]
        pending = connection.execute("SELECT COUNT(*) FROM change_queue").fetchone()[0]
        failures = [
            dict(row)
            for row in connection.execute(
                """
                SELECT connection_key, document_id, event_kind, attempt_count,
                       last_error_code, next_attempt_at
                FROM change_queue WHERE last_error_code IS NOT NULL
                ORDER BY next_attempt_at LIMIT 20
                """
            )
        ]
    return {
        "connections": connections,
        "pending_changes": pending,
        "recent_failures": failures,
    }


def _launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT_LABEL}.plist"


def _install_agent(config_path: Path | None, data_root: Path) -> dict:
    executable = Path(sys.executable).parent / "personal-agent-sync"
    if not executable.is_file():
        discovered = shutil.which("personal-agent-sync")
        executable = Path(discovered) if discovered else executable
    if not executable.is_file():
        raise SyncError(
            "executable_not_found", "personal-agent-sync is not installed on PATH"
        )
    source = (config_path or default_config_path()).expanduser().resolve()
    launch_agents = _launch_agent_path().parent
    launch_agents.mkdir(parents=True, exist_ok=True)
    path = _launch_agent_path()
    payload = {
        "Label": LAUNCH_AGENT_LABEL,
        "ProgramArguments": [str(executable), "--config", str(source), "run"],
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ProcessType": "Background",
        "ThrottleInterval": 10,
        "StandardOutPath": str(data_root / "sync.log"),
        "StandardErrorPath": str(data_root / "sync-error.log"),
    }
    temporary = path.with_suffix(".plist.tmp")
    with temporary.open("wb") as stream:
        plistlib.dump(payload, stream)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    os.spawnlp(
        os.P_WAIT, "launchctl", "launchctl", "bootout", f"gui/{os.getuid()}", str(path)
    )
    status = os.spawnlp(
        os.P_WAIT,
        "launchctl",
        "launchctl",
        "bootstrap",
        f"gui/{os.getuid()}",
        str(path),
    )
    if status != 0:
        raise SyncError("launch_agent_failed", "Sync launch agent could not be started")
    return {"installed": True, "label": LAUNCH_AGENT_LABEL}


def _uninstall_agent() -> dict:
    path = _launch_agent_path()
    if path.exists():
        os.spawnlp(
            os.P_WAIT,
            "launchctl",
            "launchctl",
            "bootout",
            f"gui/{os.getuid()}",
            str(path),
        )
        path.unlink(missing_ok=True)
    return {"installed": False, "label": LAUNCH_AGENT_LABEL}


def main() -> None:
    arguments = parser().parse_args()
    try:
        if arguments.command == "init-from-corpus":
            result = write_discovered_config(
                output=arguments.output,
                service_url=arguments.service_url,
                device_id=arguments.device_id,
                display_name=arguments.display_name,
                corpus_python=arguments.corpus_python,
                document_files_python=arguments.document_files_python,
                corpus_data_root=arguments.corpus_data_root,
                replace=arguments.replace,
            )
            print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
            return
        config = load_config(arguments.config)
        if arguments.command == "run":
            asyncio.run(_run(arguments.config))
            return
        if arguments.command == "set-credential":
            store_token(config.device_id, arguments.token)
            result = {"stored": True, "device_id": config.device_id}
        elif arguments.command == "validate":
            SyncState(config)
            read_token(config.device_id)
            result = {"valid": True, "device_id": config.device_id}
        elif arguments.command == "reconcile":
            result = {"connections": reconcile_all(SyncState(config))}
        elif arguments.command == "rebind-root":
            state = SyncState(config)
            result = state.rebind_connection_root(
                arguments.connection_key, arguments.root
            )
            result["local_corpus"] = rebind_local_corpus_roots(
                config,
                set(result["connection_keys"]),
                Path(str(result["root"])),
            )
            rewrite_connection_roots(
                arguments.config,
                set(result["connection_keys"]),
                Path(str(result["root"])),
            )
            result["config_updated"] = True
        elif arguments.command == "status":
            result = _status(SyncState(config))
        elif arguments.command == "storage-report":
            result = asyncio.run(
                remote_storage_report(
                    config,
                    hotspot_limit=arguments.hotspot_limit,
                )
            )
        elif arguments.command == "storage-maintain":
            result = asyncio.run(
                maintain_remote_storage(
                    config,
                    staged_min_age_hours=arguments.staged_min_age_hours,
                    compact_search_index=not arguments.skip_search_index,
                    maximum_batches_per_corpus=arguments.maximum_batches,
                    unit_metadata_batch_size=arguments.unit_metadata_batch_size,
                    maximum_unit_metadata_batches_per_corpus=(
                        arguments.unit_metadata_batches
                    ),
                )
            )
        elif arguments.command == "approve-remote":
            state = SyncState(config)
            state.approve_remote(
                arguments.connection_key,
                arguments.document_id,
                arguments.revision_sha256,
                arguments.max_bytes,
            )
            result = {"approved": True, "document_id": arguments.document_id}
        elif arguments.command == "import":
            result = asyncio.run(
                _import(arguments.config, arguments.product, arguments.file)
            )
        elif arguments.command == "migrate-local":
            result = asyncio.run(migrate_local(config, read_token(config.device_id)))
        elif arguments.command == "verify-migration":
            result = asyncio.run(verify_local(config, read_token(config.device_id)))
        elif arguments.command == "install-agent":
            result = _install_agent(arguments.config, config.data_root)
        elif arguments.command == "uninstall-agent":
            result = _uninstall_agent()
        else:  # pragma: no cover
            raise SyncError("invalid_command", "command is unsupported")
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    except SyncError as error:
        print(
            json.dumps(
                {"ok": False, "error": {"code": error.code, "message": str(error)}},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        raise SystemExit(2) from error


if __name__ == "__main__":
    main()
