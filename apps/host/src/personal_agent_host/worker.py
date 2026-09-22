"""Detached worker for direct Host jobs.

The Host launches this module from its installed interpreter. It owns the command
process and persists enough identity information for a later Host process to
attach without guessing about PID reuse.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import select
import signal
import subprocess
import threading
import time
from pathlib import Path

OUTPUT_CAP = 64 * 1024 * 1024
INPUT_CAP = 1_048_576
GRACE_S = 5.0


def _atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def _boot_id() -> str | None:
    try:
        return (
            Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
        )
    except OSError:
        return None


def _start_time(pid: int) -> str | None:
    try:
        text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        return text[text.rfind(")") + 2 :].split()[19]
    except (OSError, IndexError):
        return None


class Output:
    def __init__(self, directory: Path) -> None:
        self.remaining = OUTPUT_CAP
        self.lock = threading.Lock()
        self.truncated = False
        self.handles = {
            "stdout": (directory / "stdout").open("ab", buffering=0),
            "stderr": (directory / "stderr").open("ab", buffering=0),
        }

    def pump(self, stream: str, source) -> None:
        read = getattr(source, "read1", source.read)
        try:
            while chunk := read(65_536):
                with self.lock:
                    allowed = min(len(chunk), self.remaining)
                    if allowed:
                        self.handles[stream].write(chunk[:allowed])
                        self.remaining -= allowed
                    if allowed != len(chunk):
                        self.truncated = True
        except OSError:
            pass
        finally:
            with contextlib.suppress(OSError):
                source.close()

    def close(self) -> None:
        for handle in self.handles.values():
            handle.close()


def _terminate_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + GRACE_S
    while time.monotonic() < deadline:
        try:
            os.killpg(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    with contextlib.suppress(ProcessLookupError):
        os.killpg(pid, signal.SIGKILL)


def _drain_fifo(fd: int, pending: bytearray) -> None:
    while len(pending) < INPUT_CAP:
        try:
            data = os.read(fd, min(65_536, INPUT_CAP - len(pending)))
        except BlockingIOError:
            return
        if not data:
            return
        pending.extend(data)


def run(directory: Path, cwd: str, command: list[str], keep_stdin_open: bool) -> int:
    directory.mkdir(parents=True, exist_ok=True)
    initial = directory / "stdin.initial"
    fifo = directory / "stdin.fifo"
    close_request = directory / "stdin.close"
    if keep_stdin_open:
        with contextlib.suppress(FileExistsError):
            os.mkfifo(fifo, 0o600)

    output = Output(directory)
    child = subprocess.Popen(
        command,
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        bufsize=0,
    )
    assert (
        child.stdin is not None
        and child.stdout is not None
        and child.stderr is not None
    )
    _atomic_json(
        directory / "run.json",
        {
            "pid": child.pid,
            "pid_start": _start_time(child.pid),
            "boot_id": _boot_id(),
            "worker_pid": os.getpid(),
            "worker_start": _start_time(os.getpid()),
            "started": time.time(),
            "keep_stdin_open": keep_stdin_open,
        },
    )

    stdout = threading.Thread(
        target=output.pump, args=("stdout", child.stdout), daemon=True
    )
    stderr = threading.Thread(
        target=output.pump, args=("stderr", child.stderr), daemon=True
    )
    stdout.start()
    stderr.start()

    pending = bytearray(initial.read_bytes()) if initial.exists() else bytearray()
    initial.unlink(missing_ok=True)
    stdin_fd = child.stdin.fileno()
    os.set_blocking(stdin_fd, False)
    reader_fd: int | None = (
        os.open(fifo, os.O_RDWR | os.O_NONBLOCK) if keep_stdin_open else None
    )
    eof_requested = not keep_stdin_open
    cancelled = False
    timed_out = False
    try:
        deadline = float((directory / "deadline").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        deadline = time.monotonic() + 1

    try:
        while child.poll() is None:
            if (directory / "cancel").exists():
                cancelled = True
                _terminate_group(child.pid)
                break
            if time.monotonic() >= deadline:
                timed_out = True
                _terminate_group(child.pid)
                break

            read_fds = (
                [reader_fd]
                if reader_fd is not None and len(pending) < INPUT_CAP
                else []
            )
            write_fds = [stdin_fd] if pending else []
            readable, writable, _ = select.select(read_fds, write_fds, [], 0.1)
            if reader_fd is not None and reader_fd in readable:
                _drain_fifo(reader_fd, pending)
            if close_request.exists():
                eof_requested = True
                if reader_fd is not None:
                    _drain_fifo(reader_fd, pending)
            if stdin_fd in writable and pending:
                try:
                    count = os.write(stdin_fd, pending)
                    del pending[:count]
                except (BlockingIOError, BrokenPipeError):
                    pass
            if eof_requested and not pending:
                if reader_fd is not None:
                    _drain_fifo(reader_fd, pending)
                if not pending:
                    with contextlib.suppress(OSError):
                        child.stdin.close()
                    if reader_fd is not None:
                        os.close(reader_fd)
                        reader_fd = None
                    fifo.unlink(missing_ok=True)

        returncode = child.wait()
    finally:
        if reader_fd is not None:
            os.close(reader_fd)
        fifo.unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            child.stdin.close()
        stdout.join(timeout=1)
        stderr.join(timeout=1)
        if stdout.is_alive():
            with contextlib.suppress(OSError):
                child.stdout.close()
        if stderr.is_alive():
            with contextlib.suppress(OSError):
                child.stderr.close()
        stdout.join(timeout=1)
        stderr.join(timeout=1)
        output.close()

    cancelled = cancelled or (directory / "cancel").exists()
    status = (
        "cancelled"
        if cancelled
        else "timed_out"
        if timed_out
        else ("succeeded" if returncode == 0 else "failed")
    )
    _atomic_json(
        directory / "result.json",
        {
            "status": status,
            "exit_code": returncode,
            "finished": time.time(),
            "truncated": output.truncated,
        },
    )
    return returncode


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-dir", required=True)
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--command-json", required=True)
    parser.add_argument("--keep-stdin-open", action="store_true")
    args = parser.parse_args()
    directory = Path(args.job_dir)
    try:
        run(directory, args.cwd, json.loads(args.command_json), args.keep_stdin_open)
    except Exception as exc:
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / "stderr").open("ab") as handle:
            handle.write(
                f"direct worker failed: {exc}\n".encode("utf-8", errors="replace")
            )
        _atomic_json(
            directory / "result.json",
            {
                "status": "failed",
                "exit_code": None,
                "finished": time.time(),
                "truncated": False,
            },
        )
        raise


if __name__ == "__main__":
    main()
