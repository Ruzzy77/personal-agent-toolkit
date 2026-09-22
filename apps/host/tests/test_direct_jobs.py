from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from personal_agent_host.config import RootPolicy
from personal_agent_host.files import ToolError
from personal_agent_host.jobs import JobManager


def _manager(tmp_path: Path) -> tuple[JobManager, RootPolicy]:
    root = tmp_path / "workspace"
    root.mkdir()
    config = SimpleNamespace(
        sync=SimpleNamespace(data_root=tmp_path / "state"),
        max_concurrent_jobs=4,
    )
    return JobManager(config), RootPolicy(
        id="workspace", root=root, permission="read_write", execute="host", sources=()
    )


def test_direct_short_process_and_real_cwd(tmp_path: Path) -> None:
    async def run() -> None:
        manager, policy = _manager(tmp_path)
        job = await manager.submit(
            policy, cwd=".", argv=[sys.executable, "-c", "import os; print(os.getcwd())"],
            shell=None, stdin=None, timeout_s=10,
        )
        result = await manager.wait(job.id, 10)
        assert result.status == "succeeded"
        assert result.cwd == str(policy.root.resolve())
        assert str(policy.root.resolve()) in manager.tail(result, "stdout")
    asyncio.run(run())


def test_stdin_open_then_eof(tmp_path: Path) -> None:
    async def run() -> None:
        manager, policy = _manager(tmp_path)
        code = "import sys; first=sys.stdin.readline().strip(); rest=sys.stdin.read().strip(); print(first+'|'+rest)"
        job = await manager.submit(
            policy, cwd=".", argv=[sys.executable, "-c", code], shell=None,
            stdin="one\n", timeout_s=10, keep_stdin_open=True,
        )
        for _ in range(30):
            current = manager.store.load(job.id)
            if current and current.status == "running" and current.pid:
                break
            await asyncio.sleep(.05)
        await manager.write_stdin(job.id, "two\n", eof=True)
        result = await manager.wait(job.id, 10)
        assert result.status == "succeeded"
        assert "one|two" in manager.tail(result, "stdout")
    asyncio.run(run())


def test_cancel_and_timeout(tmp_path: Path) -> None:
    async def run() -> None:
        manager, policy = _manager(tmp_path)
        timed = await manager.submit(
            policy, cwd=".", argv=[sys.executable, "-c", "import time; time.sleep(30)"],
            shell=None, stdin=None, timeout_s=1,
        )
        assert (await manager.wait(timed.id, 10)).status == "timed_out"
        cancelled = await manager.submit(
            policy, cwd=".", argv=[sys.executable, "-c", "import time; time.sleep(30)"],
            shell=None, stdin=None, timeout_s=30,
        )
        await asyncio.sleep(.2)
        assert (await manager.cancel(cancelled.id)).status == "cancelled"
    asyncio.run(run())


def test_restart_attaches_to_owned_worker(tmp_path: Path) -> None:
    async def run() -> None:
        first, policy = _manager(tmp_path)
        job = await first.submit(
            policy, cwd=".", argv=[sys.executable, "-c", "import time; time.sleep(.4)"],
            shell=None, stdin=None, timeout_s=10,
        )
        await asyncio.sleep(.15)
        await first.stop()
        second = JobManager(first.config)
        await second.start()
        result = await second.wait(job.id, 5)
        assert result.status == "succeeded"
    asyncio.run(run())


def test_retired_and_disallowed_execution_are_explicit(tmp_path: Path) -> None:
    async def run() -> None:
        manager, policy = _manager(tmp_path)
        try:
            await manager.submit(
                policy, cwd=".", argv=["true"], shell=None, stdin=None,
                timeout_s=1, profile="documents",
            )
        except ToolError as failure:
            assert failure.code == "invalid_request"
        else:
            raise AssertionError("retired profile was accepted")
        denied = RootPolicy(
            id="no", root=policy.root, permission="read_write", execute="none", sources=()
        )
        try:
            await manager.submit(
                denied, cwd=".", argv=["true"], shell=None, stdin=None, timeout_s=1
            )
        except ToolError as failure:
            assert failure.code == "policy_denied"
        else:
            raise AssertionError("disabled root was accepted")
    asyncio.run(run())


def test_large_unconsumed_initial_input_still_times_out(tmp_path: Path) -> None:
    async def run() -> None:
        manager, policy = _manager(tmp_path)
        job = await manager.submit(
            policy, cwd=".", argv=[sys.executable, "-c", "import time; time.sleep(30)"],
            shell=None, stdin="x" * 1_000_000, timeout_s=1,
        )
        assert (await manager.wait(job.id, 10)).status == "timed_out"
    asyncio.run(run())


def test_live_short_output_is_visible_before_exit(tmp_path: Path) -> None:
    async def run() -> None:
        manager, policy = _manager(tmp_path)
        job = await manager.submit(
            policy, cwd=".", argv=[sys.executable, "-c", "import time; print('ready', flush=True); time.sleep(1)"],
            shell=None, stdin=None, timeout_s=10,
        )
        await asyncio.sleep(.2)
        current = manager.store.load(job.id)
        assert current is not None and "ready" in manager.tail(current, "stdout")
        assert (await manager.wait(job.id, 5)).status == "succeeded"
    asyncio.run(run())


def test_cancel_during_spawn_does_not_leave_a_worker(tmp_path: Path) -> None:
    async def run() -> None:
        manager, policy = _manager(tmp_path)
        real_spawn = asyncio.create_subprocess_exec
        entered = asyncio.Event()

        async def delayed_spawn(*args, **kwargs):
            entered.set()
            await asyncio.sleep(.2)
            return await real_spawn(*args, **kwargs)

        with patch("personal_agent_host.jobs.asyncio.create_subprocess_exec", delayed_spawn):
            job = await manager.submit(
                policy, cwd=".", argv=[sys.executable, "-c", "import time; time.sleep(30)"],
                shell=None, stdin=None, timeout_s=10,
            )
            await asyncio.wait_for(entered.wait(), 1)
            result = await manager.cancel(job.id)
            assert result.status == "cancelled"
            assert (await manager.wait(job.id, 2)).status == "cancelled"
    asyncio.run(run())
