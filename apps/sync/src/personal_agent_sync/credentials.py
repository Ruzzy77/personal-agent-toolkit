"""Store the Sync device bearer credential in macOS Keychain, or a private file on Linux."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from .errors import SyncError

SERVICE = "Personal Agent Sync Device"


def token_file() -> Path:
    """Linux keeps the credential in a private file inside the Host prefix."""

    configured = os.environ.get("PERSONAL_AGENT_SYNC_TOKEN_FILE")
    if configured:
        return Path(configured).expanduser()
    base = Path(os.environ.get("XDG_DATA_HOME", "~/.local/share")).expanduser()
    return base / "personal-agent-host" / "config" / "sync-device.token"


def read_token(device_id: str) -> str:
    environment = os.environ.get("PERSONAL_AGENT_SYNC_TOKEN")
    if environment:
        return environment
    if sys.platform != "darwin":
        try:
            token = token_file().read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise SyncError(
                "credential_unavailable", "Sync device credential file is unavailable"
            ) from exc
        if not token:
            raise SyncError("credential_unavailable", "Sync device credential is empty")
        return token
    try:
        result = subprocess.run(
            [
                "/usr/bin/security",
                "find-generic-password",
                "-s",
                SERVICE,
                "-a",
                device_id,
                "-w",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SyncError(
            "credential_unavailable",
            "Sync device credential is unavailable in Keychain",
        ) from exc
    token = result.stdout.strip()
    if not token:
        raise SyncError("credential_unavailable", "Sync device credential is empty")
    return token


def store_token(device_id: str, token: str) -> None:
    if not token or any(character.isspace() for character in token):
        raise SyncError("invalid_credential", "Sync device credential is invalid")
    if sys.platform != "darwin":
        target = token_file()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.touch(mode=0o600, exist_ok=True)
            target.chmod(0o600)
            target.write_text(token + "\n", encoding="utf-8")
        except OSError as exc:
            raise SyncError(
                "credential_store_failed", "Sync credential file could not be written"
            ) from exc
        return
    try:
        subprocess.run(
            [
                "/usr/bin/security",
                "add-generic-password",
                "-U",
                "-s",
                SERVICE,
                "-a",
                device_id,
                "-w",
                token,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SyncError(
            "credential_store_failed", "Sync credential could not be stored"
        ) from exc
