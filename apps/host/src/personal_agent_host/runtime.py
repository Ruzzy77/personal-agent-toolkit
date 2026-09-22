"""Describe the actual direct-execution Host environment without container probing."""

from __future__ import annotations

import asyncio
import contextlib
import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Any

from personal_agent_host.config import HostConfig


async def _passwordless_sudo() -> bool:
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            "sudo",
            "-n",
            "true",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(process.wait(), timeout=2)
        return process.returncode == 0
    except (OSError, TimeoutError):
        if process is not None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
        return False


async def execution_capabilities(config: HostConfig) -> dict[str, Any]:
    executables = {
        name: value
        for name in (
            "python3",
            "python",
            "node",
            "npm",
            "uv",
            "git",
            "rg",
            "document-files",
        )
        if (value := shutil.which(name))
    }
    return {
        "execution": {
            "mode": "direct_host",
            "user": os.environ.get("USER") or os.environ.get("LOGNAME"),
            "home": str(Path.home()),
            "roots": [
                {"id": root.id, "path": str(root.root)}
                for root in getattr(config, "roots", ())
            ],
            "python": sys.executable,
            "platform": platform.platform(),
            "executables": executables,
            "sudo_passwordless": await _passwordless_sudo(),
            "isolation": "none",
            "source_protection": "logical_root_policy_only",
        },
        "network": {
            "policy": "host_network",
            "inbound": "host_configuration",
            "egress": "host_configuration",
        },
    }
