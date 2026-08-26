from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from personal_agent_remote.tunnel_service import _resolve_uv_program


class ResolveUvProgramTests(unittest.TestCase):
    def test_discovered_symlink_is_preserved_for_package_manager_upgrades(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            versioned = root / "Cellar" / "uv" / "0.2.1" / "bin" / "uv"
            versioned.parent.mkdir(parents=True)
            versioned.write_text("#!/bin/sh\n", encoding="utf-8")
            versioned.chmod(0o755)
            stable = root / "bin" / "uv"
            stable.parent.mkdir()
            stable.symlink_to(versioned)

            with mock.patch(
                "personal_agent_remote.tunnel_service.shutil.which",
                return_value=str(stable),
            ):
                self.assertEqual(_resolve_uv_program(), stable)


if __name__ == "__main__":
    unittest.main()
