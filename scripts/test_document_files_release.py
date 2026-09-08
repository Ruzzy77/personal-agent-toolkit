"""Checksummed release consumption: no processing-time download or mutable source trust."""

import hashlib
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import document_files_release as release


class ReleaseConsumerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.archive = self.root / "host.zip"
        with zipfile.ZipFile(self.archive, "w") as output:
            output.writestr(
                "document-files/pyproject.toml", '[project]\nversion = "1.8.0"\n'
            )
            output.writestr("document-files/src/example.py", "value = 1\n")
        self.lock = self.root / "lock.json"
        self.data = {
            "schemaVersion": "document-files.release-lock.v1",
            "state": "pinned",
            "version": "1.8.0",
            "sourceCommit": "a" * 40,
            "artifacts": {
                name: {
                    "url": f"https://github.com/Ruzzy77/document-files/releases/download/v1.8.0/{name}.zip",
                    "sha256": hashlib.sha256(self.archive.read_bytes()).hexdigest(),
                }
                for name in ("host", "wheel")
            },
        }
        self.write_lock()
        env = patch.dict(
            os.environ,
            {
                "DOCUMENT_FILES_RELEASE_LOCK": str(self.lock),
                "XDG_CACHE_HOME": str(self.root / "cache"),
            },
        )
        env.start()
        self.addCleanup(env.stop)

    def write_lock(self):
        self.lock.write_text(json.dumps(self.data))

    def test_explicit_prepare_and_repeated_verified_read(self):
        with self.assertRaisesRegex(ValueError, "not prepared"):
            release.document_source()
        release.artifact_path("host", prepare=True, local=self.archive)
        source = release.document_source()
        self.assertEqual(source, release.document_source())
        (source / "src/example.py").write_text("changed")
        with self.assertRaisesRegex(ValueError, "cache changed"):
            release.document_source()

    def test_checksum_rejects_changed_download(self):
        self.archive.write_bytes(b"modified")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            release.artifact_path("host", prepare=True, local=self.archive)

    def test_archive_escape_is_rejected(self):
        with zipfile.ZipFile(self.archive, "a") as output:
            output.writestr("document-files/../../escape", "not allowed")
        self.data["artifacts"]["host"]["sha256"] = hashlib.sha256(
            self.archive.read_bytes()
        ).hexdigest()
        self.write_lock()
        release.artifact_path("host", prepare=True, local=self.archive)
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            release.document_source()
        self.assertFalse((self.root / "escape").exists())

    def test_unqualified_transition_keeps_baseline(self):
        self.data["state"] = "migration_pending"
        self.write_lock()
        self.assertEqual(
            release.document_source(), release.ROOT / "plugins/document-files"
        )

    def test_cached_version_mismatch_rejected(self):
        release.artifact_path("host", prepare=True, local=self.archive)
        release.document_source()
        self.data["version"] = "9.9.9"
        self.write_lock()
        with self.assertRaisesRegex(ValueError, "metadata"):
            release.document_source()

    def test_runtime_foreign_endpoint_rejected(self):
        self.data["artifacts"]["runtime-macos-aarch64"] = {
            "url": "https://example.com/runtime.zip",
            "sha256": "b" * 64,
        }
        self.write_lock()
        with self.assertRaisesRegex(ValueError, "exact independent"):
            release.release_lock()

    def test_unpinned_or_foreign_endpoint_rejected(self):
        self.data["artifacts"]["host"]["url"] = "https://example.com/host.zip"
        self.write_lock()
        with self.assertRaisesRegex(ValueError, "exact independent"):
            release.release_lock()


if __name__ == "__main__":
    unittest.main()
