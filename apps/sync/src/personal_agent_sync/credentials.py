"""Store the Sync device bearer credential in macOS Keychain."""

from __future__ import annotations

import os
import subprocess

from .errors import SyncError

SERVICE = "Personal Agent Sync Device"


def read_token(device_id: str) -> str:
    environment = os.environ.get("PERSONAL_AGENT_SYNC_TOKEN")
    if environment:
        return environment
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
