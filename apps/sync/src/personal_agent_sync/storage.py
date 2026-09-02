"""Explicit remote Corpus storage reporting and conservative maintenance."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from .config import SyncConfig
from .credentials import read_token
from .errors import SyncError
from .migration import LocalCorpusMigration
from .remote import RemoteClient


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SyncError("remote_protocol_error", f"remote {name} is invalid")
    return value


async def remote_storage_report(
    config: SyncConfig,
    *,
    hotspot_limit: int = 10,
) -> dict[str, Any]:
    """Read current shard sizes and explicit logical-storage diagnostics."""

    if not 0 <= hotspot_limit <= 20:
        raise SyncError(
            "invalid_storage_request", "hotspot limit must be between 0 and 20"
        )
    remote = RemoteClient(config, read_token(config.device_id))
    corpus = LocalCorpusMigration(config)
    corpora: list[dict[str, Any]] = []
    try:
        for corpus_id in corpus.corpus_ids():
            inventory = await remote.inventory(
                corpus_id,
                limit=1,
                include_storage_details=True,
                hotspot_limit=hotspot_limit,
            )
            storage = inventory.get("storage")
            details = inventory.get("storage_details")
            if not isinstance(storage, dict) or not isinstance(details, dict):
                raise SyncError(
                    "remote_protocol_error",
                    "remote Corpus storage report is invalid",
                )
            size = _integer(storage.get("database_size_bytes"), "database size")
            corpora.append(
                {
                    "corpus_id": corpus_id,
                    "database_size_bytes": size,
                    "counts": inventory.get("counts"),
                    "external": inventory.get("external"),
                    "staged_upload_count": inventory.get("staged_upload_count"),
                    "storage": storage,
                    "storage_details": details,
                }
            )
    finally:
        await remote.close()
    corpora.sort(key=lambda value: value["database_size_bytes"], reverse=True)
    return {
        "observed_at": datetime.now(UTC).isoformat(),
        "database_size_bytes": sum(
            int(value["database_size_bytes"]) for value in corpora
        ),
        "corpus_count": len(corpora),
        "corpora": corpora,
    }


async def maintain_remote_storage(
    config: SyncConfig,
    *,
    staged_min_age_hours: float = 24,
    compact_search_index: bool = True,
    compaction_batch_size: int = 10,
    maximum_batches_per_corpus: int = 1_000,
) -> dict[str, Any]:
    """Remove abandoned staging and compact only the derived search index.

    Canonical documents, revisions, projections, units, and Context-linked
    records are not selected here. Their retention continues to follow the
    local Corpus snapshot and the existing protected-record checks.
    """

    if staged_min_age_hours < 0:
        raise SyncError(
            "invalid_storage_request", "staged upload age must not be negative"
        )
    if not 1 <= compaction_batch_size <= 10:
        raise SyncError(
            "invalid_storage_request",
            "search-index batch size must be between 1 and 10",
        )
    if not 1 <= maximum_batches_per_corpus <= 10_000:
        raise SyncError(
            "invalid_storage_request",
            "maximum batch count is outside the supported range",
        )

    remote = RemoteClient(config, read_token(config.device_id))
    corpus = LocalCorpusMigration(config)
    cutoff = datetime.now(UTC) - timedelta(hours=staged_min_age_hours)
    summaries: list[dict[str, Any]] = []
    try:
        for corpus_id in corpus.corpus_ids():
            removed_uploads = 0
            while True:
                inventory = await remote.inventory(corpus_id, limit=1)
                staged = inventory.get("staged_uploads")
                if not isinstance(staged, list):
                    raise SyncError(
                        "remote_protocol_error", "remote staged uploads are invalid"
                    )
                stale_upload_ids = sorted(
                    str(item["upload_id"])
                    for item in staged
                    if isinstance(item, dict)
                    and isinstance(item.get("upload_id"), str)
                    and (
                        _timestamp(item.get("created_at"))
                        or datetime.max.replace(tzinfo=UTC)
                    )
                    <= cutoff
                )
                if not stale_upload_ids:
                    break
                for start in range(0, len(stale_upload_ids), 10):
                    result = await remote.maintain_corpus(
                        corpus_id,
                        remove_projection_ids=[],
                        remove_document_ids=[],
                        remove_upload_ids=stale_upload_ids[start : start + 10],
                    )
                    removed = result.get("removed")
                    if not isinstance(removed, dict):
                        raise SyncError(
                            "remote_protocol_error",
                            "remote Corpus maintenance result is invalid",
                        )
                    removed_uploads += _integer(
                        removed.get("uploads"), "removed upload count"
                    )
                if inventory.get("staged_uploads_truncated") is not True:
                    break

            processed = 0
            removed_index_rows = 0
            pending = _integer(
                (inventory.get("storage") or {}).get("search_index_pending_projections")
                if isinstance(inventory.get("storage"), dict)
                else None,
                "pending search-index projection count",
            )
            batches = 0
            while compact_search_index and pending > 0:
                if batches >= maximum_batches_per_corpus:
                    raise SyncError(
                        "storage_maintenance_incomplete",
                        "search-index maintenance exceeded its batch budget",
                    )
                result = await remote.maintain_corpus(
                    corpus_id,
                    remove_projection_ids=[],
                    remove_document_ids=[],
                    remove_upload_ids=[],
                    compact_search_index_limit=compaction_batch_size,
                )
                search = result.get("search_index")
                if not isinstance(search, dict):
                    raise SyncError(
                        "remote_protocol_error",
                        "remote search-index maintenance result is invalid",
                    )
                batch_processed = _integer(
                    search.get("processed_projections"),
                    "processed search-index projection count",
                )
                next_pending = _integer(
                    search.get("pending_projections"),
                    "pending search-index projection count",
                )
                if batch_processed == 0 and next_pending > 0:
                    raise SyncError(
                        "storage_maintenance_stalled",
                        "search-index maintenance made no progress",
                    )
                processed += batch_processed
                removed_index_rows += _integer(
                    search.get("removed_structural_only_rows"),
                    "removed search-index row count",
                )
                pending = next_pending
                batches += 1

            final = await remote.inventory(
                corpus_id,
                limit=1,
                include_storage_details=True,
                hotspot_limit=0,
            )
            summaries.append(
                {
                    "corpus_id": corpus_id,
                    "removed_staged_uploads": removed_uploads,
                    "processed_search_index_projections": processed,
                    "removed_structural_only_index_rows": removed_index_rows,
                    "pending_search_index_projections": pending,
                    "storage": final.get("storage"),
                    "storage_details": final.get("storage_details"),
                }
            )
    finally:
        await remote.close()
    return {
        "observed_at": datetime.now(UTC).isoformat(),
        "staged_min_age_hours": staged_min_age_hours,
        "canonical_records_removed": 0,
        "corpora": summaries,
    }
