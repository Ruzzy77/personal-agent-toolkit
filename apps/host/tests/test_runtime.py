from __future__ import annotations

import asyncio
from types import SimpleNamespace

from personal_agent_host.runtime import execution_capabilities


def test_capabilities_describe_direct_host_execution() -> None:
    value = asyncio.run(execution_capabilities(SimpleNamespace()))
    assert value["execution"]["mode"] == "direct_host"
    assert value["execution"]["isolation"] == "none"
    assert value["execution"]["source_protection"] == "logical_root_policy_only"
    assert value["network"]["policy"] == "host_network"
