"""Regression checks for retired container-network configuration."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from personal_agent_host.config import load_host_config
from personal_agent_sync.errors import SyncError


def _config_path(tmp_path: Path, host_lines: str = "", execute: str = "host"):
    root = tmp_path / "workspace"
    root.mkdir()
    prefix = tmp_path / "prefix"
    (prefix / "config").mkdir(parents=True)
    (prefix / "config" / "host-upstream.token").write_text("x" * 32)
    source = prefix / "config" / "host.toml"
    source.write_text(
        f'''service_url = "https://context.example.workers.dev"
device_id = "test"
data_root = "{prefix / "state"}"
corpus_data_root = "{prefix / "state" / "corpus"}"
corpus_python = {json.dumps(sys.executable)}

[host]
{host_lines}
[[host.roots]]
id = "workspace"
path = "{root}"
permission = "read_write"
execute = "{execute}"
''',
        encoding="utf-8",
    )
    return source


def _config(tmp_path: Path, host_lines: str = "", execute: str = "host"):
    return load_host_config(_config_path(tmp_path, host_lines, execute))


@pytest.mark.parametrize(
    "host_lines",
    [
        'sandbox_image = "old:1"',
        '[host.execution_profiles]\nbase = "old:1"',
        'https_host_allowlist = ["pypi.org"]',
    ],
)
def test_retired_container_configuration_is_rejected(
    tmp_path: Path, host_lines: str
) -> None:
    with pytest.raises(SyncError, match="retired"):
        load_host_config(_config_path(tmp_path, host_lines))


def test_sandbox_root_requires_explicit_migration(tmp_path: Path) -> None:
    with pytest.raises(SyncError, match="explicitly migrate"):
        load_host_config(_config_path(tmp_path, execute="sandbox"))


def test_host_root_loads_without_container_configuration(tmp_path: Path) -> None:
    assert _config(tmp_path).root("workspace").execute == "host"
