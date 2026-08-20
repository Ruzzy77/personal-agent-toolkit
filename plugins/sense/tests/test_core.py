from __future__ import annotations

import asyncio
import json
import sqlite3
import stat
from pathlib import Path

import pytest
from sense.errors import (
    ConfirmationRequiredError,
    SectionConflictError,
    UnsafeStorageError,
)
from sense.exposure import guidance_overview
from sense.mcp_server import create_server
from sense.model import ProfileDocument, ProfileSection, SectionChange, section_sha256
from sense.service import SenseService
from sense.store import SenseStore


def section(section_id: str, text: str, *, sensitive: bool = False) -> ProfileSection:
    return ProfileSection(
        id=section_id,
        purpose=f"Purpose for {section_id}",
        text=text,
        origins=["user_set"],
        sensitivity="sensitive" if sensitive else "ordinary",
    )


def profile() -> ProfileDocument:
    return ProfileDocument(
        sections=[
            section("questions-and-choices", "Use independent judgment."),
            section("conversation-and-writing", "Write for the intended reader."),
        ]
    )


def change(previous: ProfileSection, text: str) -> SectionChange:
    return SectionChange(
        section_id=previous.id,
        previous_section_sha256=section_sha256(previous),
        new_section=previous.model_copy(update={"text": text}),
    )


def test_legacy_profile_becomes_one_current_state_without_provenance(
    tmp_path: Path,
) -> None:
    marker = "REMOVE-LEGACY-SOURCE-7f3e9832"
    root = tmp_path / "Sense"
    root.mkdir(mode=0o700)
    database = root / "sense.sqlite3"
    legacy = {
        "schema_version": 1,
        "revision": 18,
        "sections": [
            {
                "id": "working-together",
                "purpose": "Choose carefully.",
                "text": "Use independent judgment.",
                "origins": ["user_set", "learned_from_work"],
                "use_for": [],
                "review_when": [],
                "sensitivity": "ordinary",
                "source_refs": [
                    {
                        "kind": "file",
                        "locator": f"/private/{marker}.md",
                        "sha256": "a" * 64,
                        "origin": "user_set",
                    }
                ],
            }
        ],
        "controls": {
            "raw_conversation_storage": "never",
            "sensitive_persistence": "explicit_confirmation",
            "external_effects": "responsibility_based",
            "provider_memory_management": "provider_owned",
        },
    }
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE current_profile (
                singleton INTEGER PRIMARY KEY,
                lifecycle TEXT NOT NULL,
                revision INTEGER NOT NULL,
                profile_json TEXT NOT NULL,
                profile_sha256 TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE profile_revisions (
                revision INTEGER PRIMARY KEY,
                profile_json TEXT NOT NULL,
                profile_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE remote_operation_replays (
                operation TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                request_sha256 TEXT NOT NULL,
                response_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE runtime_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """
        )
        serialized = json.dumps(legacy)
        connection.execute(
            "INSERT INTO current_profile VALUES (1, 'active', 18, ?, ?, ?)",
            (serialized, "b" * 64, "2026-08-20T00:00:00Z"),
        )
        connection.execute(
            "INSERT INTO profile_revisions VALUES (17, ?, ?, ?)",
            (serialized, "c" * 64, "2026-08-19T00:00:00Z"),
        )
        connection.execute(
            "INSERT INTO remote_operation_replays VALUES (?, ?, ?, ?, ?)",
            ("revise", "old-key", "d" * 64, marker, "2026-08-20T00:00:00Z"),
        )
    database.chmod(0o600)

    stored = SenseService(root).store.read()
    item = stored.profile.sections[0]
    assert item.id == "questions-and-choices"
    assert item.origins == ["user_set", "learned_from_results"]
    assert set(item.model_dump()) == {"id", "purpose", "text", "origins", "sensitivity"}

    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert tables == {"current_profile"}
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
    assert marker.encode() not in database.read_bytes()
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE(database.stat().st_mode) == 0o600


def test_atomic_update_uses_only_section_tokens_and_natural_noop(
    tmp_path: Path,
) -> None:
    store = SenseStore(tmp_path / "Sense")
    store.initialize(profile())
    original = store.read()
    first, second = original.profile.sections
    changes = [
        change(first, "Use one final judgment."),
        change(second, "Write the final result for its reader."),
    ]

    result = store.revise(changes=changes)
    repeated = store.revise(changes=changes)
    assert result == {
        "effect": "sections_updated",
        "changed_section_ids": ["conversation-and-writing", "questions-and-choices"],
        "unchanged_section_ids": [],
    }
    assert repeated["effect"] == "no_change"

    current = store.read()
    valid = change(current.profile.sections[0], "Must not be partially written.")
    invalid = SectionChange(
        section_id="conversation-and-writing",
        previous_section_sha256="0" * 64,
        new_section=current.profile.sections[1].model_copy(
            update={"text": "Conflict."}
        ),
    )
    with pytest.raises(SectionConflictError):
        store.revise(changes=[valid, invalid])
    assert store.read().profile == current.profile


def test_sensitive_guidance_needs_local_confirmation_and_stays_out_of_overview(
    tmp_path: Path,
) -> None:
    document = ProfileDocument(
        sections=[
            section("questions-and-choices", "Use independent judgment."),
            section("private-life", "SECRET-MARKER", sensitive=True),
        ]
    )
    store = SenseStore(tmp_path / "Sense")
    store.initialize(document)
    sensitive = store.read().profile.sections[1]
    update = change(sensitive, "UPDATED-SECRET")

    with pytest.raises(ConfirmationRequiredError):
        store.revise(changes=[update])
    store.revise(changes=[update], user_confirmed=True)

    overview = guidance_overview(
        store.read().profile,
        updated_at="2026-08-20T00:00:00Z",
    )
    assert "UPDATED-SECRET" not in str(overview)


def test_permanent_deletion_requires_confirmation(tmp_path: Path) -> None:
    store = SenseStore(tmp_path / "Sense")
    store.initialize(profile())
    target = store.read().profile.sections[0]
    with pytest.raises(ConfirmationRequiredError):
        store.remove_section(
            section_id=target.id,
            previous_section_sha256=section_sha256(target),
            user_confirmed=False,
        )
    store.remove_section(
        section_id=target.id,
        previous_section_sha256=section_sha256(target),
        user_confirmed=True,
    )
    assert [item.id for item in store.read().profile.sections] == [
        "conversation-and-writing"
    ]


def test_storage_rejects_a_symlinked_data_root(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    link = tmp_path / "Sense"
    link.symlink_to(target, target_is_directory=True)

    with pytest.raises(UnsafeStorageError, match="symbolic link"):
        SenseStore(link).initialize(profile())
    assert list(target.iterdir()) == []


def test_mcp_exposes_only_read_overview_and_update(tmp_path: Path) -> None:
    root = tmp_path / "Sense"
    SenseStore(root).initialize(profile())
    server = create_server(root)
    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}

    assert set(tools) == {"sense_read", "sense_overview", "sense_revise"}
    revise_schema = str(tools["sense_revise"].input_schema)
    for removed_field in (
        "expected_revision",
        "idempotency_key",
        "previous_understanding",
        "changed_future_judgment",
        "source_refs",
        "use_for",
        "review_when",
    ):
        assert removed_field not in revise_schema
    result = asyncio.run(server.call_tool("sense_read", {"view": "index"}))
    assert result.structured_content["ok"] is True
    assert set(result.structured_content["result"]) == {"sections"}
