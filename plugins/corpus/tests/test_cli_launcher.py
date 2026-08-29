from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from corpus.database import utc_now, workspace_connection


def register_workspace(data_root: Path, workspace_id: str, root: Path) -> None:
    observed = root.stat()
    now = utc_now()
    with workspace_connection(data_root) as connection:
        connection.execute(
            """
            INSERT INTO workspaces(
                workspace_id, context_id, display_name, root_path,
                root_path_nfc, root_device, root_inode, execution_policy,
                current_relative_path, generation, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, 1, ?, ?)
            """,
            (
                workspace_id,
                f"context-{workspace_id}",
                workspace_id,
                str(root),
                str(root),
                observed.st_dev,
                observed.st_ino,
                "external_host_allowed",
                now,
                now,
            ),
        )


class CLILauncherTest(unittest.TestCase):
    def test_runtime_environment_cannot_overlap_a_work_folder(self) -> None:
        project = Path(__file__).resolve().parents[1]
        for launcher_name in ("corpus", "corpus-mcp"):
            with (
                self.subTest(launcher=launcher_name),
                tempfile.TemporaryDirectory() as temporary,
            ):
                base = Path(temporary)
                isolated = base / "plugin"
                (isolated / "launchers").mkdir(parents=True)
                shutil.copytree(project / "src/corpus", isolated / "src/corpus")
                launcher = isolated / "launchers" / launcher_name
                shutil.copy2(project / "launchers" / launcher_name, launcher)
                launcher.chmod(0o700)

                data = base / "private-data"
                workspace = base / "work"
                workspace.mkdir(mode=0o700)
                register_workspace(data, "drafts", workspace)

                uv_marker = base / "uv-ran"
                fake_uv = base / "uv"
                fake_uv.write_text(
                    '#!/bin/sh\n: > "$UV_MARKER"\nexit 0\n', encoding="utf-8"
                )
                fake_uv.chmod(0o700)
                environment = {
                    **os.environ,
                    "PYTHON": sys.executable,
                    "UV": str(fake_uv),
                    "UV_MARKER": str(uv_marker),
                    "CORPUS_PYTHON_ENV": str(workspace / "python-env"),
                    "CORPUS_DATA_DIR": str(data),
                }
                arguments = (
                    ["--data-root", str(data), "workspace", "list"]
                    if launcher_name == "corpus"
                    else []
                )
                completed = subprocess.run(
                    [str(launcher), *arguments],
                    capture_output=True,
                    check=False,
                    text=True,
                    env=environment,
                    timeout=30,
                )

                self.assertNotEqual(completed.returncode, 0)
                self.assertIn('"code":"unsafe_runtime"', completed.stderr)
                self.assertFalse(uv_marker.exists())
                self.assertFalse((workspace / "python-env").exists())


if __name__ == "__main__":
    unittest.main()
