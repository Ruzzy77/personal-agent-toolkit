import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from personal_agent_host.cli import _copy_sqlite, _rsync
from personal_agent_host.config import _backup
from personal_agent_sync.errors import SyncError


def test_registered_sqlite_paths_stay_inside_the_existing_backup(tmp_path: Path):
    root = tmp_path / "workspace"
    root.mkdir()
    config = _backup(
        {
            "target": str(tmp_path / "nas"),
            "paths": [str(root)],
            "sqlite_paths": [str(root / "flow" / "workspace.sqlite")],
        }
    )
    assert config.sqlite_paths == (root / "flow" / "workspace.sqlite",)
    with pytest.raises(SyncError):
        _backup(
            {
                "target": str(tmp_path / "nas"),
                "paths": [str(root)],
                "sqlite_paths": [str(tmp_path / "outside.sqlite")],
            }
        )


def test_live_wal_database_is_safely_copied_and_restored(tmp_path: Path):
    source = tmp_path / "workspace.sqlite"
    live = sqlite3.connect(source)
    live.execute("PRAGMA journal_mode=WAL")
    live.execute("CREATE TABLE versions(id INTEGER PRIMARY KEY, body TEXT)")
    live.execute("INSERT INTO versions VALUES(1,'보존할 본문')")
    live.commit()
    destination = tmp_path / "nas" / "workspace.sqlite"
    _copy_sqlite(source, destination)
    live.execute("INSERT INTO versions VALUES(2,'다음 변경')")
    live.commit()
    restored = tmp_path / "restore" / "workspace.sqlite"
    _copy_sqlite(destination, restored)
    with sqlite3.connect(restored) as reader:
        assert reader.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert reader.execute("SELECT * FROM versions").fetchall() == [
            (1, "보존할 본문")
        ]
    _copy_sqlite(source, destination)
    with sqlite3.connect(destination) as reader:
        assert reader.execute("SELECT count(*) FROM versions").fetchone()[0] == 2
    assert not list(destination.parent.glob("*.tmp"))
    live.close()


def test_rsync_excludes_registered_database_and_live_journals_only(tmp_path: Path):
    root = tmp_path / "workspace"
    root.mkdir()
    database = root / ".data" / "workspace.sqlite"
    with patch("personal_agent_host.cli.subprocess.run") as run:
        _rsync(root, tmp_path / "nas", (database,))
    command = run.call_args.args[0]
    assert "--exclude=/.data/workspace.sqlite" in command
    assert "--exclude=/.data/workspace.sqlite-*" in command
    assert "--exclude=*.sqlite" not in command
