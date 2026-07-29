"""Metadata-only corpus discovery.

This module deliberately does not open files, compute content hashes, invoke
parsers, call Spotlight/Quick Look, or follow symbolic links.
"""

from __future__ import annotations

import hashlib
import os
import stat
import unicodedata
import uuid
from collections import Counter
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path

from .database import corpus_connection, encode_json, get_corpus, utc_now
from .errors import CorpusError
from .formats import classify
from .source_access import (
    open_directory_at,
    open_source_root,
    opened_current_source_root,
    source_root_identity,
)

SF_DATALESS = getattr(stat, "SF_DATALESS", 0x40000000)
SCAN_INVENTORY_DELTA_MAX_DOCUMENTS = 500
_INVENTORY_CHANGE_TYPE_ORDER = (
    "added",
    "reappeared",
    "metadata_changed",
    "residency_changed",
    "eligibility_changed",
    "deleted",
)
_OBSERVATION_CHANGE_TYPES = {
    "metadata_changed",
    "residency_changed",
    "eligibility_changed",
}


class _OwnedDescriptor:
    def __init__(self, descriptor: int) -> None:
        self.descriptor = descriptor

    def close(self) -> None:
        if self.descriptor >= 0:
            os.close(self.descriptor)
            self.descriptor = -1

    def __enter__(self) -> _OwnedDescriptor:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


@dataclass
class ScanSummary:
    corpus_id: str
    scan_id: str
    source_root: str
    directories: int = 0
    files: int = 0
    dataless_files: int = 0
    logical_bytes: int = 0
    allocated_bytes: int = 0
    symlinks_skipped: int = 0
    special_files_skipped: int = 0
    stat_failures: int = 0
    excluded_directories: int = 0
    deleted_since_previous_scan: int = 0
    counts_by_extension: Counter = field(default_factory=Counter)
    dataless_by_extension: Counter = field(default_factory=Counter)
    eligibility_counts: Counter = field(default_factory=Counter)
    change_counts: Counter = field(default_factory=Counter)
    changed_documents: int = 0

    def as_dict(self) -> dict:
        return {
            "corpus_id": self.corpus_id,
            "scan_id": self.scan_id,
            "source_root": self.source_root,
            "mode": "metadata_only",
            "directories": self.directories,
            "files": self.files,
            "dataless_files": self.dataless_files,
            "logical_bytes": self.logical_bytes,
            "allocated_bytes": self.allocated_bytes,
            "symlinks_skipped": self.symlinks_skipped,
            "special_files_skipped": self.special_files_skipped,
            "stat_failures": self.stat_failures,
            "excluded_directories": self.excluded_directories,
            "completeness_failure_count": self.stat_failures,
            "observation_complete": self.stat_failures == 0,
            "deleted_since_previous_scan": self.deleted_since_previous_scan,
            "counts_by_extension": dict(sorted(self.counts_by_extension.items())),
            "dataless_by_extension": dict(sorted(self.dataless_by_extension.items())),
            "eligibility_counts": dict(sorted(self.eligibility_counts.items())),
            "change_counts": {
                change_type: int(self.change_counts[change_type])
                for change_type in _INVENTORY_CHANGE_TYPE_ORDER
            },
            "changed_documents": self.changed_documents,
        }


def stable_document_id(corpus_id: str, relative_path_nfc: str) -> str:
    digest = hashlib.sha256(
        # The legacy domain is part of persisted document IDs.
        f"work-corpus-document-v1\0{corpus_id}\0{relative_path_nfc}".encode()
    ).hexdigest()
    return f"doc_{digest[:32]}"


def _scan_issue_locator(path: Path, source_root: Path) -> dict[str, str]:
    try:
        relative_path = path.relative_to(source_root).as_posix()
    except ValueError:
        relative_path = "__unlocated__"
    return {
        "relative_path": unicodedata.normalize("NFC", relative_path or "."),
    }


def _scan_issue_locator_from_relative(relative_path: str) -> dict[str, str]:
    return {
        "relative_path": unicodedata.normalize("NFC", relative_path or "."),
    }


def _entry_stat(directory_descriptor: int, name: str) -> os.stat_result:
    return os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)


def _record_scan_issue(
    connection,
    *,
    scan_id: str,
    code: str,
    message: str,
    details: dict,
    structural_locator: dict[str, str],
) -> None:
    locator_key = hashlib.sha256(
        encode_json(
            {
                "code": code,
                "structural_locator": structural_locator,
            }
        ).encode()
    ).hexdigest()
    connection.execute(
        """
        INSERT INTO extraction_issues(
            issue_id, scan_id, stage, severity, code, message, details_json,
            structural_locator_json, locator_key, created_at
        ) VALUES (?, ?, 'scan', 'warning', ?, ?, ?, ?, ?, ?)
        """,
        (
            f"issue_{uuid.uuid4().hex}",
            scan_id,
            code,
            message,
            encode_json(details),
            encode_json(structural_locator),
            locator_key,
            utc_now(),
        ),
    )


def scan_corpus(data_root: Path, corpus_id: str) -> dict:
    corpus = get_corpus(data_root, corpus_id)
    source_root = Path(corpus["source_root"])
    source_scope = corpus["source_scope"]
    excluded_directory_names = set(source_scope["exclude_directory_names"])
    excluded_path_prefixes = tuple(source_scope["exclude_path_prefixes"])
    scan_id = f"scan_{uuid.uuid4().hex}"
    started_at = utc_now()
    summary = ScanSummary(
        corpus_id=corpus_id,
        scan_id=scan_id,
        source_root=str(source_root),
    )
    inventory_changes: dict[str, set[str]] = {}

    with (
        corpus_connection(data_root, corpus_id) as connection,
        ExitStack() as open_directories,
    ):
        connection.execute(
            "INSERT INTO scan_runs(scan_id, started_at, status) VALUES (?, ?, 'running')",
            (scan_id, started_at),
        )
        connection.execute("SAVEPOINT scan_inventory")
        initial_source_root_identity: tuple[int, int] | None = None

        try:
            root_descriptor = open_source_root(source_root)
        except CorpusError as exc:
            summary.stat_failures += 1
            _record_scan_issue(
                connection,
                scan_id=scan_id,
                code="source_root_open_failed",
                message="Could not securely open the registered source root.",
                details={"error": str(exc)},
                structural_locator=_scan_issue_locator_from_relative("."),
            )
            stack: list[tuple[_OwnedDescriptor, tuple[str, ...]]] = []
        else:
            initial_source_root_identity = source_root_identity(root_descriptor)
            root_owner = open_directories.enter_context(
                _OwnedDescriptor(root_descriptor)
            )
            stack = [(root_owner, ())]

        while stack:
            directory_owner, directory_parts = stack.pop()
            directory_descriptor = directory_owner.descriptor
            directory_relative = "/".join(directory_parts) or "."
            directory_path = source_root.joinpath(*directory_parts)
            summary.directories += 1
            try:
                with os.scandir(directory_descriptor) as iterator:
                    entries = list(iterator)
            except OSError as exc:
                summary.stat_failures += 1
                _record_scan_issue(
                    connection,
                    scan_id=scan_id,
                    code="directory_scan_failed",
                    message="Could not enumerate a directory.",
                    details={"path": str(directory_path), "error": str(exc)},
                    structural_locator=_scan_issue_locator_from_relative(
                        directory_relative
                    ),
                )
                directory_owner.close()
                continue

            for entry in entries:
                entry_parts = (*directory_parts, entry.name)
                relative_path = "/".join(entry_parts)
                entry_path = source_root.joinpath(*entry_parts)
                try:
                    entry_stat = _entry_stat(directory_descriptor, entry.name)
                except OSError as exc:
                    summary.stat_failures += 1
                    _record_scan_issue(
                        connection,
                        scan_id=scan_id,
                        code="stat_failed",
                        message="Could not read filesystem metadata.",
                        details={"path": str(entry_path), "error": str(exc)},
                        structural_locator=_scan_issue_locator_from_relative(
                            relative_path
                        ),
                    )
                    continue

                if stat.S_ISLNK(entry_stat.st_mode):
                    summary.symlinks_skipped += 1
                    _record_scan_issue(
                        connection,
                        scan_id=scan_id,
                        code="symlink_skipped",
                        message="Symbolic links are not followed.",
                        details={"path": str(entry_path)},
                        structural_locator=_scan_issue_locator_from_relative(
                            relative_path
                        ),
                    )
                    continue
                if stat.S_ISDIR(entry_stat.st_mode):
                    relative_path_nfc = unicodedata.normalize("NFC", relative_path)
                    entry_name_nfc = unicodedata.normalize("NFC", entry.name)
                    excluded_by_prefix = any(
                        relative_path_nfc == prefix
                        or relative_path_nfc.startswith(f"{prefix}/")
                        for prefix in excluded_path_prefixes
                    )
                    if (
                        entry_name_nfc in excluded_directory_names
                        or excluded_by_prefix
                    ):
                        summary.excluded_directories += 1
                        continue
                    try:
                        child_descriptor = open_directory_at(
                            directory_descriptor,
                            entry.name,
                            relative_path=relative_path,
                            expected=entry_stat,
                        )
                    except CorpusError as exc:
                        summary.stat_failures += 1
                        _record_scan_issue(
                            connection,
                            scan_id=scan_id,
                            code="directory_changed_during_scan",
                            message="A source directory changed while it was being opened.",
                            details={"path": str(entry_path), "error": str(exc)},
                            structural_locator=_scan_issue_locator_from_relative(
                                relative_path
                            ),
                        )
                        continue
                    child_owner = open_directories.enter_context(
                        _OwnedDescriptor(child_descriptor)
                    )
                    stack.append((child_owner, entry_parts))
                    continue
                if not stat.S_ISREG(entry_stat.st_mode):
                    summary.special_files_skipped += 1
                    _record_scan_issue(
                        connection,
                        scan_id=scan_id,
                        code="special_file_skipped",
                        message="Only regular files are indexed.",
                        details={"path": str(entry_path), "mode": entry_stat.st_mode},
                        structural_locator=_scan_issue_locator_from_relative(
                            relative_path
                        ),
                    )
                    continue

                relative_path_nfc = unicodedata.normalize("NFC", relative_path)
                extension, media_type, adapter, eligibility = classify(entry.name)
                flags = int(getattr(entry_stat, "st_flags", 0))
                is_dataless = bool(flags & SF_DATALESS)
                residency_state = "remote_only" if is_dataless else "resident"
                allocated_size = int(getattr(entry_stat, "st_blocks", 0) * 512)
                document_id = stable_document_id(corpus_id, relative_path_nfc)
                now = utc_now()

                previous = connection.execute(
                    """
                    SELECT logical_size, modified_ns, changed_ns, device, inode,
                           current_revision_id, deleted_at, residency_state,
                           eligibility_state
                    FROM documents WHERE document_id = ?
                    """,
                    (document_id,),
                ).fetchone()
                metadata_changed = bool(
                    previous
                    and (
                        previous["logical_size"] != entry_stat.st_size
                        or previous["modified_ns"] != entry_stat.st_mtime_ns
                        or previous["changed_ns"] != entry_stat.st_ctime_ns
                        or previous["device"] != entry_stat.st_dev
                        or previous["inode"] != entry_stat.st_ino
                    )
                )
                change_types = inventory_changes.setdefault(document_id, set())
                if previous is None:
                    change_types.add("added")
                else:
                    if previous["deleted_at"] is not None:
                        change_types.add("reappeared")
                    if metadata_changed:
                        change_types.add("metadata_changed")
                    if previous["residency_state"] != residency_state:
                        change_types.add("residency_changed")
                    if previous["eligibility_state"] != eligibility:
                        change_types.add("eligibility_changed")
                if not change_types:
                    inventory_changes.pop(document_id)
                if metadata_changed and previous["current_revision_id"]:
                    connection.execute(
                        """
                        UPDATE interpretation_queue
                        SET state = 'stale', reason = 'source_metadata_changed', updated_at = ?
                        WHERE revision_id = ? AND state != 'stale'
                        """,
                        (now, previous["current_revision_id"]),
                    )
                    connection.execute(
                        """
                        UPDATE atomic_claims
                        SET dependency_state = 'stale'
                        WHERE claim_id IN (
                            SELECT claim_id FROM evidence_links
                            WHERE source_revision_id = ?
                        )
                          AND dependency_state = 'valid'
                        """,
                        (previous["current_revision_id"],),
                    )

                connection.execute(
                    """
                    INSERT INTO documents(
                        document_id, relative_path, relative_path_nfc, absolute_path,
                        extension, media_type, adapter, logical_size, allocated_size,
                        modified_ns, changed_ns, device, inode, mode, flags, is_dataless,
                        residency_state, eligibility_state, last_seen_scan_id,
                        first_seen_at, last_seen_at, deleted_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                    ON CONFLICT(document_id) DO UPDATE SET
                        relative_path = excluded.relative_path,
                        relative_path_nfc = excluded.relative_path_nfc,
                        absolute_path = excluded.absolute_path,
                        extension = excluded.extension,
                        media_type = excluded.media_type,
                        adapter = excluded.adapter,
                        logical_size = excluded.logical_size,
                        allocated_size = excluded.allocated_size,
                        modified_ns = excluded.modified_ns,
                        changed_ns = excluded.changed_ns,
                        device = excluded.device,
                        inode = excluded.inode,
                        mode = excluded.mode,
                        flags = excluded.flags,
                        is_dataless = excluded.is_dataless,
                        residency_state = excluded.residency_state,
                        eligibility_state = excluded.eligibility_state,
                        last_seen_scan_id = excluded.last_seen_scan_id,
                        last_seen_at = excluded.last_seen_at,
                        deleted_at = NULL
                    """,
                    (
                        document_id,
                        relative_path,
                        relative_path_nfc,
                        str(entry_path),
                        extension,
                        media_type,
                        adapter,
                        entry_stat.st_size,
                        allocated_size,
                        entry_stat.st_mtime_ns,
                        entry_stat.st_ctime_ns,
                        entry_stat.st_dev,
                        entry_stat.st_ino,
                        entry_stat.st_mode,
                        flags,
                        int(is_dataless),
                        residency_state,
                        eligibility,
                        scan_id,
                        now,
                        now,
                    ),
                )

                summary.files += 1
                summary.logical_bytes += entry_stat.st_size
                summary.allocated_bytes += allocated_size
                summary.counts_by_extension[extension or "(none)"] += 1
                summary.eligibility_counts[eligibility] += 1
                if is_dataless:
                    summary.dataless_files += 1
                    summary.dataless_by_extension[extension or "(none)"] += 1
            directory_owner.close()

        source_root_error: OSError | CorpusError | None = None
        if initial_source_root_identity is not None:
            try:
                with opened_current_source_root(
                    source_root,
                    initial_source_root_identity,
                ):
                    pass
            except (OSError, CorpusError) as exc:
                source_root_error = exc

        if source_root_error is not None:
            connection.execute("ROLLBACK TO scan_inventory")
            connection.execute("RELEASE scan_inventory")
            inventory_changes.clear()
            summary.stat_failures += 1
            _record_scan_issue(
                connection,
                scan_id=scan_id,
                code="source_root_changed_during_scan",
                message="The registered source root changed during the scan.",
                details={
                    "source_root": str(source_root),
                    "error": str(source_root_error),
                },
                structural_locator=_scan_issue_locator_from_relative("."),
            )
        else:
            connection.execute("RELEASE scan_inventory")

        completed_at = utc_now()
        if summary.stat_failures:
            # A transient File Provider or directory-enumeration failure makes absence
            # unobservable. Preserve the previous live set rather than interpreting an
            # incomplete scan as deletion.
            deleted_rows = []
            deleted = 0
        else:
            deleted_rows = connection.execute(
                """
                SELECT document_id, current_revision_id
                FROM documents
                WHERE last_seen_scan_id != ? AND deleted_at IS NULL
                """,
                (scan_id,),
            ).fetchall()
            deleted = connection.execute(
                """
                UPDATE documents
                SET deleted_at = ?
                WHERE last_seen_scan_id != ? AND deleted_at IS NULL
                """,
                (completed_at, scan_id),
            ).rowcount
            for row in deleted_rows:
                inventory_changes.setdefault(row["document_id"], set()).add("deleted")
        deleted_revision_ids = [
            row["current_revision_id"] for row in deleted_rows if row["current_revision_id"]
        ]
        if deleted_revision_ids:
            placeholders = ",".join("?" for _ in deleted_revision_ids)
            connection.execute(
                f"""
                UPDATE interpretation_queue
                SET state = 'stale', reason = 'source_deleted', updated_at = ?
                WHERE revision_id IN ({placeholders}) AND state != 'stale'
                """,
                (completed_at, *deleted_revision_ids),
            )
            connection.execute(
                f"""
                UPDATE atomic_claims
                SET dependency_state = 'stale'
                WHERE claim_id IN (
                    SELECT claim_id FROM evidence_links
                    WHERE source_revision_id IN ({placeholders})
                )
                  AND dependency_state = 'valid'
                """,
                deleted_revision_ids,
            )
        summary.deleted_since_previous_scan = deleted
        issue_count = (
            summary.symlinks_skipped + summary.special_files_skipped + summary.stat_failures
        )
        scan_status = "complete" if summary.stat_failures == 0 else "incomplete"
        root_entry_count = sum(
            1
            for path in connection.execute("SELECT relative_path FROM documents")
            if "/" not in path["relative_path"]
        )
        connection.execute(
            """
            UPDATE scan_runs SET
                completed_at = ?, root_entry_count = ?, directory_count = ?,
                file_count = ?, dataless_count = ?, logical_bytes = ?,
                allocated_bytes = ?, issue_count = ?, status = ?
            WHERE scan_id = ?
            """,
            (
                completed_at,
                root_entry_count,
                summary.directories,
                summary.files,
                summary.dataless_files,
                summary.logical_bytes,
                summary.allocated_bytes,
                issue_count,
                scan_status,
                scan_id,
            ),
        )
        for change_types in inventory_changes.values():
            summary.change_counts.update(change_types)
            if change_types.intersection(_OBSERVATION_CHANGE_TYPES):
                summary.changed_documents += 1
        ordered_changes = [
            {
                "document_id": document_id,
                "change_types": [
                    change_type
                    for change_type in _INVENTORY_CHANGE_TYPE_ORDER
                    if change_type in inventory_changes[document_id]
                ],
            }
            for document_id in sorted(inventory_changes)
        ]
        bounded_changes = ordered_changes[:SCAN_INVENTORY_DELTA_MAX_DOCUMENTS]
        connection.execute(
            """
            INSERT INTO events(event_id, event_type, payload_json, created_at)
            VALUES (?, 'scan_inventory_delta', ?, ?)
            """,
            (
                f"event_{uuid.uuid4().hex}",
                encode_json(
                    {
                        "schema_version": 1,
                        "scan_id": scan_id,
                        "inventory_complete": scan_status == "complete",
                        "count": len(ordered_changes),
                        "change_counts": {
                            change_type: int(summary.change_counts[change_type])
                            for change_type in _INVENTORY_CHANGE_TYPE_ORDER
                        },
                        "changed_documents": summary.changed_documents,
                        "truncated": len(bounded_changes) < len(ordered_changes),
                        "changes": bounded_changes,
                    }
                ),
                completed_at,
            ),
        )

    return summary.as_dict()
