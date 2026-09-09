"""Bounded File Provider capture through the installed, isolated Corpus helper."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .errors import SyncError

SF_DATALESS = 0x40000000
DOWNLOAD_TIMEOUT_SECONDS = 120
_BUILD_HELPER = """
import sys
from pathlib import Path
from corpus.capture import build_native_helper
from corpus.config import RuntimePaths
print(build_native_helper(RuntimePaths(Path(sys.argv[1]), sys.argv[2])))
"""


@dataclass(frozen=True)
class NativeCapture:
    corpus_python: Path | None
    corpus_data_root: Path | None
    corpus_id: str

    def copy(
        self,
        source_descriptor: int,
        root: Path,
        source: Path,
        destination: Path,
        maximum_bytes: int,
    ) -> dict:
        if (
            sys.platform != "darwin"
            or self.corpus_python is None
            or self.corpus_data_root is None
        ):
            raise SyncError(
                "source_materializer_unavailable",
                "Online-only Source capture requires the installed Corpus runtime",
            )
        try:
            built = subprocess.run(
                [
                    str(self.corpus_python),
                    "-I",
                    "-c",
                    _BUILD_HELPER,
                    str(self.corpus_data_root),
                    self.corpus_id,
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=150,
            )
            helper = Path(built.stdout.strip())
            if (
                built.returncode != 0
                or helper.parent != self.corpus_data_root / "runtime"
            ):
                raise ValueError("native helper was not available")
            metadata = helper.lstat()
            if (
                not helper.name.startswith("corpus-hydrator-")
                or not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != 0o700
                or metadata.st_uid != os.getuid()
            ):
                raise ValueError("unsafe native helper")
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            raise SyncError(
                "source_materializer_unavailable",
                "The local File Provider capture helper is unavailable",
            ) from exc

        directory = os.open(
            destination.parent,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
        try:
            metadata = os.fstat(directory)
            if (
                metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) != 0o700
            ):
                raise SyncError("unsafe_staging", "Capture staging must be private")
            try:
                completed = subprocess.run(
                    [
                        str(helper),
                        "copy",
                        "--source",
                        str(source),
                        "--source-fd",
                        str(source_descriptor),
                        "--source-root",
                        str(root),
                        "--destination",
                        str(destination),
                        "--destination-dir-fd",
                        str(directory),
                        "--destination-name",
                        destination.name,
                        "--max-bytes",
                        str(maximum_bytes),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=DOWNLOAD_TIMEOUT_SECONDS,
                    pass_fds=(source_descriptor, directory),
                )
            except subprocess.TimeoutExpired as exc:
                raise SyncError(
                    "source_download_pending", "File Provider download timed out"
                ) from exc
            except OSError as exc:
                raise SyncError(
                    "source_materializer_unavailable",
                    "File Provider helper could not run",
                ) from exc
            # The native helper writes success to stdout and errors to stderr.
            output = completed.stdout if completed.returncode == 0 else completed.stderr
            try:
                payload = json.loads(output.strip().splitlines()[-1])
                if not isinstance(payload, dict):
                    raise TypeError("invalid helper response")
                if completed.returncode != 0:
                    native_code = payload.get("error", {}).get("code")
                    code = {
                        "source_changed_during_copy": "source_changed",
                        "source_exceeds_maximum_bytes": "source_too_large",
                        "source_download_pending": "source_download_pending",
                        "source_read_failed": "source_download_pending",
                    }.get(native_code, "source_capture_failed")
                    raise SyncError(code, "File Provider capture could not complete")
                result = payload["result"]
                if (
                    payload.get("ok") is not True
                    or result.get("stable") is not True
                    or result.get("identityStable") is not True
                    or result.get("exactByteCount") is not True
                ):
                    raise ValueError("unverified native capture")
                return result
            except (IndexError, KeyError, TypeError, ValueError, AttributeError) as exc:
                raise SyncError(
                    "source_capture_failed",
                    "File Provider capture returned invalid metadata",
                ) from exc
        except BaseException:
            # A killed helper may have left a partial private copy.
            try:
                os.unlink(destination.name, dir_fd=directory)
            except FileNotFoundError:
                pass
            raise
        finally:
            os.close(directory)
