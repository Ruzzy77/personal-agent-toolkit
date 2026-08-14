"""Process-level single-writer lock for mutable corpus state."""

from __future__ import annotations

import fcntl
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .config import RuntimePaths, open_private_file_at, private_directory
from .errors import CorpusError


class WriterBusyError(CorpusError):
    code = "writer_busy"


@contextmanager
def writer_lock(lock_path: Path, *, timeout_seconds: float = 30) -> Iterator[None]:
    paths = RuntimePaths(
        data_root=lock_path.parent.parent.parent,
        corpus_id=lock_path.parent.name,
    )
    if lock_path != paths.corpus_root / "writer.lock":
        raise WriterBusyError(
            "writer lock path is outside the private corpus runtime",
            details={"lock_path": str(lock_path)},
        )
    with paths.open_corpus_root() as parent_descriptor:
        descriptor, _ = open_private_file_at(
            parent_descriptor,
            lock_path.name,
            path=lock_path,
            flags=os.O_RDWR,
            create=True,
        )
        deadline = time.monotonic() + timeout_seconds
        try:
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError as exc:
                    if time.monotonic() >= deadline:
                        raise WriterBusyError(
                            "another Corpus writer is active",
                            details={
                                "lock_path": str(lock_path),
                                "timeout_seconds": timeout_seconds,
                            },
                        ) from exc
                    time.sleep(0.05)
            yield
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


@contextmanager
def context_writer_lock(
    data_root: Path,
    *,
    timeout_seconds: float = 30,
) -> Iterator[None]:
    lock_path = data_root / "contexts.writer.lock"
    with private_directory(data_root, create=True) as parent_descriptor:
        descriptor, _ = open_private_file_at(
            parent_descriptor,
            lock_path.name,
            path=lock_path,
            flags=os.O_RDWR,
            create=True,
        )
        deadline = time.monotonic() + timeout_seconds
        try:
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError as exc:
                    if time.monotonic() >= deadline:
                        raise WriterBusyError(
                            "another Corpus context writer is active",
                            details={
                                "lock_path": str(lock_path),
                                "timeout_seconds": timeout_seconds,
                            },
                        ) from exc
                    time.sleep(0.05)
            yield
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


@contextmanager
def context_reader_lock(
    data_root: Path,
    *,
    timeout_seconds: float = 30,
) -> Iterator[None]:
    """Hold a shared tenant-state lock across a coherent remote read.

    Source generation apply and remote deletion use the exclusive side of the
    same lock. Creating the private lock inode is coordination metadata only;
    no Corpus, context, source, or index content is changed by a reader.
    """

    lock_path = data_root / "contexts.writer.lock"
    with private_directory(data_root, create=True) as parent_descriptor:
        descriptor, _ = open_private_file_at(
            parent_descriptor,
            lock_path.name,
            path=lock_path,
            flags=os.O_RDWR,
            create=True,
        )
        deadline = time.monotonic() + timeout_seconds
        try:
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
                    break
                except BlockingIOError as exc:
                    if time.monotonic() >= deadline:
                        raise WriterBusyError(
                            "Corpus state is changing during this read",
                            details={
                                "lock_path": str(lock_path),
                                "timeout_seconds": timeout_seconds,
                            },
                        ) from exc
                    time.sleep(0.05)
            yield
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


@contextmanager
def workspace_writer_lock(
    data_root: Path,
    *,
    timeout_seconds: float = 30,
) -> Iterator[None]:
    """Serialize updates to workspace registrations and recovery metadata."""

    lock_path = data_root / "workspaces.writer.lock"
    with private_directory(data_root, create=True) as parent_descriptor:
        descriptor, _ = open_private_file_at(
            parent_descriptor,
            lock_path.name,
            path=lock_path,
            flags=os.O_RDWR,
            create=True,
        )
        deadline = time.monotonic() + timeout_seconds
        try:
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError as exc:
                    if time.monotonic() >= deadline:
                        raise WriterBusyError(
                            "another Corpus workspace writer is active",
                            details={
                                "lock_path": str(lock_path),
                                "timeout_seconds": timeout_seconds,
                            },
                        ) from exc
                    time.sleep(0.05)
            yield
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


@contextmanager
def space_writer_lock(
    data_root: Path,
    *,
    timeout_seconds: float = 30,
) -> Iterator[None]:
    """Serialize canonical Space registry and migration updates."""

    lock_path = data_root / "spaces.writer.lock"
    with private_directory(data_root, create=True) as parent_descriptor:
        descriptor, _ = open_private_file_at(
            parent_descriptor,
            lock_path.name,
            path=lock_path,
            flags=os.O_RDWR,
            create=True,
        )
        deadline = time.monotonic() + timeout_seconds
        try:
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError as exc:
                    if time.monotonic() >= deadline:
                        raise WriterBusyError(
                            "another Corpus Space writer is active",
                            details={
                                "lock_path": str(lock_path),
                                "timeout_seconds": timeout_seconds,
                            },
                        ) from exc
                    time.sleep(0.05)
            yield
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


@contextmanager
def source_workspace_registry_lock(
    data_root: Path,
    *,
    timeout_seconds: float = 30,
) -> Iterator[None]:
    """Serialize source-root and editable-workspace registry decisions."""

    lock_path = data_root / "source-workspace-registry.writer.lock"
    with private_directory(data_root, create=True) as parent_descriptor:
        descriptor, _ = open_private_file_at(
            parent_descriptor,
            lock_path.name,
            path=lock_path,
            flags=os.O_RDWR,
            create=True,
        )
        deadline = time.monotonic() + timeout_seconds
        try:
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError as exc:
                    if time.monotonic() >= deadline:
                        raise WriterBusyError(
                            "another Corpus source or workspace registry writer is active",
                            details={
                                "lock_path": str(lock_path),
                                "timeout_seconds": timeout_seconds,
                            },
                        ) from exc
                    time.sleep(0.05)
            yield
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)
