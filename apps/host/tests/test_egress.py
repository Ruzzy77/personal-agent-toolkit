"""Standard-library tests for Host egress policy and Docker argument construction."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from personal_agent_host.config import HostConfig, load_host_config
from personal_agent_host.egress import (
    HostnameError,
    _resolve_public,
    is_public_address,
    normalize_hostname,
)
from personal_agent_host.files import ToolError
from personal_agent_host.jobs import EgressRequest, Job, JobManager


def _config(tmp_path: Path, allowlist: list[str] | None = None) -> HostConfig:
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
https_host_allowlist = {json.dumps(allowlist or [])}

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

    def test_exact_hostname_normalization_and_public_filter(self) -> None:
        self.assertEqual(normalize_hostname("PyPI.ORG."), "pypi.org")
        with self.assertRaises(HostnameError):
            normalize_hostname("127.0.0.1")
        self.assertFalse(is_public_address("100.64.0.1"))
        self.assertFalse(is_public_address("169.254.169.254"))
        self.assertFalse(is_public_address("fc00::1"))
        self.assertFalse(is_public_address("224.0.0.1"))
        self.assertFalse(is_public_address("0.0.0.0"))
        with patch(
            "personal_agent_host.egress.socket.getaddrinfo",
            return_value=[
                (2, 1, 6, "", ("10.0.0.1", 443)),
                (2, 1, 6, "", ("93.184.216.34", 443)),
            ],
        ):
            self.assertEqual(
                _resolve_public("example.com", 443), [(2, "93.184.216.34")]
            )

    def test_default_job_stays_network_none_and_profiles_select_images(self) -> None:
        config = _config(self.path)
        manager = JobManager(config)
        args = manager._docker_create_args(
            _job(), config.root("workspace"), config.execution_profile("documents"), None
        )
        self.assertEqual(args[args.index("--network") + 1], "none")
        self.assertIn("personal-agent-host-documents:1", args)
        self.assertNotIn("--dns", args)

    def test_egress_job_uses_private_network_proxy_and_owner_allowlist(self) -> None:
        config = _config(self.path, ["pypi.org"])
        manager = JobManager(config)
        request = manager._egress_request(["PyPI.ORG"])
        assert request is not None
        request = EgressRequest("abcdef123456", request.hosts, "172.30.0.2")
        args = manager._docker_create_args(
            _job(), config.root("workspace"), config.execution_profile(None), request
        )
        self.assertEqual(
            args[args.index("--network") + 1], "pah-private-abcdef123456"
        )
        self.assertEqual(
            args[args.index("--dns") : args.index("--dns") + 2],
            ["--dns", "127.0.0.1"],
        )
        self.assertIn("pah-egress-proxy:172.30.0.2", args)
        self.assertIn("HTTPS_PROXY=http://pah-egress-proxy:3128", args)
        with self.assertRaises(ToolError) as denied:
            manager._egress_request(["registry.npmjs.org"])
        self.assertEqual(denied.exception.code, "policy_denied")

    def test_cleanup_removes_only_label_matched_egress_resources(self) -> None:
        manager = _FakeDockerManager(_config(self.path))
        asyncio.run(manager._cleanup_egress(_job()))
        self.assertIn(("rm", "-f", "pah-egress-abcdef123456"), manager.calls)
        self.assertIn(
            ("network", "rm", "pah-private-abcdef123456"), manager.calls
        )
        self.assertIn(
            ("network", "rm", "pah-uplink-abcdef123456"), manager.calls
        )


    def test_cancel_after_container_creation_removes_endpoint_before_network(self) -> None:
        async def scenario() -> None:
            created = asyncio.Event()

            class CancellingDocker(_FakeDockerManager):
                async def _prepare_egress(self, egress: EgressRequest) -> None:
                    return None

                async def _proxy_ip(self, egress: EgressRequest) -> str:
                    return "172.30.0.2"

                async def _docker(self, *args: str) -> _Result:
                    result = await super()._docker(*args)
                    if args[0] == "create" and "pah-abcdef123456" in args:
                        created.set()
                        await asyncio.Event().wait()
                    return result

            manager = CancellingDocker(_config(self.path, ["pypi.org"]))
            with patch("personal_agent_host.jobs.uuid.uuid4") as identifier:
                identifier.return_value.hex = "abcdef123456"
                job = await manager.submit(
                    manager.config.root("workspace"), cwd=".", argv=["true"],
                    shell=None, stdin=None, timeout_s=30, https_hosts=["pypi.org"],
                )
            task = manager.tasks[job.id]
            await asyncio.wait_for(created.wait(), 2)
            await manager.cancel(job.id)
            await asyncio.gather(task, return_exceptions=True)
            self.assertEqual(manager.store.load(job.id).status, "cancelled")
            self.assertLess(
                manager.calls.index(("rm", "-f", "pah-abcdef123456")),
                manager.calls.index(("network", "rm", "pah-private-abcdef123456")),
            )

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
