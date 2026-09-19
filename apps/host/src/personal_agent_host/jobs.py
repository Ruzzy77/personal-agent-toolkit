"""Sandboxed command execution: one Docker container per job, tracked in SQLite."""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
import logging
import os
import sqlite3
import stat
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from personal_agent_host.config import LIMITS, HostConfig, RootPolicy
from personal_agent_host.egress import PROXY_ALIAS, HostnameError, normalize_hostname
from personal_agent_host.files import ToolError, relative_path

OUTPUT_CAP = 64 * 1024 * 1024
TAIL_BYTES = 32 * 1024
RETENTION_S = 7 * 24 * 3600
TERMINAL = {"succeeded", "failed", "cancelled", "timed_out", "lost"}
RESOURCE_LABEL = "personal-agent-host.job"
log = logging.getLogger(__name__)


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


@dataclass(frozen=True)
class EgressRequest:
    job_id: str
    hosts: tuple[str, ...]
    proxy_ip: str = ""
    @property
    def private_network(self) -> str:
        return "pah-private-" + self.job_id

    @property
    def uplink_network(self) -> str:
        return "pah-uplink-" + self.job_id

    @property
    def proxy_container(self) -> str:
        return "pah-egress-" + self.job_id


def _inner(base: Path, target: Path) -> str:
    return "/workspace/" + target.relative_to(base).as_posix()


def _mount(source: Path, destination: str, *, readonly: bool = False) -> list[str]:
    text = str(source)
    if '"' in text:
        raise ToolError("invalid_path", "a mounted path cannot contain a quote")
    field = f'"{text}"' if "," in text else text
    spec = f"type=bind,src={field},dst={destination},bind-propagation=rprivate"
    if readonly:
        spec += ",readonly,bind-recursive=readonly"
    return ["--mount", spec]


def _refuse_shared_inodes(guard: Path, label: str, limit: int = 20_000) -> None:
    seen = 0
    for item in guard.rglob("*"):
        seen += 1
        if seen > limit:
            raise ToolError(
                "protected_source_unavailable",
                f"{label} is too large to verify before execution",
            )
        try:
            status = item.lstat()
        except OSError:
            continue
        if stat.S_ISREG(status.st_mode) and status.st_nlink > 1:
            raise ToolError(
                "protected_source_shared",
                f"{label} shares a file with a writable location",
            )


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
                job.id, job.root, job.cwd, json.dumps(job.command), job.status,
                job.exit_code, job.created, job.started, job.finished,
                job.timeout_s, int(job.truncated),
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

    def terminal(self) -> list[Job]:
        rows = self.db.execute(
            "SELECT * FROM jobs WHERE status IN ('succeeded','failed','cancelled','timed_out','lost')"
        ).fetchall()
        return [self._job(row) for row in rows]

    def stream_path(self, job_id: str, stream: str) -> Path:
        return self.directory / job_id / stream

    @staticmethod
    def _job(row: tuple) -> Job:
        return Job(
            id=row[0], root=row[1], cwd=row[2], command=json.loads(row[3]),
            status=row[4], exit_code=row[5], created=row[6], started=row[7],
            finished=row[8], timeout_s=row[9], truncated=bool(row[10]),
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
            if job.status == "running" and await self._container_exists(job):
                self.done[job.id] = asyncio.Event()
                self.tasks[job.id] = asyncio.create_task(self._finish_existing(job))
            else:
                job.status = "lost"
                job.finished = time.time()
                self.store.save(job)
                await self._cleanup_egress(job)

        for job in self.store.terminal():
            with contextlib.suppress(Exception):
                await self._cleanup_egress(job)

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
    ) -> Job:
        if policy.execute != "sandbox" or policy.permission != "read_write":
            raise ToolError("policy_denied", f"{policy.id} does not allow execution")
        self._verify_protection(policy)
        if (argv is None) == (shell is None):
            raise ToolError("invalid_request", "give exactly one of argv or shell")
        try:
            image = self.config.execution_profile(profile)
        except Exception as exc:
            raise ToolError("invalid_request", "unknown execution profile") from exc
        egress = self._egress_request(https_hosts)
        workdir = relative_path(policy.root, cwd or ".")
        if not workdir.is_dir():
            raise ToolError("invalid_path", "cwd is not a directory")
        inner_cwd = "/workspace/" + workdir.relative_to(policy.root.resolve()).as_posix()
        command = argv if argv is not None else ["/bin/sh", "-c", shell or ""]
        job = Job(
            id=uuid.uuid4().hex[:12], root=policy.id, cwd=inner_cwd,
            command=command, status="queued", exit_code=None, created=time.time(),
            started=None, finished=None, timeout_s=timeout_s, truncated=False,
        )
        if egress is not None:
            egress = EgressRequest(job.id, egress.hosts)
        (self.store.directory / job.id).mkdir(parents=True, exist_ok=True)
        self.store.save(job)
        self.done[job.id] = asyncio.Event()
        self.tasks[job.id] = asyncio.create_task(
            self._run(job, policy, stdin, image, egress)
        )
        return job

    def _egress_request(self, requested: list[str] | None) -> EgressRequest | None:
        if requested is None or not requested:
            return None
        if not isinstance(requested, list) or not all(isinstance(item, str) for item in requested):
            raise ToolError("invalid_request", "https_hosts must be a hostname list")
        try:
            hosts = tuple(sorted({normalize_hostname(item) for item in requested}))
        except HostnameError as exc:
            raise ToolError("invalid_request", "https_hosts contains an invalid hostname") from exc
        if len(hosts) != len(requested):
            raise ToolError("invalid_request", "https_hosts contains duplicates")
        denied = set(hosts) - self.config.https_host_allowlist
        if denied:
            raise ToolError("policy_denied", "https_hosts is not owner-approved")
        return EgressRequest("", hosts)

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
        return {
            "stream": stream, "output": chunk.decode("utf-8", errors="replace"),
            "next_offset": offset + len(chunk),
            "eof": job.status in TERMINAL and offset + len(chunk) >= size,
        }

    def tail(self, job: Job, stream: str) -> str:
        path = self.store.stream_path(job.id, stream)
        if not path.exists():
            return ""
        with path.open("rb") as handle:
            handle.seek(max(0, path.stat().st_size - TAIL_BYTES))
            return handle.read().decode("utf-8", errors="replace")

    @staticmethod
    def public(job: Job) -> dict[str, Any]:
        return {
            "job_id": job.id, "status": job.status, "exit_code": job.exit_code,
            "root": job.root, "cwd": job.cwd, "command": job.command,
            "created": job.created, "started": job.started, "finished": job.finished,
            "truncated": job.truncated,
        }

    def _docker_create_args(
        self, job: Job, policy: RootPolicy, image: str, egress: EgressRequest | None
    ) -> list[str]:
        uid, gid = os.getuid(), os.getgid()
        args = [
            "docker", "create", "-i", "--name", job.container,
            "--label", f"{RESOURCE_LABEL}={job.id}",
            "--user", f"{uid}:{gid}", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
        ]
        if egress is None:
            args += ["--network", "none"]
        else:
            args += [
                "--network", egress.private_network, "--dns", "127.0.0.1",
                "--add-host", f"{PROXY_ALIAS}:{egress.proxy_ip}",
                "-e", f"HTTPS_PROXY=http://{PROXY_ALIAS}:3128",
                "-e", f"https_proxy=http://{PROXY_ALIAS}:3128",
                "-e", "NO_PROXY=", "-e", "no_proxy=",
            ]
        args += [
            "--memory", "8g", "--cpus", "4", "--pids-limit", "512",
            "--read-only", "--tmpfs", "/tmp:rw,size=1g",
            "--log-opt", "max-size=64m", "--log-opt", "max-file=2",
            "-e", "HOME=/tmp", "-w", job.cwd,
        ]
        args += _mount(policy.root.resolve(), "/workspace")
        args += self._protection_mounts(policy)
        for source_id in policy.sources:
            source = self.config.root(source_id)
            args += _mount(source.root.resolve(), f"/sources/{source_id}", readonly=True)
        args.append(image)
        args += job.command
        return args

    def _verify_protection(self, policy: RootPolicy) -> None:
        base = policy.root.resolve()
        for guard in self.config.protected_within(base):
            try:
                resolved = guard.resolve(strict=True)
            except OSError as exc:
                raise ToolError(
                    "protected_source_unavailable", f"{_inner(base, guard)} cannot be resolved"
                ) from exc
            if resolved != guard or not resolved.is_dir():
                raise ToolError(
                    "protected_source_unavailable", f"{_inner(base, guard)} is not the expected directory"
                )
            _refuse_shared_inodes(guard, _inner(base, guard))
        for source_id in policy.sources:
            source = self.config.root(source_id).root.resolve()
            if not source.is_dir():
                raise ToolError("source_unavailable", f"{source_id} is currently unavailable")
            if base in source.parents and not self.config.protects(source):
                raise ToolError("policy_denied", f"{source_id} is also writable inside {policy.id}")

    def _protection_mounts(self, policy: RootPolicy) -> list[str]:
        base = policy.root.resolve()
        args: list[str] = []
        pinned: set[Path] = set()
        for guard in self.config.protected_within(base):
            for ancestor in reversed(guard.parents):
                if base not in ancestor.parents or ancestor in pinned:
                    continue
                pinned.add(ancestor)
                args += _mount(ancestor, _inner(base, ancestor))
            args += _mount(guard, _inner(base, guard), readonly=True)
        return args

    async def _run(
        self, job: Job, policy: RootPolicy, stdin: str | None,
        image: str, egress: EgressRequest | None,
    ) -> None:
        try:
            async with self.slots:
                if job.id not in self.cancel_requested:
                    await self._execute(job, policy, stdin, image, egress)
        except asyncio.CancelledError:
            pass
        finally:
            if job.status not in TERMINAL and job.id in self.cancel_requested:
                job.status = "cancelled"
                job.finished = time.time()
                self.store.save(job)
            self.done[job.id].set()
            self.tasks.pop(job.id, None)

    async def _execute(
        self, job: Job, policy: RootPolicy, stdin: str | None,
        image: str, egress: EgressRequest | None,
    ) -> None:
        try:
            if egress is not None:
                await self._prepare_egress(egress)
                egress = EgressRequest(
                    egress.job_id, egress.hosts, await self._proxy_ip(egress)
                )
            created = await self._docker(*self._docker_create_args(job, policy, image, egress)[1:])
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
                "docker", "start", "-a", "-i", job.container,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
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
        except Exception as exc:
            log.exception("Host sandbox job failed")
            if job.status not in TERMINAL:
                await self._docker("rm", "-f", job.container)
                job.status = "failed"
                job.finished = time.time()
                self.store.save(job)
                self._write(job, "stderr", str(exc))
        finally:
            if job.id in self.cancel_requested:
                await self._remove_labeled("container", job.container, "rm", "-f")
            if egress is not None:
                try:
                    await self._cleanup_egress(job)
                except (ToolError, OSError) as exc:
                    if job.status == "succeeded":
                        job.status = "failed"
                        job.finished = time.time()
                        self.store.save(job)
                        self._write(job, "stderr", f"egress cleanup failed: {exc}")

    async def _prepare_egress(self, egress: EgressRequest) -> None:
        labels = ["--label", f"{RESOURCE_LABEL}={egress.job_id}"]
        script = Path(__file__).with_name("egress.py").resolve()
        if not script.is_file():
            raise ToolError("egress_unavailable", "egress proxy script is unavailable")
        private = await self._docker(
            "network", "create", "--driver", "bridge", "--internal", "--ipv6=false",
            "-o", "com.docker.network.bridge.gateway_mode_ipv4=isolated",
            *labels, egress.private_network,
        )
        if private.returncode != 0:
            raise ToolError("egress_unavailable", "private egress network could not be created")
        uplink = await self._docker(
            "network", "create", "--driver", "bridge", "--ipv6=false",
            "-o", "com.docker.network.bridge.enable_icc=false",
            *labels, egress.uplink_network,
        )
        if uplink.returncode != 0:
            raise ToolError("egress_unavailable", "egress uplink network could not be created")
        proxy = await self._docker(
            "create", "--name", egress.proxy_container, *labels,
            *_mount(script, "/opt/pah-egress.py", readonly=True),
            "--user", "65532:65532", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--network", egress.uplink_network,
            "--memory", "256m", "--cpus", "0.5", "--pids-limit", "64",
            "--read-only", "--tmpfs", "/tmp:rw,size=64m",
            "--log-opt", "max-size=16m", "--log-opt", "max-file=1",
            "-e", "HOME=/tmp", "-e", "PAH_EGRESS_PORT=3128",
            "-e", "PAH_EGRESS_ALLOWLIST_JSON=" + json.dumps(egress.hosts),
            self.config.egress_proxy_image, "python3", "/opt/pah-egress.py",
        )
        if proxy.returncode != 0:
            raise ToolError("egress_unavailable", "egress proxy could not be created")
        attached = await self._docker(
            "network", "connect", egress.private_network, egress.proxy_container
        )
        if attached.returncode != 0:
            raise ToolError("egress_unavailable", "egress proxy could not be isolated")
        started = await self._docker("start", egress.proxy_container)
        if started.returncode != 0:
            raise ToolError("egress_unavailable", "egress proxy could not start")
        running = await self._docker(
            "inspect", "--format", "{{.State.Running}}", egress.proxy_container
        )
        if running.returncode != 0 or running.stdout.strip() != "true":
            raise ToolError("egress_unavailable", "egress proxy did not become ready")

    async def _proxy_ip(self, egress: EgressRequest) -> str:
        template = '{{with index .NetworkSettings.Networks "' + egress.private_network + '"}}{{.IPAddress}}{{end}}'
        result = await self._docker("inspect", "--format", template, egress.proxy_container)
        address = result.stdout.strip()
        try:
            private_address = ipaddress.ip_address(address)
        except ValueError as exc:
            raise ToolError("egress_unavailable", "egress proxy address is invalid") from exc
        if not private_address.is_private:
            raise ToolError("egress_unavailable", "egress proxy address is not private")
        if result.returncode != 0 or not address:
            raise ToolError("egress_unavailable", "egress proxy address is unavailable")
        return address


    async def _finish_existing(self, job: Job) -> None:
        process = await asyncio.create_subprocess_exec(
            "docker", "logs", "--follow", job.container,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.gather(
            self._pump(job, "stdout", process.stdout, append=False),
            self._pump(job, "stderr", process.stderr, append=False),
        )
        await process.wait()
        await self._finalize(job, timed_out=False)
        try:
            await self._cleanup_egress(job)
        except (ToolError, OSError) as exc:
            if job.status == "succeeded":
                job.status = "failed"
                job.finished = time.time()
                self.store.save(job)
                self._write(job, "stderr", f"egress cleanup failed: {exc}")
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

    async def _cleanup_egress(self, job: Job) -> None:
        egress = EgressRequest(job.id, ())
        await self._remove_labeled("container", egress.proxy_container, "rm", "-f")
        await self._remove_labeled("network", egress.private_network, "network", "rm")
        await self._remove_labeled("network", egress.uplink_network, "network", "rm")

    async def _remove_labeled(self, kind: str, name: str, *remove: str) -> None:
        template = (
            '{{index .Config.Labels "' + RESOURCE_LABEL + '"}}'
            if kind == "container"
            else '{{index .Labels "' + RESOURCE_LABEL + '"}}'
        )
        inspected = await self._docker("inspect", "--format", template, name)
        if inspected.returncode != 0:
            if "No such" in inspected.stderr:
                return
            raise ToolError("egress_cleanup_failed", "egress resource could not be inspected")
        if inspected.stdout.strip() != name.rsplit("-", 1)[-1]:
            raise ToolError("egress_cleanup_failed", "egress resource label does not match")
        removed = await self._docker(*remove, name)
        if removed.returncode != 0:
            raise ToolError("egress_cleanup_failed", "egress resource could not be removed")

    async def _pump(self, job: Job, stream: str, reader: asyncio.StreamReader | None, append: bool = True) -> None:
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
            "docker", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        communicate = asyncio.create_task(process.communicate())
        try:
            out, err = await asyncio.shield(communicate)
        except asyncio.CancelledError:
            out, err = await communicate
            raise

        class Result:
            returncode = process.returncode
            stdout = out.decode("utf-8", errors="replace")
            stderr = err.decode("utf-8", errors="replace")

        return Result()


def _incomplete_tail(chunk: bytes) -> int | None:
    try:
        chunk.decode("utf-8")
        return None
    except UnicodeDecodeError as exc:
        return exc.start if exc.start >= len(chunk) - 3 else None


def clamp_timeout(value: int | None) -> int:
    if value is None:
        return 1800
    return max(1, min(int(value), LIMITS["timeout_s"]))
