"""Fixed-interface client for the administrator-installed egress guard."""

from __future__ import annotations

import asyncio
import re

from personal_agent_host.files import ToolError

SUDO = "/usr/bin/sudo"
GUARD = "/usr/local/libexec/personal-agent-host-egress-guard"
NETWORK = re.compile(r"pah-egress-[0-9a-f]{12}")


async def invoke(action: str, network: str) -> None:
    """Run only a fixed guard action for one Host-owned network."""

    if action not in {"attach", "check", "detach"} or not NETWORK.fullmatch(network):
        raise ToolError("egress_unavailable", "egress guard request is invalid")
    try:
        process = await asyncio.create_subprocess_exec(
            SUDO, "-n", GUARD, action, network,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=20)
    except (OSError, TimeoutError) as exc:
        raise ToolError(
            "egress_unavailable", "public egress guard is unavailable"
        ) from exc
    if process.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()
        raise ToolError(
            "egress_unavailable",
            f"public egress guard rejected the job network{': ' + detail if detail else ''}",
        )
