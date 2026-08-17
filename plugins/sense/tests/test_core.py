from __future__ import annotations

import asyncio
import stat
from pathlib import Path

import pytest

from sense.errors import PreviewReadOnlyError, RevisionConflictError, UnsafeStorageError
from sense.exposure import guidance_overview
from sense.mcp_server import create_server
from sense.model import ProfileDocument, ProfileSection, SectionRevision, SourceRef, section_sha256
from sense.store import SenseStore


def source_ref() -> SourceRef:
    return SourceRef(
        kind="file",
        locator="/private/owner.md",
        sha256="a" * 64,
        origin="user_set",
    )


def section(section_id: str, text: str, *, sensitive: bool = False) -> ProfileSection:
    return ProfileSection(
        id=section_id,
        purpose=f"Purpose for {section_id}",
        text=text,
        origins=["user_set"],
        use_for=["important choices"],
        review_when=["the guidance changes"],
        sensitivity="sensitive" if sensitive else "ordinary",
        source_refs=[source_ref()],
    )


def profile() -> ProfileDocument:
    return ProfileDocument(
        revision=1,
        sections=[
            section("working-together", "Use independent judgment."),
            section("writing", "Write for the intended reader."),
        ],
    )


def activate(store: SenseStore) -> None:
    current = store.read()
    store.activate(
        expected_revision=current.profile.revision,
        confirm_profile_digest=current.digest,
    )


def change(previous: ProfileSection, text: str) -> SectionRevision:
    return SectionRevision(
        section_id=previous.id,
        previous_section_sha256=section_sha256(previous),
        previous_understanding=f"Previous guidance for {previous.id}.",
        changed_future_judgment=f"Future judgment for {previous.id} changes.",
        new_section=previous.model_copy(update={"text": text}),
    )


def test_private_storage_and_preview_boundary(tmp_path: Path) -> None:
    root = tmp_path / "private" / "Sense"
    store = SenseStore(root)
    current = store.initialize(profile())

    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE(store.database_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.lock_path.stat().st_mode) == 0o600
    with pytest.raises(PreviewReadOnlyError):
        store.revise_batch(
            expected_revision=1,
            idempotency_key="preview-write",
            changes=[change(current.profile.sections[0], "Do not write this.")],
            user_confirmed=False,
        )
    assert store.read().profile.revision == 1


def test_batch_revision_is_atomic_and_idempotent(tmp_path: Path) -> None:
    store = SenseStore(tmp_path / "Sense")
    store.initialize(profile())
    activate(store)
    original = store.read()
    working, writing = original.profile.sections
    changes = [
        change(working, "Use one final judgment."),
        change(writing, "Write the final result for its reader."),
    ]

    preview = store.preview_revise_batch(
        expected_revision=1,
        changes=changes,
        user_confirmed=False,
    )
    assert preview.proposed_profile.revision == 2
    assert store.read().profile.revision == 1

    result = store.revise_batch(
        expected_revision=1,
        idempotency_key="two-sections",
        changes=changes,
        user_confirmed=False,
    )
    replay = store.revise_batch(
        expected_revision=1,
        idempotency_key="two-sections",
        changes=changes,
        user_confirmed=False,
    )
    assert result["revision"] == 2
    assert replay["replayed"] is True
    assert [item.text for item in store.read().profile.sections] == [
        "Use one final judgment.",
        "Write the final result for its reader.",
    ]

    current = store.read()
    valid = change(current.profile.sections[0], "Must not be partially written.")
    invalid = SectionRevision(
        section_id="writing",
        previous_section_sha256="0" * 64,
        previous_understanding="Old writing guidance.",
        changed_future_judgment="New writing guidance.",
        new_section=current.profile.sections[1].model_copy(update={"text": "Conflict."}),
    )
    with pytest.raises(RevisionConflictError):
        store.revise_batch(
            expected_revision=2,
            idempotency_key="atomic-conflict",
            changes=[valid, invalid],
            user_confirmed=False,
        )
    assert store.read().profile == current.profile


def test_overview_hides_sensitive_content_and_source_locations() -> None:
    document = ProfileDocument(
        revision=1,
        sections=[
            section("working-together", "Read /Users/example/private.md."),
            section("private-life", "SECRET-MARKER", sensitive=True),
        ],
    )

    overview = guidance_overview(
        document,
        lifecycle="active",
        updated_at="2026-08-13T00:00:00Z",
    )
    serialized = str(overview)
    assert "SECRET-MARKER" not in serialized
    assert "/Users/example/private.md" not in serialized
    assert source_ref().sha256 not in serialized
    assert "[연결된 자료]" in serialized


def test_forget_removes_content_from_current_and_retained_revisions(tmp_path: Path) -> None:
    marker = "FORGET-ME-7f3e9832"
    store = SenseStore(tmp_path / "Sense")
    initial = ProfileDocument(
        revision=1,
        sections=[
            section("working-together", marker),
            section("writing", "Keep this section."),
        ],
    )
    store.initialize(initial)
    activate(store)
    current = store.read()
    store.revise_batch(
        expected_revision=1,
        idempotency_key="before-forget",
        changes=[change(current.profile.sections[0], f"{marker} revised")],
        user_confirmed=False,
    )

    replacement = section("working-together", "Use independent judgment.")
    preview = store.preview_forget(
        section_id="working-together",
        replacement_section=replacement,
    )
    current = store.read()
    store.forget(
        expected_revision=current.profile.revision,
        section_id="working-together",
        confirmation_digest=preview["confirmation_digest"],
        replacement_section=replacement,
        user_confirmed=True,
    )

    assert marker not in store.read().profile.model_dump_json()
    assert all(marker not in item.model_dump_json() for item in store.history())
    for name in ("sense.sqlite3", "sense.sqlite3-wal", "sense.sqlite3-shm"):
        path = store.data_root / name
        if path.exists():
            assert marker.encode() not in path.read_bytes()


def test_storage_rejects_a_symlinked_data_root(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    link = tmp_path / "Sense"
    link.symlink_to(target, target_is_directory=True)

    with pytest.raises(UnsafeStorageError, match="symbolic link"):
        SenseStore(link).initialize(profile())
    assert list(target.iterdir()) == []


def test_mcp_exposes_one_revision_path_and_reads_without_writing(tmp_path: Path) -> None:
    root = tmp_path / "Sense"
    SenseStore(root).initialize(profile())
    server = create_server(root)
    tools = {tool.name for tool in asyncio.run(server.list_tools())}

    assert tools == {
        "sense_read",
        "sense_overview",
        "sense_preview_revision",
        "sense_revise_batch",
        "sense_control",
        "sense_status",
    }
    result = asyncio.run(server.call_tool("sense_read", {"view": "index"}))
    assert result.structured_content["ok"] is True
    assert result.structured_content["result"]["revision"] == 1
    assert SenseStore(root).read().profile.revision == 1
