"""Host direct-public egress policy and Docker argument construction."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from personal_agent_host.config import HostConfig, load_host_config
from personal_agent_host.files import ToolError
from personal_agent_host.jobs import DirectEgress, Job, JobManager


def _config(tmp_path: Path, retired: str = "") -> HostConfig:
    root = tmp_path / "workspace"
    root.mkdir()
    prefix = tmp_path / "prefix"
    (prefix / "config").mkdir(parents=True)
    (prefix / "config" / "host-upstream.token").write_text("x" * 32)
    (prefix / "config" / "host.toml").write_text(
        f"""service_url = "https://context.example.workers.dev"
device_id = "test"
data_root = "{prefix / "state"}"
corpus_data_root = "{prefix / "state" / "corpus"}"
corpus_python = {json.dumps(sys.executable)}

[host]
{retired}
[[host.roots]]
id = "workspace"
path = "{root}"
permission = "read_write"
execute = "sandbox"
""",
        encoding="utf-8",
    )
    return load_host_config(prefix / "config" / "host.toml")


def _job() -> Job:
    return Job(
        id="abcdef123456", root="workspace", cwd="/workspace", command=["true"],
        status="queued", exit_code=None, created=0, started=None, finished=None,
        timeout_s=60, truncated=False,
    )


class _Result:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


class _FakeDockerManager(JobManager):
    def __init__(self, config: HostConfig) -> None:
        super().__init__(config)
        self.calls: list[tuple[str, ...]] = []

    async def _docker(self, *args: str) -> _Result:
        self.calls.append(args)
        return _Result("abcdef123456\n") if args[0] == "inspect" else _Result()


class EgressTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_retired_proxy_configuration_is_rejected(self) -> None:
        with self.assertRaises(Exception) as failure:
            _config(self.path, 'https_host_allowlist = ["pypi.org"]')
        self.assertIn("was removed", str(failure.exception))

    def test_default_job_uses_a_direct_job_network_without_proxy(self) -> None:
        config = _config(self.path)
        manager = JobManager(config)
        args = manager._docker_create_args(
            _job(), config.root("workspace"), config.execution_profile("documents"),
            DirectEgress("abcdef123456"),
        )
        self.assertEqual(args[args.index("--network") + 1], "pah-egress-abcdef123456")
        self.assertIn("personal-agent-host-documents:1", args)
        self.assertNotIn("--dns", args)
        self.assertNotIn("HTTPS_PROXY", " ".join(args))
        self.assertNotIn("--publish", args)
        self.assertEqual(args[args.index("--tmpfs") + 1], "/tmp:rw,exec,nosuid,nodev,size=1g,mode=1777")

    def test_legacy_https_hosts_is_explicitly_rejected(self) -> None:
        manager = JobManager(_config(self.path))
        with self.assertRaises(ToolError) as failure:
            asyncio.run(manager.submit(
                manager.config.root("workspace"), cwd=".", argv=["true"],
                shell=None, stdin=None, timeout_s=30, https_hosts=["pypi.org"],
            ))
        self.assertEqual(failure.exception.code, "invalid_request")

    def test_prepare_creates_one_isolated_network_and_checks_guard(self) -> None:
        manager = _FakeDockerManager(_config(self.path))
        with patch("personal_agent_host.jobs.guard.invoke", new=AsyncMock()) as invoke:
            asyncio.run(manager._prepare_egress(DirectEgress("abcdef123456")))
        self.assertIn(
            (
                "network", "create", "--driver", "bridge", "--ipv6=false",
                "-o", "com.docker.network.bridge.enable_icc=false",
                "--label", "personal-agent-host.job=abcdef123456",
                "pah-egress-abcdef123456",
            ),
            manager.calls,
        )
        self.assertEqual(
            [call.args for call in invoke.await_args_list],
            [("attach", "pah-egress-abcdef123456"), ("check", "pah-egress-abcdef123456")],
        )

    def test_guard_failure_removes_the_created_network(self) -> None:
        manager = _FakeDockerManager(_config(self.path))
        failure = ToolError("egress_unavailable", "no guard")
        with patch(
            "personal_agent_host.jobs.guard.invoke", new=AsyncMock(side_effect=[failure, None])
        ), self.assertRaises(ToolError):
            asyncio.run(manager._prepare_egress(DirectEgress("abcdef123456")))
        self.assertIn(
            ("network", "rm", "pah-egress-abcdef123456"), manager.calls
        )

    def test_guard_and_detach_failure_preserves_labeled_network(self) -> None:
        manager = _FakeDockerManager(_config(self.path))
        failure = ToolError("egress_unavailable", "no guard")
        with patch(
            "personal_agent_host.jobs.guard.invoke",
            new=AsyncMock(side_effect=[failure, failure]),
        ), self.assertRaises(ToolError):
            asyncio.run(manager._prepare_egress(DirectEgress("abcdef123456")))
        self.assertNotIn(
            ("network", "rm", "pah-egress-abcdef123456"), manager.calls
        )

    def test_cleanup_detaches_guard_then_removes_only_labeled_network(self) -> None:
        manager = _FakeDockerManager(_config(self.path))
        with patch("personal_agent_host.jobs.guard.invoke", new=AsyncMock()) as invoke:
            asyncio.run(manager._cleanup_egress(_job()))
        self.assertEqual(
            invoke.await_args.args, ("detach", "pah-egress-abcdef123456")
        )
        self.assertIn(
            ("network", "rm", "pah-egress-abcdef123456"), manager.calls
        )


if __name__ == "__main__":
    unittest.main()
