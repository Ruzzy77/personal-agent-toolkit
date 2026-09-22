from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from personal_agent_host.config import RootPolicy
from personal_agent_host.files import ToolError
from personal_agent_host.jobs import RETENTION_S, TAIL_BYTES, Job, JobManager


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
            policy,
            cwd=".",
            argv=[sys.executable, "-c", "import os; print(os.getcwd())"],
            shell=None,
            stdin=None,
            timeout_s=10,
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
            policy,
            cwd=".",
            argv=[sys.executable, "-c", code],
            shell=None,
            stdin="one\n",
            timeout_s=10,
            keep_stdin_open=True,
        )
        for _ in range(30):
            current = manager.store.load(job.id)
            if current and current.status == "running" and current.pid:
                break
            await asyncio.sleep(0.05)
        await manager.write_stdin(job.id, "two\n", eof=True)
        result = await manager.wait(job.id, 10)
        assert result.status == "succeeded"
        assert "one|two" in manager.tail(result, "stdout")

    asyncio.run(run())


def test_cancel_and_timeout(tmp_path: Path) -> None:
    async def run() -> None:
        manager, policy = _manager(tmp_path)
        timed = await manager.submit(
            policy,
            cwd=".",
            argv=[sys.executable, "-c", "import time; time.sleep(30)"],
            shell=None,
            stdin=None,
            timeout_s=1,
        )
        assert (await manager.wait(timed.id, 10)).status == "timed_out"
        cancelled = await manager.submit(
            policy,
            cwd=".",
            argv=[sys.executable, "-c", "import time; time.sleep(30)"],
            shell=None,
            stdin=None,
            timeout_s=30,
        )
        await asyncio.sleep(0.2)
        assert (await manager.cancel(cancelled.id)).status == "cancelled"

    asyncio.run(run())


def test_restart_attaches_to_owned_worker(tmp_path: Path) -> None:
    async def run() -> None:
        first, policy = _manager(tmp_path)
        job = await first.submit(
            policy,
            cwd=".",
            argv=[sys.executable, "-c", "import time; time.sleep(.4)"],
            shell=None,
            stdin=None,
            timeout_s=10,
        )
        await asyncio.sleep(0.15)
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
                policy,
                cwd=".",
                argv=["true"],
                shell=None,
                stdin=None,
                timeout_s=1,
                profile="documents",
            )
        except ToolError as failure:
            assert failure.code == "invalid_request"
        else:
            raise AssertionError("retired profile was accepted")
        denied = RootPolicy(
            id="no",
            root=policy.root,
            permission="read_write",
            execute="none",
            sources=(),
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
            policy,
            cwd=".",
            argv=[sys.executable, "-c", "import time; time.sleep(30)"],
            shell=None,
            stdin="x" * 1_000_000,
            timeout_s=1,
        )
        assert (await manager.wait(job.id, 10)).status == "timed_out"

    asyncio.run(run())


def test_live_short_output_is_visible_before_exit(tmp_path: Path) -> None:
    async def run() -> None:
        manager, policy = _manager(tmp_path)
        job = await manager.submit(
            policy,
            cwd=".",
            argv=[
                sys.executable,
                "-c",
                "import time; print('ready', flush=True); time.sleep(1)",
            ],
            shell=None,
            stdin=None,
            timeout_s=10,
        )
        await asyncio.sleep(0.2)
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
            await asyncio.sleep(0.2)
            return await real_spawn(*args, **kwargs)

        with patch(
            "personal_agent_host.jobs.asyncio.create_subprocess_exec", delayed_spawn
        ):
            job = await manager.submit(
                policy,
                cwd=".",
                argv=[sys.executable, "-c", "import time; time.sleep(30)"],
                shell=None,
                stdin=None,
                timeout_s=10,
            )
            await asyncio.wait_for(entered.wait(), 1)
            result = await manager.cancel(job.id)
            assert result.status == "cancelled"
            assert (await manager.wait(job.id, 2)).status == "cancelled"

    asyncio.run(run())


@pytest.mark.parametrize("yield_before_cancel", [False, True])
def test_cancel_waiting_job_finishes_without_a_free_slot(
    tmp_path: Path, yield_before_cancel: bool
) -> None:
    async def run() -> None:
        manager, policy = _manager(tmp_path)
        for _ in range(manager.config.max_concurrent_jobs):
            await manager.slots.acquire()
        try:
            job = await manager.submit(
                policy,
                cwd=".",
                argv=[sys.executable, "-c", "print('must not run')"],
                shell=None,
                stdin=None,
                timeout_s=10,
            )
            if yield_before_cancel:
                await asyncio.sleep(0)
            result = await asyncio.wait_for(manager.cancel(job.id), timeout=1)
            assert result.status == "cancelled"
            assert result.pid is None and result.worker_pid is None
            assert manager.done[job.id].is_set()
            assert job.id not in manager.tasks
            assert not manager.tail(result, "stdout")
        finally:
            for _ in range(manager.config.max_concurrent_jobs):
                manager.slots.release()
            await manager.stop()

    asyncio.run(run())


def _stored_job(
    manager: JobManager,
    data: bytes,
    *,
    job_id: str = "stored",
    status: str = "succeeded",
    finished: float | None = None,
) -> Job:
    job = Job(
        id=job_id,
        root="workspace",
        cwd="/unused",
        command=["test"],
        status=status,
        exit_code=0 if status == "succeeded" else None,
        created=time.time(),
        started=time.time(),
        finished=finished,
        timeout_s=10,
        truncated=False,
    )
    directory = manager.store.directory / job_id
    directory.mkdir()
    (directory / "stdout").write_bytes(data)
    manager.store.save(job)
    return job


@pytest.mark.parametrize("limit,prefix", [(4, ""), (5, ""), (65_536, "x" * 65_535)])
def test_output_pages_preserve_utf8(tmp_path: Path, limit: int, prefix: str) -> None:
    manager, _ = _manager(tmp_path)
    expected = prefix + "가나다🙂끝"
    job = _stored_job(manager, expected.encode())
    offset, parts = 0, []
    while True:
        page = manager.output(job, "stdout", offset, limit)
        assert page["next_offset"] > offset
        assert page["next_offset"] - offset == len(page["output"].encode())
        parts.append(page["output"])
        offset = page["next_offset"]
        if page["eof"]:
            break
    assert "".join(parts) == expected


def test_live_partial_character_does_not_advance_offset(tmp_path: Path) -> None:
    manager, _ = _manager(tmp_path)
    job = _stored_job(manager, "가".encode()[:2], status="running")
    page = manager.output(job, "stdout", 0, 4)
    assert (page["output"], page["next_offset"], page["eof"]) == ("", 0, False)
    manager.store.stream_path(job.id, "stdout").write_bytes("가".encode())
    page = manager.output(job, "stdout", 0, 4)
    assert (page["output"], page["next_offset"]) == ("가", 3)


def test_too_small_utf8_page_fails_without_losing_bytes(tmp_path: Path) -> None:
    manager, _ = _manager(tmp_path)
    job = _stored_job(manager, "가나".encode())
    with pytest.raises(ToolError, match="limit is too small"):
        manager.output(job, "stdout", 0, 1)
    assert manager.output(job, "stdout", 0, 4)["output"] == "가"


def test_utf8_tail_omits_only_partial_characters(tmp_path: Path) -> None:
    manager, _ = _manager(tmp_path)
    job = _stored_job(manager, ("가" + "x" * (TAIL_BYTES - 1)).encode())
    assert manager.tail(job, "stdout") == "x" * (TAIL_BYTES - 1)
    job.status = "running"
    manager.store.stream_path(job.id, "stdout").write_bytes(
        b"ready " + "가".encode()[:2]
    )
    assert manager.tail(job, "stdout") == "ready "
    job.status = "failed"
    assert manager.tail(job, "stdout") == "ready �"


def test_retention_removes_only_expired_jobs_and_completion_events(
    tmp_path: Path,
) -> None:
    manager, _ = _manager(tmp_path)
    now = time.time()
    old = _stored_job(manager, b"old", job_id="old", finished=now - RETENTION_S - 1)
    recent = _stored_job(manager, b"recent", job_id="recent", finished=now)
    active = _stored_job(manager, b"active", job_id="active", status="running")
    for job in (old, recent, active):
        manager.done[job.id] = asyncio.Event()
    manager.expire()
    assert manager.store.load(old.id) is None
    assert not (manager.store.directory / old.id).exists()
    assert old.id not in manager.done
    for job in (recent, active):
        assert manager.store.load(job.id) is not None
        assert (manager.store.directory / job.id).exists()
        assert job.id in manager.done


def test_hourly_retention_includes_jobs() -> None:
    from personal_agent_host.app import expire_files

    async def run() -> None:
        jobs, transfers, workspace = Mock(), Mock(), Mock()
        sleep = AsyncMock(side_effect=[None, asyncio.CancelledError()])
        with (
            patch("personal_agent_host.app.asyncio.sleep", sleep),
            pytest.raises(asyncio.CancelledError),
        ):
            await expire_files(jobs, transfers, workspace)
        jobs.expire.assert_called_once_with()
        transfers.expire.assert_called_once_with()
        workspace.expire.assert_called_once_with()
        assert sleep.await_count == 2
        sleep.assert_awaited_with(3600)

    asyncio.run(run())
