"""Direct host command execution, with detached per-job workers and SQLite history.

Jobs run as the Host owner on the real configured root.  File-root permissions
remain logical API policy only: this mode is deliberately not a sandbox and a
command with the owner's sudo access can reach outside a root or alter sources.
"""
from __future__ import annotations

import asyncio
import codecs
import contextlib
import json
import os
import signal
import sqlite3
import sys
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
GRACE_S = 5.0


def clamp_timeout(value: int) -> int:
    return min(max(1, value), LIMITS["timeout_s"])


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
    pid: int | None = None
    pid_start: str | None = None
    boot_id: str | None = None
    worker_pid: int | None = None
    worker_start: str | None = None
    input_open: bool = False
    runtime: str | None = None


def _boot_id() -> str | None:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _start_time(pid: int) -> str | None:
    try:
        text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        return text[text.rfind(")") + 2 :].split()[19]
    except (OSError, IndexError):
        return None


class JobStore:
    """SQLite store which keeps pre-direct-execution rows readable."""

    _columns = (
        "id, root, cwd, command, status, exit_code, created, started, finished, "
        "timeout_s, truncated, pid, pid_start, boot_id, worker_pid, worker_start, input_open, runtime"
    )

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(directory / "jobs.sqlite", check_same_thread=False)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, root TEXT, cwd TEXT, "
            "command TEXT, status TEXT, exit_code INTEGER, created REAL, started REAL, "
            "finished REAL, timeout_s INTEGER, truncated INTEGER)"
        )
        present = {row[1] for row in self.db.execute("PRAGMA table_info(jobs)")}
        for name, definition in (
            ("pid", "INTEGER"), ("pid_start", "TEXT"), ("boot_id", "TEXT"),
            ("worker_pid", "INTEGER"), ("worker_start", "TEXT"), ("input_open", "INTEGER DEFAULT 0"),
            ("runtime", "TEXT"),
        ):
            if name not in present:
                self.db.execute(f"ALTER TABLE jobs ADD COLUMN {name} {definition}")
        self.db.commit()

    def save(self, job: Job) -> None:
        values = (
            job.id, job.root, job.cwd, json.dumps(job.command), job.status,
            job.exit_code, job.created, job.started, job.finished, job.timeout_s,
            int(job.truncated), job.pid, job.pid_start, job.boot_id, job.worker_pid, job.worker_start,
            int(job.input_open), job.runtime,
        )
        self.db.execute(
            f"INSERT OR REPLACE INTO jobs ({self._columns}) VALUES ({','.join('?' for _ in values)})",
            values,
        )
        self.db.commit()

    def load(self, job_id: str) -> Job | None:
        row = self.db.execute(f"SELECT {self._columns} FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._job(row) if row else None

    def active(self) -> list[Job]:
        rows = self.db.execute(
            f"SELECT {self._columns} FROM jobs WHERE status IN ('queued','running')"
        ).fetchall()
        return [self._job(row) for row in rows]

    def expire(self, now: float) -> list[str]:
        rows = self.db.execute(
            "SELECT id FROM jobs WHERE finished IS NOT NULL AND finished < ?",
            (now - RETENTION_S,),
        ).fetchall()
        for (job_id,) in rows:
            directory = self.directory / job_id
            for name in ("stdout", "stderr", "stdin.initial", "stdin.fifo", "stdin.close",
                         "run.json", "result.json", "deadline", "cancel"):
                (directory / name).unlink(missing_ok=True)
            with contextlib.suppress(OSError):
                directory.rmdir()
            self.db.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        self.db.commit()
        return [job_id for (job_id,) in rows]

    def stream_path(self, job_id: str, stream: str) -> Path:
        return self.directory / job_id / stream

    @staticmethod
    def _job(row: tuple[Any, ...]) -> Job:
        return Job(
            id=row[0], root=row[1], cwd=row[2], command=json.loads(row[3]),
            status=row[4], exit_code=row[5], created=row[6], started=row[7],
            finished=row[8], timeout_s=row[9], truncated=bool(row[10]),
            pid=row[11], pid_start=row[12], boot_id=row[13], worker_pid=row[14],
            worker_start=row[15], input_open=bool(row[16]), runtime=row[17],
        )


class JobManager:
    def __init__(self, config: HostConfig) -> None:
        self.config = config
        self.store = JobStore(config.sync.data_root / "jobs")
        self.slots = asyncio.Semaphore(config.max_concurrent_jobs)
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.done: dict[str, asyncio.Event] = {}
        self.starting: set[str] = set()

    async def start(self) -> None:
        self.expire()
        recovered: list[Job] = []
        for job in self.store.active():
            self.done[job.id] = asyncio.Event()
            self._sync_result(job)
            if job.status in TERMINAL:
                self.done[job.id].set()
            elif self._owns_live_process(job) or self._owns_live_worker(job):
                recovered.append(job)
            else:
                self._lost(job, "job worker or process is no longer present")
                self.done[job.id].set()
        # Running workers already consume capacity; only released recovered slots
        # permit new launches after they finish.
        self.slots = asyncio.Semaphore(max(0, self.config.max_concurrent_jobs - len(recovered)))
        for job in recovered:
            self.tasks[job.id] = asyncio.create_task(self._resume(job))

    async def _resume(self, job: Job) -> None:
        try:
            await self._monitor(job)
        finally:
            self.done[job.id].set()
            self.tasks.pop(job.id, None)
            self.slots.release()

    def expire(self) -> None:
        for job_id in self.store.expire(time.time()):
            self.done.pop(job_id, None)

    async def stop(self) -> None:
        """Stop monitors only; workers retain ownership of direct commands."""
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def submit(
        self,
        policy: RootPolicy,
        *,
        cwd: str,
        argv: list[str] | None,
        shell: str | None,
        stdin: str | None,
        timeout_s: int,
        profile: str | None = None,
        https_hosts: list[str] | None = None,
        keep_stdin_open: bool = False,
    ) -> Job:
        if policy.execute != "host" or policy.permission != "read_write":
            raise ToolError("policy_denied", f"{policy.id} does not allow direct host execution")
        if profile is not None:
            raise ToolError("invalid_request", "profile was retired; jobs use the Host runtime")
        if https_hosts is not None:
            raise ToolError("invalid_request", "https_hosts was retired; direct host jobs use host networking")
        if (argv is None) == (shell is None):
            raise ToolError("invalid_request", "give exactly one of argv or shell")
        workdir = relative_path(policy.root, cwd or ".")
        if not workdir.is_dir():
            raise ToolError("invalid_path", "cwd is not a directory")
        command = argv if argv is not None else ["/bin/sh", "-c", shell or ""]
        job = Job(
            id=uuid.uuid4().hex[:12], root=policy.id, cwd=str(workdir.resolve()),
            command=command, status="queued", exit_code=None, created=time.time(),
            started=None, finished=None, timeout_s=timeout_s, truncated=False,
            input_open=keep_stdin_open, runtime=sys.executable,
        )
        directory = self._directory(job)
        directory.mkdir(parents=True, exist_ok=True)
        if stdin is not None:
            (directory / "stdin.initial").write_bytes(stdin.encode("utf-8"))
        self.store.save(job)
        self.done[job.id] = asyncio.Event()
        self.tasks[job.id] = asyncio.create_task(self._launch(job))
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
        self._sync_result(job)
        if job.status in TERMINAL:
            return job
        if job.status == "queued":
            (self._directory(job) / "cancel").touch()
            if job.id not in self.starting:
                # Waiting for a slot cannot have created an OS child. Finalize
                # now instead of waiting for an unrelated running job to end.
                job.status, job.finished = "cancelled", time.time()
                job.input_open = False
                self.store.save(job)
                task = self.tasks.pop(job.id, None)
                if task is not None:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                self.done[job.id].set()
                return job
            # A spawn may already own a child before its identity is persisted.
            # Leave that handoff alive so the worker observes the cancel marker.
            return await self.wait(job.id, GRACE_S + 2)
        (self._directory(job) / "cancel").touch()
        await asyncio.sleep(0.2)
        self._sync_result(job)
        if job.status not in TERMINAL and self._owns_live_process(job):
            self._signal_group(job.pid, signal.SIGTERM)
            await asyncio.sleep(GRACE_S)
            if self._owns_live_process(job):
                self._signal_group(job.pid, signal.SIGKILL)
        return await self.wait(job.id, GRACE_S + 1)

    async def write_stdin(self, job_id: str, data: str, *, eof: bool = False) -> Job:
        job = self.store.load(job_id)
        if job is None:
            raise ToolError("not_found", "unknown job")
        self._sync_result(job)
        if job.status != "running" or not job.input_open:
            raise ToolError("invalid_request", "job was not started with keep_stdin_open")
        directory = self._directory(job)
        fifo = directory / "stdin.fifo"
        if data:
            try:
                descriptor = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
            except OSError as exc:
                raise ToolError("input_unavailable", "job input is no longer available") from exc
            try:
                encoded = data.encode("utf-8")
                while encoded:
                    try:
                        written = os.write(descriptor, encoded)
                    except BlockingIOError:
                        await asyncio.sleep(0.02)
                        self._sync_result(job)
                        if job.status != "running":
                            raise ToolError("input_unavailable", "job input is no longer available")
                        continue
                    encoded = encoded[written:]
            finally:
                os.close(descriptor)
        if eof:
            (directory / "stdin.close").touch()
            job.input_open = False
            self.store.save(job)
        return job

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
        final = job.status in TERMINAL and offset + len(chunk) >= size
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        output = decoder.decode(chunk, final=final)
        consumed = len(chunk) - len(decoder.getstate()[0])
        if chunk and not consumed and offset + len(chunk) < size:
            raise ToolError(
                "invalid_request",
                "limit is too small for the next UTF-8 character; use at least 4 bytes",
            )
        return {
            "stream": stream, "output": output,
            "next_offset": offset + consumed,
            "eof": job.status in TERMINAL and offset + consumed >= size,
        }

    def tail(self, job: Job, stream: str) -> str:
        path = self.store.stream_path(job.id, stream)
        if not path.exists():
            return ""
        start = max(0, path.stat().st_size - TAIL_BYTES)
        with path.open("rb") as handle:
            handle.seek(start)
            chunk = handle.read()
        if start:
            # The tail may begin inside a character; omit only its partial prefix.
            for _ in range(3):
                if not chunk or chunk[0] & 0xC0 != 0x80:
                    break
                chunk = chunk[1:]
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        return decoder.decode(chunk, final=job.status in TERMINAL)

    @staticmethod
    def public(job: Job) -> dict[str, Any]:
        return {
            "job_id": job.id, "status": job.status, "exit_code": job.exit_code,
            "root": job.root, "cwd": job.cwd, "command": job.command,
            "created": job.created, "started": job.started, "finished": job.finished,
            "truncated": job.truncated,
        }

    async def _launch(self, job: Job) -> None:
        try:
            async with self.slots:
                self.starting.add(job.id)
                directory = self._directory(job)
                if (directory / "cancel").exists() or job.status != "queued":
                    job.status, job.finished = "cancelled", time.time()
                    self.store.save(job)
                    return
                # The deadline starts when the direct worker is handed off, not
                # while this job waits for a concurrency slot.
                (directory / "deadline").write_text(
                    str(time.monotonic() + job.timeout_s), encoding="utf-8"
                )
                spawn = asyncio.create_task(asyncio.create_subprocess_exec(
                    job.runtime or sys.executable, "-m", "personal_agent_host.worker",
                    "--job-dir", str(directory), "--cwd", job.cwd,
                    "--command-json", json.dumps(job.command),
                    *(["--keep-stdin-open"] if job.input_open else []),
                    start_new_session=True,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                ))
                try:
                    process = await asyncio.shield(spawn)
                except asyncio.CancelledError:
                    # Service shutdown stops monitoring, not an already-created
                    # direct worker.  Persist its identity before propagating.
                    process = await asyncio.shield(spawn)
                    job.worker_pid = process.pid
                    job.status, job.started = "running", time.time()
                    self.store.save(job)
                    raise
                job.worker_pid = process.pid
                job.status, job.started = "running", time.time()
                self.store.save(job)
                # Let the worker publish the child identity before callers can cancel.
                for _ in range(20):
                    self._sync_run(job)
                    if job.pid is not None or (self._directory(job) / "result.json").exists():
                        break
                    await asyncio.sleep(0.05)
                if job.pid is None:
                    self._sync_result(job)
                    if job.status not in TERMINAL:
                        self._lost(job, "direct worker did not publish process identity")
                    return
                await self._monitor(job)
        except asyncio.CancelledError:
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            if job.status not in TERMINAL:
                self._fail(job, f"direct job could not start: {exc}")
        finally:
            self.starting.discard(job.id)
            self.done[job.id].set()
            self.tasks.pop(job.id, None)

    async def _monitor(self, job: Job) -> None:
        missing_since: float | None = None
        while True:
            self._sync_result(job)
            if job.status in TERMINAL:
                return
            if self._owns_live_process(job) or self._owns_live_worker(job):
                missing_since = None
            else:
                # The child can exit just before its worker atomically publishes
                # result.json.  Never turn that short handoff into a false loss.
                missing_since = missing_since or time.monotonic()
                if time.monotonic() - missing_since >= 1:
                    self._sync_result(job)
                    if job.status not in TERMINAL:
                        self._lost(job, "direct worker disappeared without a final result")
                    return
            await asyncio.sleep(0.1)

    def _sync_run(self, job: Job) -> None:
        try:
            value = json.loads((self._directory(job) / "run.json").read_text(encoding="utf-8"))
            fresh = self.store.load(job.id) or job
            fresh.pid = int(value["pid"])
            fresh.pid_start = value.get("pid_start")
            fresh.boot_id = value.get("boot_id")
            fresh.worker_pid = int(value.get("worker_pid") or fresh.worker_pid or 0) or None
            fresh.worker_start = value.get("worker_start")
            fresh.started = float(value.get("started") or fresh.started or time.time())
            if fresh.status == "queued":
                fresh.status = "running"
            self.store.save(fresh)
            job.__dict__.update(fresh.__dict__)
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            pass

    def _sync_result(self, job: Job) -> None:
        self._sync_run(job)
        try:
            value = json.loads((self._directory(job) / "result.json").read_text(encoding="utf-8"))
            status = value["status"]
            if status not in TERMINAL:
                raise ValueError("invalid terminal status")
            job.status = status
            job.exit_code = value.get("exit_code")
            job.finished = float(value["finished"])
            job.truncated = bool(value.get("truncated", False))
            job.input_open = False
            self.store.save(job)
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            pass

    def _owns_live_process(self, job: Job) -> bool:
        if job.pid is None or job.pid_start is None or job.boot_id is None:
            return False
        return job.boot_id == _boot_id() and job.pid_start == _start_time(job.pid)

    def _owns_live_worker(self, job: Job) -> bool:
        if job.worker_pid is None or job.worker_start is None or job.boot_id is None:
            return False
        return job.boot_id == _boot_id() and job.worker_start == _start_time(job.worker_pid)

    @staticmethod
    def _signal_group(pid: int | None, signal_value: signal.Signals) -> None:
        if pid is None:
            return
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pid, signal_value)

    def _lost(self, job: Job, message: str) -> None:
        job.status, job.finished = "lost", time.time()
        self.store.save(job)
        self._append(job, "stderr", message + "\n")

    def _fail(self, job: Job, message: str) -> None:
        job.status, job.finished = "failed", time.time()
        self.store.save(job)
        self._append(job, "stderr", message + "\n")

    def _append(self, job: Job, stream: str, text: str) -> None:
        path = self.store.stream_path(job.id, stream)
        existing = path.stat().st_size if path.exists() else 0
        if existing >= OUTPUT_CAP:
            job.truncated = True
            self.store.save(job)
            return
        value = text.encode("utf-8")[: OUTPUT_CAP - existing]
        with path.open("ab") as handle:
            handle.write(value)
        if len(value) != len(text.encode("utf-8")):
            job.truncated = True
            self.store.save(job)

    def _directory(self, job: Job) -> Path:
        return self.store.directory / job.id
