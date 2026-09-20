"""Installed execution profiles are reported distinctly from configured profiles."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from personal_agent_host.runtime import execution_capabilities, inspect_profile


def test_installed_image_manifest():
    process = SimpleNamespace(
        returncode=0,
        communicate=AsyncMock(return_value=(
            b'sha256:abc\n{"org.personal-agent.features":"[\\"python\\",\\"node\\"]"}\n',
            b"",
        )),
    )
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
        value = asyncio.run(inspect_profile("web", "web:1"))
    assert value["available"] is True
    assert value["features"] == ["python", "node"]
    assert value["image_id"] == "sha256:abc"


def test_absent_image_is_not_advertised_as_available():
    process = SimpleNamespace(returncode=1, communicate=AsyncMock(return_value=(b"", b"")))
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
        value = asyncio.run(inspect_profile("web", "web:1"))
    assert value["available"] is False
    assert value["reason"] == "image_not_installed"


def test_capabilities_advertise_direct_guarded_public_egress():
    config = SimpleNamespace(
        execution_profiles={"base": "base:1", "web": "web:1"},
        sandbox_image="base:1",
    )
    with patch("personal_agent_host.runtime.inspect_profile", AsyncMock(
        side_effect=lambda name, image: {"name": name, "image": image, "available": True}
    )):
        value = asyncio.run(execution_capabilities(config))
    assert value["default_profile"] == "base"
    assert value["network"] == {
        "default": "public_ipv4",
        "policy": "direct_public_only_guarded",
        "protocols": ["tcp", "udp", "icmp"],
        "inbound": "blocked",
    }
    assert [item["name"] for item in value["profiles"]] == ["base", "web"]
