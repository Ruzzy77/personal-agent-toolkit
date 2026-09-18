"""Sandboxed command execution: one Docker container per job, tracked in SQLite."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from personal_agent_host.config import LIMITS, HostConfig, RootPolicy
from personal_agent_host.files import ToolError, relative_path

OUTPUT_CAP = 64 * 1024 * 1024
TAIL_BYTES = 32 * 1024
RETENTION_S = 7 * 24 * 3600
TERMINAL = {"succeeded", "failed", "cancelled", "timed_out", "lost"}


@dataclass
class Job:
    id: str
    root: str
    cwd: str
    command: list[str]
    status: str
    exit_code: int | None
    created: float
    started: float | None
    finished: float | None
    timeout_s: int
    truncated: bool

    @property
    def container(self) -> str:
        return f"pah-{self.id}"


class JobStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(directory / "jobs.sqlite", check_same_thread=False)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, root TEXT, cwd TEXT, "
            "command TEXT, status TEXT, exit_code INTEGER, created REAL, started REAL, "
            "finished REAL, timeout_s INTEGER, truncated INTEGER)"
        )
        self.db.commit()

    def save(self, job: Job) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                job.id,
                job.root,
                job.cwd,
                json.dumps(job.command),
                job.status,
                job.exit_code,
                job.created,
                job.started,
                job.finished,
                job.timeout_s,
                int(job.truncated),
            ),
        )
        self.db.commit()

    def load(self, job_id: str) -> Job | None:
        row = self.db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._job(row) if row else None

    def active(self) -> list[Job]:
        rows = self.db.execute(
            "SELECT * FROM jobs WHERE status IN ('queued','running')"
        ).fetchall()
        return [self._job(row) for row in rows]

    def expire(self, now: float) -> None:
        rows = self.db.execute(
            "SELECT id FROM jobs WHERE finished IS NOT NULL AND finished < ?",
            (now - RETENTION_S,),
        ).fetchall()
        for (job_id,) in rows:
            for name in ("stdout", "stderr"):
                (self.directory / job_id / name).unlink(missing_ok=True)
            with contextlib.suppress(OSError):
                (self.directory / job_id).rmdir()
            self.db.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        self.db.commit()

    def stream_path(self, job_id: str, stream: str) -> Path:
        return self.directory / job_id / stream

    @staticmethod
    def _job(row: tuple) -> Job:
        return Job(
            id=row[0],
            root=row[1],
            cwd=row[2],
            command=json.loads(row[3]),
            status=row[4],
            exit_code=row[5],
            created=row[6],
            started=row[7],
            finished=row[8],
            timeout_s=row[9],
            truncated=bool(row[10]),
        )


class JobManager:
    def __init__(self, config: HostConfig) -> None:
        self.config = config
        self.store = JobStore(config.sync.data_root / "jobs")
        self.slots = asyncio.Semaphore(config.max_concurrent_jobs)
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.done: dict[str, asyncio.Event] = {}
        self.cancel_requested: set[str] = set()

    async def start(self) -> None:
        self.store.expire(time.time())
        for job in self.store.active():
            # Containers that outlived a daemon restart are re-read to completion.
            if job.status == "running" and await self._container_exists(job):
                self.done[job.id] = asyncio.Event()
                self.tasks[job.id] = asyncio.create_task(self._finish_existing(job))
            else:
                job.status = "lost"
                job.finished = time.time()
                self.store.save(job)

    async def submit(
        self,
        policy: RootPolicy,
        *,
        cwd: str,
        argv: list[str] | None,
        shell: str | None,
        stdin: str | None,
        timeout_s: int,
    ) -> Job:
        if policy.execute != "sandbox" or policy.permission != "read_write":
            raise ToolError("policy_denied", f"{policy.id} does not allow execution")
        if (argv is None) == (shell is None):
            raise ToolError("invalid_request", "give exactly one of argv or shell")
        workdir = relative_path(policy.root, cwd or ".")
        if not workdir.is_dir():
            raise ToolError("invalid_path", "cwd is not a directory")
        inner_cwd = (
            "/workspace/" + workdir.relative_to(policy.root.resolve()).as_posix()
        )
        command = argv if argv is not None else ["/bin/sh", "-c", shell or ""]
        job = Job(
            id=uuid.uuid4().hex[:12],
            root=policy.id,
            cwd=inner_cwd,
            command=command,
            status="queued",
            exit_code=None,
            created=time.time(),
            started=None,
            finished=None,
            timeout_s=timeout_s,
            truncated=False,
        )
        (self.store.directory / job.id).mkdir(parents=True, exist_ok=True)
        self.store.save(job)
        self.done[job.id] = asyncio.Event()
        self.tasks[job.id] = asyncio.create_task(self._run(job, policy, stdin))
        return job

    async def wait(self, job_id: str, seconds: float) -> Job:
        event = self.done.get(job_id)
        if event is not None and seconds > 0:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(event.wait(), timeout=seconds)
        job = self.store.load(job_id)
        assert job is not None
        return job

    async def cancel(self, job_id: str) -> Job:
        job = self.store.load(job_id)
        if job is None:
            raise ToolError("not_found", "unknown job")
        if job.status in TERMINAL:
            return job
        self.cancel_requested.add(job_id)
        if job.status == "running":
            await self._docker("kill", job.container)
        elif job.status == "queued":
            task = self.tasks.get(job_id)
            if task is not None:
                task.cancel()
            job.status = "cancelled"
            job.finished = time.time()
            self.store.save(job)
            self.done[job_id].set()
        return await self.wait(job_id, 10)

    def output(self, job: Job, stream: str, offset: int, limit: int) -> dict[str, Any]:
        path = self.store.stream_path(job.id, stream)
        size = path.stat().st_size if path.exists() else 0
        if offset > size:
            raise ToolError("invalid_request", "offset is beyond the stored output")
        chunk = b""
        if limit > 0 and offset < size:
            with path.open("rb") as handle:
                handle.seek(offset)
                chunk = handle.read(limit)
            if next_offset_cut := _incomplete_tail(chunk):
                chunk = chunk[:next_offset_cut]
        text = chunk.decode("utf-8", errors="replace")
        next_offset = offset + len(chunk)
        return {
            "stream": stream,
            "output": text,
            "next_offset": next_offset,
            "eof": job.status in TERMINAL and next_offset >= size,
        }

    def tail(self, job: Job, stream: str) -> str:
        path = self.store.stream_path(job.id, stream)
        if not path.exists():
            return ""
        size = path.stat().st_size
        with path.open("rb") as handle:
            handle.seek(max(0, size - TAIL_BYTES))
            data = handle.read()
        return data.decode("utf-8", errors="replace")

    @staticmethod
    def public(job: Job) -> dict[str, Any]:
        return {
            "job_id": job.id,
            "status": job.status,
            "exit_code": job.exit_code,
            "root": job.root,
            "cwd": job.cwd,
            "command": job.command,
            "created": job.created,
            "started": job.started,
            "finished": job.finished,
            "truncated": job.truncated,
        }

    # ----- internals -------------------------------------------------------

    def _docker_create_args(self, job: Job, policy: RootPolicy) -> list[str]:
        uid, gid = os.getuid(), os.getgid()
        args = [
            "docker",
            "create",
            "-i",
            "--name",
            job.container,
            "--user",
            f"{uid}:{gid}",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--network",
            "none",
            "--memory",
            "8g",
            "--cpus",
            "4",
            "--pids-limit",
            "512",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,size=1g",
            "--log-opt",
            "max-size=64m",
            "--log-opt",
            "max-file=2",
            "-e",
            "HOME=/tmp",
            "-v",
            f"{policy.root.resolve()}:/workspace",
            "-w",
            job.cwd,
        ]
        for source_id in policy.sources:
            source = self.config.root(source_id)
            args += ["-v", f"{source.root.resolve()}:/sources/{source_id}:ro"]
        args.append(self.config.sandbox_image)
        args += job.command
        return args

    async def _run(self, job: Job, policy: RootPolicy, stdin: str | None) -> None:
        try:
            async with self.slots:
                if job.id in self.cancel_requested:
                    return
                await self._execute(job, policy, stdin)
        except asyncio.CancelledError:
            pass
        finally:
            self.done[job.id].set()
            self.tasks.pop(job.id, None)

    async def _execute(self, job: Job, policy: RootPolicy, stdin: str | None) -> None:
        created = await self._docker(*self._docker_create_args(job, policy)[1:])
        if created.returncode != 0:
            job.status = "failed"
            job.finished = time.time()
            self.store.save(job)
            self._write(job, "stderr", created.stderr)
            return
        job.status = "running"
        job.started = time.time()
        self.store.save(job)
        process = await asyncio.create_subprocess_exec(
            "docker",
            "start",
            "-a",
            "-i",
            job.container,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert process.stdin is not None
        if stdin:
            process.stdin.write(stdin.encode("utf-8"))
            with contextlib.suppress(Exception):
                await process.stdin.drain()
        process.stdin.close()
        pump = asyncio.gather(
            self._pump(job, "stdout", process.stdout),
            self._pump(job, "stderr", process.stderr),
        )
        timed_out = False
        try:
            await asyncio.wait_for(process.wait(), timeout=job.timeout_s)
        except TimeoutError:
            timed_out = True
            await self._docker("kill", job.container)
            await process.wait()
        await pump
        await self._finalize(job, timed_out=timed_out)

    async def _finish_existing(self, job: Job) -> None:
        process = await asyncio.create_subprocess_exec(
            "docker",
            "logs",
            "--follow",
            job.container,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.gather(
            self._pump(job, "stdout", process.stdout, append=False),
            self._pump(job, "stderr", process.stderr, append=False),
        )
        await process.wait()
        await self._finalize(job, timed_out=False)
        self.done[job.id].set()
        self.tasks.pop(job.id, None)

    async def _finalize(self, job: Job, *, timed_out: bool) -> None:
        inspect = await self._docker(
            "inspect", "--format", "{{.State.ExitCode}}", job.container
        )
        exit_code = None
        with contextlib.suppress(ValueError):
            exit_code = int(inspect.stdout.strip())
        await self._docker("rm", "-f", job.container)
        job.exit_code = exit_code
        if job.id in self.cancel_requested:
            job.status = "cancelled"
        elif timed_out:
            job.status = "timed_out"
        else:
            job.status = "succeeded" if exit_code == 0 else "failed"
        job.finished = time.time()
        self.store.save(job)

    async def _pump(
        self,
        job: Job,
        stream: str,
        reader: asyncio.StreamReader | None,
        append: bool = True,
    ) -> None:
        if reader is None:
            return
        path = self.store.stream_path(job.id, stream)
        path.parent.mkdir(parents=True, exist_ok=True)
        written = path.stat().st_size if append and path.exists() else 0
        with path.open("ab" if append else "wb") as handle:
            while True:
                chunk = await reader.read(65536)
                if not chunk:
                    break
                room = OUTPUT_CAP - written
                if room <= 0:
                    if not job.truncated:
                        job.truncated = True
                        self.store.save(job)
                    continue
                piece = chunk[:room]
                handle.write(piece)
                handle.flush()
                written += len(piece)
                if len(piece) < len(chunk) and not job.truncated:
                    job.truncated = True
                    self.store.save(job)

    def _write(self, job: Job, stream: str, text: str) -> None:
        path = self.store.stream_path(job.id, stream)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    async def _container_exists(self, job: Job) -> bool:
        result = await self._docker("inspect", "--format", "{{.Id}}", job.container)
        return result.returncode == 0

    @staticmethod
    async def _docker(*args: str) -> Any:
        process = await asyncio.create_subprocess_exec(
            "docker",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await process.communicate()

        class Result:
            returncode = process.returncode
            stdout = out.decode("utf-8", errors="replace")
            stderr = err.decode("utf-8", errors="replace")

        return Result()


def _incomplete_tail(chunk: bytes) -> int | None:
    """Return the cut position when ``chunk`` ends inside a UTF-8 sequence."""

    try:
        chunk.decode("utf-8")
        return None
    except UnicodeDecodeError as exc:
        return exc.start if exc.start >= len(chunk) - 3 else None


def clamp_timeout(value: int | None) -> int:
    if value is None:
        return 1800
    return max(1, min(int(value), LIMITS["timeout_s"]))
