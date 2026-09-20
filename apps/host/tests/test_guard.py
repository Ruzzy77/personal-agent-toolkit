"""Fixed execution-boundary tests for the administrator egress guard."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock, patch

from personal_agent_host import guard


def test_guard_client_uses_an_absolute_sudo_path() -> None:
    process = type(
        "Process", (), {"returncode": 0, "communicate": AsyncMock(return_value=(b"", b""))}
    )()
    with patch(
        "asyncio.create_subprocess_exec", new=AsyncMock(return_value=process)
    ) as create:
        asyncio.run(guard.invoke("check", "pah-egress-abcdef123456"))
    assert create.await_args.args[:4] == (
        "/usr/bin/sudo", "-n",
        "/usr/local/libexec/personal-agent-host-egress-guard", "check",
    )


def test_root_guard_uses_fixed_absolute_binary_paths() -> None:
    path = Path(__file__).parents[1] / "scripts" / "egress-guard.py"
    spec = importlib.util.spec_from_file_location("egress_guard_script", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert path.read_text(encoding="utf-8").startswith("#!/usr/bin/python3\n")
    assert (module.DOCKER, module.IPTABLES, module.IP) == (
        "/usr/bin/docker", "/usr/sbin/iptables", "/usr/sbin/ip",
    )
