from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from personal_agent_host.config import RootPolicy
from personal_agent_host.files import ABSENT, ToolError
from personal_agent_host.transfers import (
    CHUNK_BYTES,
    MAX_FILE_BYTES,
    PREFIX,
    Transfers,
)


class _Config:
    def __init__(self, root: Path, data_root: Path) -> None:
        self.sync = SimpleNamespace(data_root=data_root)
        self.policy = RootPolicy("workspace", root, "read_write", "none", ())
        self.protected = root / "protected.bin"

    def protects(self, target: Path) -> bool:
        return target == self.protected

    def root(self, root_id: str) -> RootPolicy:
        if root_id != self.policy.id:
            raise ValueError("unknown root")
        return self.policy


class TransferTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name)
        self.root = base / "root"
        self.root.mkdir()
        self.config = _Config(self.root, base / "data")
        self.policy = self.config.policy

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_chunk_limit(self) -> None:
        self.assertEqual(CHUNK_BYTES, 8 * 1024 * 1024)

    def test_restart_continues_upload_without_storing_token(self) -> None:
        payload = b"\x00" + "한국어".encode() + b"\xfftail"
        digest = hashlib.sha256(payload).hexdigest()
        first = Transfers(self.config)
        started = first.begin_upload(
            self.policy, "결과.bin", len(payload), ABSENT, digest
        )
        first.write_chunk(
            started["transfer_id"], started["token"], 0, payload[:5]
        )

        state_path = self.config.sync.data_root / "host-transfers" / "transfers.json"
        state = state_path.read_text()
        self.assertNotIn(started["token"], state)
        self.assertEqual(json.loads(state)["transfers"][0]["offset"], 5)

        resumed = Transfers(self.config)
        self.assertEqual(
            resumed.status(started["transfer_id"], started["token"])["offset"], 5
        )
        resumed.write_chunk(
            started["transfer_id"], started["token"], 0, payload[:5]
        )
        resumed.write_chunk(
            started["transfer_id"], started["token"], 5, payload[5:]
        )
        result = resumed.commit(started["transfer_id"], started["token"])

        self.assertEqual((self.root / "결과.bin").read_bytes(), payload)
        self.assertEqual(result["version"], "sha256:" + digest)

    def test_expired_recorded_upload_is_cleaned_after_restart(self) -> None:
        transfers = Transfers(self.config)
        started = transfers.begin_upload(self.policy, "partial.bin", 1, ABSENT)
        staging = self.root / f"{PREFIX}{started['transfer_id']}"
        self.assertTrue(staging.exists())
        transfers.items[started["transfer_id"]].expires_at = time.time() - 1
        transfers._save()

        resumed = Transfers(self.config)
        self.assertFalse(staging.exists())
        with self.assertRaises(ToolError):
            resumed.status(started["transfer_id"], started["token"])

    def test_concurrent_conflicting_chunk_has_one_winner(self) -> None:
        transfers = Transfers(self.config)
        started = transfers.begin_upload(self.policy, "race.bin", 3, ABSENT)
        barrier = threading.Barrier(3)
        results: list[tuple[bytes, bool]] = []
        results_lock = threading.Lock()

        def write(payload: bytes) -> None:
            barrier.wait()
            try:
                transfers.write_chunk(
                    started["transfer_id"], started["token"], 0, payload
                )
                accepted = True
            except ToolError:
                accepted = False
            with results_lock:
                results.append((payload, accepted))

        workers = [
            threading.Thread(target=write, args=(payload,))
            for payload in (b"one", b"two")
        ]
        for worker in workers:
            worker.start()
        barrier.wait()
        for worker in workers:
            worker.join()

        accepted = [payload for payload, success in results if success]
        self.assertEqual(len(accepted), 1)
        staging = self.root / f"{PREFIX}{started['transfer_id']}"
        self.assertEqual(staging.read_bytes(), accepted[0])
        self.assertEqual(
            transfers.status(started["transfer_id"], started["token"])["offset"], 3
        )
        state_path = self.config.sync.data_root / "host-transfers" / "transfers.json"
        offset = json.loads(state_path.read_text())["transfers"][0]["offset"]
        self.assertEqual(offset, 3)

    def test_download_rejects_sparse_file_larger_than_limit(self) -> None:
        oversized = self.root / "large.bin"
        with oversized.open("wb") as handle:
            handle.seek(MAX_FILE_BYTES)
            handle.write(b"x")

        with self.assertRaises(ToolError) as raised:
            Transfers(self.config).begin_download(self.policy, "large.bin")

        self.assertEqual(raised.exception.code, "too_large")

    def test_protected_file_can_be_downloaded(self) -> None:
        self.config.protected.write_bytes(b"read me")
        transfers = Transfers(self.config)

        started = transfers.begin_download(self.policy, "protected.bin")

        self.assertEqual(
            transfers.read_chunk(started["transfer_id"], started["token"], 0, 8),
            b"read me",
        )

    def test_symlink_swap_is_denied_for_download(self) -> None:
        original = self.root / "original.bin"
        original.write_bytes(b"inside")
        link = self.root / "link.bin"
        link.symlink_to(original.name)
        transfers = Transfers(self.config)
        started = transfers.begin_download(self.policy, "link.bin")
        outside = Path(self.temporary.name) / "outside.bin"
        outside.write_bytes(b"outside")
        link.unlink()
        link.symlink_to(outside)

        with self.assertRaises(ToolError):
            transfers.download_info(started["transfer_id"], started["token"])


if __name__ == "__main__":
    unittest.main()
