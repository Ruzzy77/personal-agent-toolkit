"""Canonical current-context transfer for the trusted remote control plane.

The bundle contains user-authored application state verbatim, but never the
Corpus-owned context database, source-root field, provider/session record,
approval history, or local snapshot id.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from collections import defaultdict
from collections.abc import Callable, Mapping
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .adapter_registry import AdapterRegistry
from .config import (
    ensure_private_directory_at,
    normalize_corpus_id,
    open_private_file_at,
    private_directory,
)
from .contexts import (
    CONTEXT_DISCLOSURE_STATES,
    CONTEXT_ITEM_KINDS,
    CONTEXT_MAX_ATTRIBUTES_BYTES,
    CONTEXT_MAX_BODY_CHARS,
    CONTEXT_MAX_CORPORA,
    CONTEXT_MAX_IDENTIFIER_CHARS,
    CONTEXT_MAX_PURPOSE_CHARS,
    CONTEXT_MAX_SCOPE_BYTES,
    CONTEXT_MAX_TITLE_CHARS,
    CONTEXT_SOURCE_ROLES,
    GENERAL_CANDIDATE_KINDS,
    normalize_context_id,
)
from .database import (
    context_connection,
    context_read_connection,
    corpus_read_connection,
    encode_json,
    get_corpus,
    utc_now,
)
from .errors import CorpusError
from .locking import context_reader_lock, context_writer_lock
from .remote_deletion_state import require_no_remote_delete_intent
from .remote_source_sync import (
    build_source_sync_manifest,
    canonical_source_sync_manifest,
    read_coordinated_source_sync_head,
)

CONTEXT_MIGRATION_FORMAT = "corpus-current-context-migration-v1"
CONTEXT_MIGRATION_RECEIPT_FORMAT = "corpus-context-migration-receipt-v1"
CONTEXT_MIGRATION_MAX_BYTES = 2 * 1024 * 1024
CONTEXT_MIGRATION_MAX_CONTEXTS = 100
CONTEXT_MIGRATION_MAX_CORPORA = 100
CONTEXT_MIGRATION_MAX_ITEMS = 2_000
CONTEXT_MIGRATION_MAX_ITEMS_PER_CONTEXT = 500
CONTEXT_MIGRATION_MAX_SOURCES = 10_000
CONTEXT_MIGRATION_MAX_RECEIPTS = 256
CONTEXT_MIGRATION_RECEIPT_MAX_BYTES = 8 * 1024

_RECEIPT_DIRECTORY_NAME = "context-migration-receipts"
_HEX_CHARACTERS = frozenset("0123456789abcdef")

SourceHeadReader = Callable[[str], Mapping[str, Any]]


class ContextMigrationError(CorpusError):
    code = "context_migration_error"


class ContextMigrationValidationError(ContextMigrationError):
    code = "context_migration_validation_error"


class ContextMigrationConflictError(ContextMigrationError):
    code = "context_migration_conflict"


class ContextMigrationTargetNotEmptyError(ContextMigrationError):
    code = "context_migration_target_not_empty"


class ContextMigrationIdempotencyError(ContextMigrationError):
    code = "context_migration_idempotency_conflict"


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    except (RecursionError, TypeError, ValueError) as exc:
        raise ContextMigrationValidationError(
            "context migration data must be finite canonical JSON"
        ) from exc


def _digest(value: object) -> str:
    payload = b"corpus-context-migration-v1\0" + _canonical_bytes(value)
    return hashlib.sha256(payload).hexdigest()


def _string(value: object, *, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ContextMigrationValidationError(
            "context migration field must be a string",
            details={"field": field},
        )
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        raise ContextMigrationValidationError(
            "context migration string is empty or too long",
            details={"field": field, "maximum_chars": maximum},
        )
    return normalized


def _sha256(value: object, *, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in _HEX_CHARACTERS for character in value)
    ):
        raise ContextMigrationValidationError(
            "context migration digest must be lowercase sha256",
            details={"field": field},
        )
    return value


def _timestamp(value: object, *, field: str) -> str:
    normalized = _string(value, field=field, maximum=100)
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContextMigrationValidationError(
            "context migration timestamp is invalid",
            details={"field": field},
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContextMigrationValidationError(
            "context migration timestamp requires a timezone",
            details={"field": field},
        )
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _json_object(value: object, *, field: str, maximum_bytes: int) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ContextMigrationValidationError(
            "context migration JSON field must be an object",
            details={"field": field},
        )
    payload = _canonical_bytes(value)
    if len(payload) > maximum_bytes:
        raise ContextMigrationValidationError(
            "context migration JSON field exceeds its byte limit",
            details={"field": field, "maximum_bytes": maximum_bytes},
        )
    return json.loads(payload)


def _canonical_source(value: object, *, context_corpora: set[str]) -> dict[str, str]:
    required = {
        "corpus_id",
        "document_id",
        "revision_id",
        "projection_id",
        "source_unit_id",
        "link_role",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ContextMigrationValidationError(
            "context migration source fields are invalid",
            details={"required": sorted(required)},
        )
    corpus_id = normalize_corpus_id(
        _string(
            value["corpus_id"],
            field="source.corpus_id",
            maximum=64,
        )
    )
    if corpus_id not in context_corpora:
        raise ContextMigrationValidationError(
            "context migration source is outside its context corpora",
            details={"corpus_id": corpus_id},
        )
    source = {
        "corpus_id": corpus_id,
        "document_id": _string(
            value["document_id"],
            field="source.document_id",
            maximum=CONTEXT_MAX_IDENTIFIER_CHARS,
        ),
        "revision_id": _string(
            value["revision_id"],
            field="source.revision_id",
            maximum=CONTEXT_MAX_IDENTIFIER_CHARS,
        ),
        "projection_id": _string(
            value["projection_id"],
            field="source.projection_id",
            maximum=CONTEXT_MAX_IDENTIFIER_CHARS,
        ),
        "source_unit_id": _string(
            value["source_unit_id"],
            field="source.source_unit_id",
            maximum=CONTEXT_MAX_IDENTIFIER_CHARS,
        ),
        "link_role": _string(value["link_role"], field="source.link_role", maximum=32),
    }
    if source["link_role"] not in CONTEXT_SOURCE_ROLES:
        raise ContextMigrationValidationError("context migration source role is invalid")
    return source


def _canonical_item(value: object, *, context_corpora: set[str]) -> dict[str, Any]:
    required = {
        "item_id",
        "client_ref",
        "kind",
        "body_text",
        "attributes",
        "disclosure_state",
        "created_at",
        "sources",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ContextMigrationValidationError(
            "context migration item fields are invalid",
            details={"required": sorted(required)},
        )
    kind = _string(value["kind"], field="item.kind", maximum=32)
    if kind not in CONTEXT_ITEM_KINDS:
        raise ContextMigrationValidationError("context migration item kind is invalid")
    disclosure = _string(
        value["disclosure_state"],
        field="item.disclosure_state",
        maximum=32,
    )
    if disclosure not in CONTEXT_DISCLOSURE_STATES:
        raise ContextMigrationValidationError(
            "context migration disclosure state is invalid"
        )
    raw_sources = value["sources"]
    if not isinstance(raw_sources, list):
        raise ContextMigrationValidationError("context migration sources must be a list")
    sources = sorted(
        (_canonical_source(source, context_corpora=context_corpora) for source in raw_sources),
        key=lambda source: (
            source["corpus_id"],
            source["document_id"],
            source["revision_id"],
            source["projection_id"],
            source["source_unit_id"],
            source["link_role"],
        ),
    )
    source_keys = [(source["corpus_id"], source["source_unit_id"]) for source in sources]
    if len(source_keys) != len(set(source_keys)):
        raise ContextMigrationValidationError(
            "context migration item contains duplicate source units"
        )
    if kind in {"finding", "relationship", "difference"} and not any(
        source["link_role"] == "direct" for source in sources
    ):
        raise ContextMigrationValidationError(
            "source-linked context migration item requires a direct source"
        )
    if disclosure == "general_candidate" and (
        kind not in GENERAL_CANDIDATE_KINDS
        or not sources
        or any(source["link_role"] != "direct" for source in sources)
    ):
        raise ContextMigrationValidationError(
            "general candidate must use only direct indexed sources",
            details={"reason": "general_candidate_invalid"},
        )
    return {
        "item_id": _string(
            value["item_id"],
            field="item.item_id",
            maximum=CONTEXT_MAX_IDENTIFIER_CHARS,
        ),
        "client_ref": _string(
            value["client_ref"],
            field="item.client_ref",
            maximum=CONTEXT_MAX_IDENTIFIER_CHARS,
        ),
        "kind": kind,
        "body_text": _string(
            value["body_text"],
            field="item.body_text",
            maximum=CONTEXT_MAX_BODY_CHARS,
        ),
        "attributes": _json_object(
            value["attributes"],
            field="item.attributes",
            maximum_bytes=CONTEXT_MAX_ATTRIBUTES_BYTES,
        ),
        "disclosure_state": disclosure,
        "created_at": _timestamp(value["created_at"], field="item.created_at"),
        "sources": sources,
    }


def _canonical_context(value: object, *, known_corpora: set[str]) -> dict[str, Any]:
    required = {
        "context_id",
        "title",
        "purpose",
        "scope",
        "corpus_ids",
        "state",
        "version",
        "created_at",
        "updated_at",
        "items",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ContextMigrationValidationError(
            "context migration context fields are invalid",
            details={"required": sorted(required)},
        )
    raw_corpora = value["corpus_ids"]
    if (
        not isinstance(raw_corpora, list)
        or not 1 <= len(raw_corpora) <= CONTEXT_MAX_CORPORA
        or any(not isinstance(corpus_id, str) for corpus_id in raw_corpora)
    ):
        raise ContextMigrationValidationError(
            "context migration corpus_ids must be a bounded non-empty list"
        )
    corpus_ids = sorted(normalize_corpus_id(corpus_id) for corpus_id in raw_corpora)
    if len(corpus_ids) != len(set(corpus_ids)) or not set(corpus_ids) <= known_corpora:
        raise ContextMigrationValidationError(
            "context migration corpus_ids are duplicated or undeclared"
        )
    state = _string(value["state"], field="context.state", maximum=32)
    if state not in {"active", "archived"}:
        raise ContextMigrationValidationError("context migration state is invalid")
    version = value["version"]
    if type(version) is not int or not 1 <= version <= (1 << 63) - 1:
        raise ContextMigrationValidationError("context migration version is invalid")
    raw_items = value["items"]
    if not isinstance(raw_items, list) or len(raw_items) > CONTEXT_MIGRATION_MAX_ITEMS_PER_CONTEXT:
        raise ContextMigrationValidationError(
            "context migration item count exceeds its per-context limit"
        )
    items = sorted(
        (
            _canonical_item(item, context_corpora=set(corpus_ids))
            for item in raw_items
        ),
        key=lambda item: item["item_id"],
    )
    item_ids = [item["item_id"] for item in items]
    client_refs = [item["client_ref"] for item in items]
    if len(item_ids) != len(set(item_ids)) or len(client_refs) != len(set(client_refs)):
        raise ContextMigrationValidationError(
            "context migration item identities must be unique"
        )
    created_at = _timestamp(value["created_at"], field="context.created_at")
    updated_at = _timestamp(value["updated_at"], field="context.updated_at")
    if created_at > updated_at or any(item["created_at"] > updated_at for item in items):
        raise ContextMigrationValidationError(
            "context migration timestamps are out of order"
        )
    return {
        "context_id": normalize_context_id(
            _string(value["context_id"], field="context.context_id", maximum=64)
        ),
        "title": _string(value["title"], field="context.title", maximum=CONTEXT_MAX_TITLE_CHARS),
        "purpose": _string(
            value["purpose"],
            field="context.purpose",
            maximum=CONTEXT_MAX_PURPOSE_CHARS,
        ),
        "scope": _json_object(
            value["scope"],
            field="context.scope",
            maximum_bytes=CONTEXT_MAX_SCOPE_BYTES,
        ),
        "corpus_ids": corpus_ids,
        "state": state,
        "version": version,
        "created_at": created_at,
        "updated_at": updated_at,
        "items": items,
    }


def canonical_context_migration_bundle(value: object) -> tuple[dict[str, Any], str]:
    if not isinstance(value, dict):
        raise ContextMigrationValidationError("context migration bundle must be an object")
    required = {"format", "corpora", "contexts", "bundle_sha256"}
    if set(value) != required or value.get("format") != CONTEXT_MIGRATION_FORMAT:
        raise ContextMigrationValidationError(
            "context migration bundle fields or format are invalid"
        )
    raw_corpora = value.get("corpora")
    if (
        not isinstance(raw_corpora, list)
        or not 1 <= len(raw_corpora) <= CONTEXT_MIGRATION_MAX_CORPORA
    ):
        raise ContextMigrationValidationError(
            "context migration corpus count is outside its limit"
        )
    corpora = []
    for entry in raw_corpora:
        if not isinstance(entry, dict) or set(entry) != {
            "corpus_id",
            "source_manifest_sha256",
        }:
            raise ContextMigrationValidationError(
                "context migration corpus descriptor is invalid"
            )
        digest = _sha256(
            entry.get("source_manifest_sha256"),
            field="source_manifest_sha256",
        )
        corpora.append(
            {
                "corpus_id": normalize_corpus_id(
                    _string(entry.get("corpus_id"), field="corpus_id", maximum=64)
                ),
                "source_manifest_sha256": digest,
            }
        )
    corpora.sort(key=lambda entry: entry["corpus_id"])
    corpus_ids = [entry["corpus_id"] for entry in corpora]
    if len(corpus_ids) != len(set(corpus_ids)):
        raise ContextMigrationValidationError(
            "context migration corpora must be unique"
        )
    raw_contexts = value.get("contexts")
    if (
        not isinstance(raw_contexts, list)
        or not 1 <= len(raw_contexts) <= CONTEXT_MIGRATION_MAX_CONTEXTS
    ):
        raise ContextMigrationValidationError(
            "context migration context count is outside its limit"
        )
    contexts = sorted(
        (
            _canonical_context(context, known_corpora=set(corpus_ids))
            for context in raw_contexts
        ),
        key=lambda context: context["context_id"],
    )
    context_ids = [context["context_id"] for context in contexts]
    item_ids = [item["item_id"] for context in contexts for item in context["items"]]
    source_count = sum(
        len(item["sources"])
        for context in contexts
        for item in context["items"]
    )
    used_corpora = {
        corpus_id for context in contexts for corpus_id in context["corpus_ids"]
    }
    if len(context_ids) != len(set(context_ids)) or len(item_ids) != len(set(item_ids)):
        raise ContextMigrationValidationError(
            "context migration context and item identities must be globally unique"
        )
    if len(item_ids) > CONTEXT_MIGRATION_MAX_ITEMS or source_count > CONTEXT_MIGRATION_MAX_SOURCES:
        raise ContextMigrationValidationError(
            "context migration item or source count exceeds its limit"
        )
    if used_corpora != set(corpus_ids):
        raise ContextMigrationValidationError(
            "context migration corpus descriptors must exactly cover the contexts"
        )
    unsigned = {
        "format": CONTEXT_MIGRATION_FORMAT,
        "corpora": corpora,
        "contexts": sorted(
            (
                _canonical_context(context, known_corpora=set(corpus_ids))
                for context in contexts
            ),
            key=lambda context: context["context_id"],
        ),
    }
    digest = _digest(unsigned)
    if value.get("bundle_sha256") != digest:
        raise ContextMigrationValidationError(
            "context migration bundle digest does not match its payload"
        )
    canonical = {**unsigned, "bundle_sha256": digest}
    if len(_canonical_bytes(canonical)) > CONTEXT_MIGRATION_MAX_BYTES:
        raise ContextMigrationValidationError(
            "context migration bundle exceeds its byte limit"
        )
    return canonical, digest


def parse_context_migration_bundle(payload: bytes) -> tuple[dict[str, Any], str]:
    if len(payload) > CONTEXT_MIGRATION_MAX_BYTES:
        raise ContextMigrationValidationError(
            "context migration bundle exceeds its byte limit"
        )

    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, child in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = child
        return result

    try:
        value = json.loads(
            payload,
            object_pairs_hook=object_pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("non-finite")),
        )
    except (RecursionError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ContextMigrationValidationError(
            "context migration bundle is not strict JSON"
        ) from exc
    canonical, digest = canonical_context_migration_bundle(value)
    if payload != _canonical_bytes(canonical):
        raise ContextMigrationValidationError(
            "context migration bundle is not canonical JSON"
        )
    return canonical, digest


def _source_descriptor(source: Mapping[str, Any]) -> dict[str, str]:
    return {
        "corpus_id": source["corpus_id"],
        "document_id": source["document_id"],
        "revision_id": source["revision_id"],
        "projection_id": source["projection_id"],
        "source_unit_id": source["source_unit_id"],
        "link_role": source["link_role"],
    }


def _context_snapshot(
    data_root: Path,
    *,
    context_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    parameters: tuple[str, ...] = ()
    where = ""
    if context_ids is not None:
        normalized = tuple(sorted(normalize_context_id(value) for value in context_ids))
        if not normalized:
            return []
        where = f" WHERE context_id IN ({','.join('?' for _ in normalized)})"
        parameters = normalized
    with context_read_connection(data_root) as connection:
        context_rows = connection.execute(
            f"SELECT * FROM contexts{where} ORDER BY context_id",
            parameters,
        ).fetchall()
        corpus_rows = connection.execute(
            f"SELECT * FROM context_corpora{where} ORDER BY context_id, corpus_id",
            parameters,
        ).fetchall()
        item_rows = connection.execute(
            f"""
            SELECT * FROM context_items
            {where}{' AND' if where else ' WHERE'} lifecycle_state = 'active'
            ORDER BY context_id, item_id
            """,
            parameters,
        ).fetchall()
        active_item_ids = tuple(row["item_id"] for row in item_rows)
        if active_item_ids:
            item_placeholders = ",".join("?" for _ in active_item_ids)
            source_rows = connection.execute(
                f"""
                SELECT * FROM context_sources
                WHERE item_id IN ({item_placeholders})
                ORDER BY item_id, source_ref_id
                """,
                active_item_ids,
            ).fetchall()
            external_count = connection.execute(
                f"""
                SELECT COUNT(*) FROM context_external_sources
                WHERE item_id IN ({item_placeholders})
                """,
                active_item_ids,
            ).fetchone()[0]
        else:
            source_rows = []
            external_count = 0
    if external_count:
        raise ContextMigrationValidationError(
            "provider-linked current context cannot be migrated",
            details={"reason": "external_source_state_present"},
        )
    corpora_by_context: dict[str, list[str]] = defaultdict(list)
    for row in corpus_rows:
        corpora_by_context[row["context_id"]].append(row["corpus_id"])
    sources_by_item: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in source_rows:
        sources_by_item[row["item_id"]].append(_source_descriptor(row))
    items_by_context: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in item_rows:
        items_by_context[row["context_id"]].append(
            {
                "item_id": row["item_id"],
                "client_ref": row["client_ref"],
                "kind": row["kind"],
                "body_text": row["body_text"],
                "attributes": json.loads(row["attributes_json"]),
                "disclosure_state": row["disclosure_state"],
                "created_at": row["created_at"],
                "sources": sources_by_item.get(row["item_id"], []),
            }
        )
    return [
        {
            "context_id": row["context_id"],
            "title": row["title"],
            "purpose": row["purpose"],
            "scope": json.loads(row["scope_json"]),
            "corpus_ids": corpora_by_context.get(row["context_id"], []),
            "state": row["state"],
            "version": row["version"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "items": items_by_context.get(row["context_id"], []),
        }
        for row in context_rows
    ]


def export_context_migration_bundle(
    data_root: Path,
    *,
    context_ids: list[str] | None = None,
) -> dict[str, Any]:
    if not (data_root / "contexts.sqlite3").is_file():
        raise ContextMigrationValidationError("context migration source does not exist")
    selected_context_ids: set[str] | None = None
    if context_ids is not None:
        if (
            not isinstance(context_ids, list)
            or not 1 <= len(context_ids) <= CONTEXT_MIGRATION_MAX_CONTEXTS
        ):
            raise ContextMigrationValidationError(
                "context migration selection must be a bounded non-empty list"
            )
        normalized_context_ids = [
            normalize_context_id(
                _string(
                    context_id,
                    field="context_ids",
                    maximum=64,
                )
            )
            for context_id in context_ids
        ]
        if len(normalized_context_ids) != len(set(normalized_context_ids)):
            raise ContextMigrationValidationError(
                "context migration selection contains duplicate context ids"
            )
        selected_context_ids = set(normalized_context_ids)
    with context_reader_lock(data_root):
        contexts = _context_snapshot(data_root, context_ids=selected_context_ids)
        if selected_context_ids is not None and {
            context["context_id"] for context in contexts
        } != selected_context_ids:
            raise ContextMigrationValidationError(
                "context migration selected context does not exist",
                details={"reason": "selected_context_missing"},
            )
        if not contexts:
            raise ContextMigrationValidationError("context migration source is empty")
        corpus_ids = sorted(
            {corpus_id for context in contexts for corpus_id in context["corpus_ids"]}
        )
        corpora: list[dict[str, str]] = []
        for corpus_id in corpus_ids:
            corpus = get_corpus(data_root, corpus_id)
            if corpus["provider_kind"] != "filesystem":
                raise ContextMigrationValidationError(
                    "context migration requires a filesystem corpus",
                    details={"reason": "provider_source_not_portable", "corpus_id": corpus_id},
                )
            manifest = build_source_sync_manifest(
                corpus_id=corpus_id,
                source_root=Path(corpus["source_root"]),
                source_scope=corpus["source_scope"],
            )
            _canonical, manifest_sha256 = canonical_source_sync_manifest(manifest)
            corpora.append(
                {
                    "corpus_id": corpus_id,
                    "source_manifest_sha256": manifest_sha256,
                }
            )
    unsigned = {
        "format": CONTEXT_MIGRATION_FORMAT,
        "corpora": corpora,
        "contexts": sorted(
            (
                _canonical_context(context, known_corpora=set(corpus_ids))
                for context in contexts
            ),
            key=lambda context: context["context_id"],
        ),
    }
    raw = {**unsigned, "bundle_sha256": _digest(unsigned)}
    canonical, _bundle_sha256 = canonical_context_migration_bundle(raw)
    return canonical


def _canonical_source_heads(
    value: object,
    *,
    bundle_corpora: list[dict[str, str]],
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) != len(bundle_corpora):
        raise ContextMigrationValidationError(
            "context migration source head count is invalid"
        )
    heads = []
    for entry in value:
        if not isinstance(entry, dict) or set(entry) != {
            "corpus_id",
            "generation",
            "manifest_sha256",
        }:
            raise ContextMigrationValidationError(
                "context migration source head fields are invalid"
            )
        generation = entry.get("generation")
        corpus_id = entry.get("corpus_id")
        if not isinstance(corpus_id, str):
            raise ContextMigrationValidationError(
                "context migration source head corpus_id is invalid"
            )
        if type(generation) is not int or generation <= 0:
            raise ContextMigrationValidationError(
                "context migration source head is invalid"
            )
        digest = _sha256(entry.get("manifest_sha256"), field="manifest_sha256")
        heads.append(
            {
                "corpus_id": normalize_corpus_id(corpus_id),
                "generation": generation,
                "manifest_sha256": digest,
            }
        )
    heads.sort(key=lambda entry: entry["corpus_id"])
    expected_digests = {
        entry["corpus_id"]: entry["source_manifest_sha256"]
        for entry in bundle_corpora
    }
    if [entry["corpus_id"] for entry in heads] != sorted(expected_digests) or any(
        head["manifest_sha256"] != expected_digests[head["corpus_id"]]
        for head in heads
    ):
        raise ContextMigrationConflictError(
            "context migration source heads do not match the local source generation",
            details={"reason": "source_manifest_mismatch"},
        )
    return heads


def _verify_current_heads(
    expected_heads: list[dict[str, Any]],
    *,
    source_head_reader: SourceHeadReader,
) -> None:
    for expected in expected_heads:
        current = source_head_reader(expected["corpus_id"])
        if (
            set(current) != {"generation", "manifest_sha256", "index_state"}
            or current.get("index_state") != "indexed"
            or current.get("generation") != expected["generation"]
            or current.get("manifest_sha256") != expected["manifest_sha256"]
        ):
            raise ContextMigrationConflictError(
                "context migration source generation is not current and indexed",
                details={
                    "reason": "source_generation_mismatch",
                    "corpus_id": expected["corpus_id"],
                },
            )


def _destination_source_mapping(
    data_root: Path,
    contexts: list[dict[str, Any]],
    *,
    adapter_registry: AdapterRegistry,
) -> dict[tuple[str, str, str, str, str], dict[str, Any]]:
    requested: dict[str, set[tuple[str, str, str, str]]] = defaultdict(set)
    for context in contexts:
        for item in context["items"]:
            for source in item["sources"]:
                requested[source["corpus_id"]].add(
                    (
                        source["document_id"],
                        source["revision_id"],
                        source["projection_id"],
                        source["source_unit_id"],
                    )
                )
    mapped: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for corpus_id, tuples in requested.items():
        corpus = get_corpus(data_root, corpus_id)
        if (
            corpus["execution_policy"] != "external_host_allowed"
            or corpus["provider_kind"] != "filesystem"
        ):
            raise ContextMigrationConflictError(
                "context migration destination corpus is not externally usable",
                details={"reason": "destination_corpus_policy", "corpus_id": corpus_id},
            )
        with corpus_read_connection(data_root, corpus_id) as connection:
            snapshot = connection.execute(
                """
                SELECT snapshot_id
                FROM snapshots
                WHERE state = 'complete'
                ORDER BY rowid DESC
                LIMIT 1
                """
            ).fetchone()
            if snapshot is None:
                raise ContextMigrationConflictError(
                    "context migration destination has no complete snapshot",
                    details={"reason": "destination_snapshot_missing", "corpus_id": corpus_id},
                )
            for document_id, revision_id, projection_id, unit_id in sorted(tuples):
                row = connection.execute(
                    """
                    SELECT u.source_anchor_json, d.extension,
                           p.adapter_id, p.adapter_version, p.config_hash
                    FROM source_units u
                    JOIN revisions r
                      ON r.revision_id = u.revision_id
                     AND r.document_id = ?
                    JOIN documents d
                      ON d.document_id = r.document_id
                     AND d.current_revision_id = r.revision_id
                     AND d.deleted_at IS NULL
                    JOIN extraction_projections p
                      ON p.projection_id = u.projection_id
                     AND p.revision_id = r.revision_id
                     AND p.is_active = 1
                    JOIN snapshot_documents sd
                      ON sd.snapshot_id = ?
                     AND sd.document_id = d.document_id
                     AND sd.revision_id = r.revision_id
                     AND sd.projection_id = p.projection_id
                    WHERE u.unit_id = ?
                      AND u.revision_id = ?
                      AND u.projection_id = ?
                    """,
                    (
                        document_id,
                        snapshot["snapshot_id"],
                        unit_id,
                        revision_id,
                        projection_id,
                    ),
                ).fetchone()
                if row is None:
                    raise ContextMigrationConflictError(
                        "context migration source tuple is unresolved in the current snapshot",
                        details={
                            "reason": "source_tuple_unresolved",
                            "corpus_id": corpus_id,
                            "document_id": document_id,
                            "source_unit_id": unit_id,
                        },
                    )
                try:
                    descriptor = adapter_registry.resolve(row["extension"]).descriptor
                except CorpusError as exc:
                    raise ContextMigrationConflictError(
                        "context migration source adapter is unavailable",
                        details={
                            "reason": "source_adapter_unavailable",
                            "corpus_id": corpus_id,
                            "document_id": document_id,
                        },
                    ) from exc
                if (
                    row["adapter_id"],
                    row["adapter_version"],
                    row["config_hash"],
                ) != (
                    descriptor.adapter_id,
                    descriptor.adapter_version,
                    descriptor.config_hash,
                ):
                    raise ContextMigrationConflictError(
                        "context migration source adapter is not current",
                        details={
                            "reason": "source_adapter_not_current",
                            "corpus_id": corpus_id,
                            "document_id": document_id,
                        },
                    )
                try:
                    anchor = json.loads(row["source_anchor_json"])
                except (TypeError, json.JSONDecodeError) as exc:
                    raise ContextMigrationConflictError(
                        "context migration source anchor is invalid",
                        details={"reason": "source_anchor_invalid", "corpus_id": corpus_id},
                    ) from exc
                span = anchor.get("source_span", {}) if isinstance(anchor, dict) else {}
                if not isinstance(span, dict):
                    raise ContextMigrationConflictError(
                        "context migration source span is invalid",
                        details={"reason": "source_span_invalid", "corpus_id": corpus_id},
                    )
                key = (corpus_id, document_id, revision_id, projection_id, unit_id)
                mapped[key] = {
                    "snapshot_id": snapshot["snapshot_id"],
                    "source_span": span,
                }
    return mapped


def _idempotency_key_hash(value: object) -> tuple[str, str]:
    key = _string(value, field="idempotency_key", maximum=512)
    encoded = key.encode()
    if not 8 <= len(encoded) <= 512:
        raise ContextMigrationValidationError(
            "context migration idempotency key byte length is invalid"
        )
    digest = hashlib.sha256(
        b"corpus-context-migration-idempotency-v1\0" + encoded
    ).hexdigest()
    return key, digest


def _receipt_name(idempotency_key_sha256: str) -> str:
    return f"receipt-{idempotency_key_sha256}.json"


def _read_receipt(
    data_root: Path,
    *,
    idempotency_key_sha256: str,
) -> dict[str, Any] | None:
    name = _receipt_name(idempotency_key_sha256)
    root = data_root / _RECEIPT_DIRECTORY_NAME
    path = root / name
    try:
        with private_directory(root) as parent_descriptor:
            descriptor, _created = open_private_file_at(
                parent_descriptor,
                name,
                path=path,
            )
            try:
                payload = os.read(descriptor, CONTEXT_MIGRATION_RECEIPT_MAX_BYTES + 1)
                if os.read(descriptor, 1):
                    payload += b"x"
            finally:
                os.close(descriptor)
    except CorpusError as exc:
        if exc.details.get("reason") in {"missing", "missing_parent"}:
            return None
        raise ContextMigrationConflictError(
            "context migration receipt is unavailable",
            details={"reason": "receipt_unavailable"},
        ) from exc
    if len(payload) > CONTEXT_MIGRATION_RECEIPT_MAX_BYTES:
        raise ContextMigrationConflictError(
            "context migration receipt exceeds its limit",
            details={"reason": "receipt_invalid"},
        )
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContextMigrationConflictError(
            "context migration receipt is invalid",
            details={"reason": "receipt_invalid"},
        ) from exc
    if (
        not isinstance(value, dict)
        or set(value) != {
            "format",
            "bundle_sha256",
            "context_ids",
            "idempotency_key_sha256",
            "status",
            "updated_at",
        }
        or value.get("format") != CONTEXT_MIGRATION_RECEIPT_FORMAT
        or not isinstance(value.get("bundle_sha256"), str)
        or len(value["bundle_sha256"]) != 64
        or any(character not in _HEX_CHARACTERS for character in value["bundle_sha256"])
        or value.get("idempotency_key_sha256") != idempotency_key_sha256
        or not isinstance(value.get("context_ids"), list)
        or not 1 <= len(value["context_ids"]) <= CONTEXT_MIGRATION_MAX_CONTEXTS
        or any(not isinstance(context_id, str) for context_id in value["context_ids"])
        or value["context_ids"]
        != sorted(normalize_context_id(context_id) for context_id in value["context_ids"])
        or len(value["context_ids"]) != len(set(value["context_ids"]))
        or value.get("status") not in {"pending", "applied"}
        or not isinstance(value.get("updated_at"), str)
        or payload != _canonical_bytes(value)
    ):
        raise ContextMigrationConflictError(
            "context migration receipt is invalid",
            details={"reason": "receipt_invalid"},
        )
    return value


def _write_receipt(
    data_root: Path,
    *,
    bundle_sha256: str,
    context_ids: list[str],
    idempotency_key_sha256: str,
    status: str,
) -> None:
    value = {
        "format": CONTEXT_MIGRATION_RECEIPT_FORMAT,
        "bundle_sha256": bundle_sha256,
        "context_ids": context_ids,
        "idempotency_key_sha256": idempotency_key_sha256,
        "status": status,
        "updated_at": utc_now(),
    }
    payload = _canonical_bytes(value)
    if len(payload) > CONTEXT_MIGRATION_RECEIPT_MAX_BYTES:
        raise ContextMigrationConflictError(
            "context migration receipt exceeds its limit",
            details={"reason": "receipt_invalid"},
        )
    name = _receipt_name(idempotency_key_sha256)
    temporary = f".{name}.{uuid.uuid4().hex}.tmp"
    root = data_root / _RECEIPT_DIRECTORY_NAME
    with private_directory(data_root, create=True) as data_descriptor:
        parent_descriptor = ensure_private_directory_at(
            data_descriptor,
            _RECEIPT_DIRECTORY_NAME,
            path=root,
        )
        os.fsync(data_descriptor)
        descriptor: int | None = None
        try:
            try:
                os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
                receipt_exists = True
            except FileNotFoundError:
                receipt_exists = False
            if (
                not receipt_exists
                and len(os.listdir(parent_descriptor))
                >= CONTEXT_MIGRATION_MAX_RECEIPTS
            ):
                raise ContextMigrationConflictError(
                    "context migration receipt capacity is exhausted",
                    details={"reason": "receipt_capacity_exceeded"},
                )
            descriptor = os.open(
                temporary,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=parent_descriptor,
            )
            os.fchmod(descriptor, 0o600)
            offset = 0
            while offset < len(payload):
                written = os.write(descriptor, payload[offset:])
                if written <= 0:
                    raise OSError("context migration receipt write made no progress")
                offset += written
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_nlink != 1
            ):
                raise OSError("context migration receipt is unsafe")
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            os.rename(
                temporary,
                name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
            )
            os.fsync(parent_descriptor)
        except OSError as exc:
            raise ContextMigrationConflictError(
                "context migration receipt could not be persisted",
                details={"reason": "receipt_unavailable"},
            ) from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            with suppress(OSError):
                os.unlink(temporary, dir_fd=parent_descriptor)
            os.close(parent_descriptor)


def _target_state(data_root: Path, bundle: dict[str, Any]) -> str:
    database = data_root / "contexts.sqlite3"
    if not database.exists():
        return "absent"
    context_ids = {context["context_id"] for context in bundle["contexts"]}
    item_ids = {
        item["item_id"]
        for context in bundle["contexts"]
        for item in context["items"]
    }
    with context_read_connection(data_root) as connection:
        context_placeholders = ",".join("?" for _ in context_ids)
        existing_context_ids = {
            row["context_id"]
            for row in connection.execute(
                f"SELECT context_id FROM contexts WHERE context_id IN ({context_placeholders})",
                tuple(sorted(context_ids)),
            ).fetchall()
        }
        if not existing_context_ids:
            if not item_ids:
                return "absent"
            item_placeholders = ",".join("?" for _ in item_ids)
            collision = connection.execute(
                f"SELECT 1 FROM context_items WHERE item_id IN ({item_placeholders}) LIMIT 1",
                tuple(sorted(item_ids)),
            ).fetchone()
            return "different" if collision is not None else "absent"
    if existing_context_ids != context_ids:
        return "different"
    try:
        contexts = _context_snapshot(data_root, context_ids=context_ids)
    except ContextMigrationValidationError:
        return "different"
    unsigned = {
        "format": CONTEXT_MIGRATION_FORMAT,
        "corpora": bundle["corpora"],
        "contexts": sorted(
            (
                _canonical_context(
                    context,
                    known_corpora={
                        corpus["corpus_id"] for corpus in bundle["corpora"]
                    },
                )
                for context in contexts
            ),
            key=lambda context: context["context_id"],
        ),
    }
    candidate = {**unsigned, "bundle_sha256": _digest(unsigned)}
    try:
        canonical, _digest_value = canonical_context_migration_bundle(candidate)
    except ContextMigrationValidationError:
        return "different"
    return "exact" if canonical == bundle else "different"


def _insert_contexts(
    data_root: Path,
    *,
    bundle: dict[str, Any],
    mapped_sources: Mapping[tuple[str, str, str, str, str], Mapping[str, Any]],
    before_commit: Callable[[], None],
) -> None:
    with context_connection(data_root) as connection:
        context_ids = tuple(context["context_id"] for context in bundle["contexts"])
        context_placeholders = ",".join("?" for _ in context_ids)
        if connection.execute(
            f"SELECT 1 FROM contexts WHERE context_id IN ({context_placeholders}) LIMIT 1",
            context_ids,
        ).fetchone() is not None:
            raise ContextMigrationTargetNotEmptyError(
                "context migration target contexts are not absent"
            )
        item_ids = tuple(
            item["item_id"]
            for context in bundle["contexts"]
            for item in context["items"]
        )
        if item_ids:
            item_placeholders = ",".join("?" for _ in item_ids)
            if connection.execute(
                f"SELECT 1 FROM context_items WHERE item_id IN ({item_placeholders}) LIMIT 1",
                item_ids,
            ).fetchone() is not None:
                raise ContextMigrationTargetNotEmptyError(
                    "context migration item identity already exists"
                )
        for context in bundle["contexts"]:
            connection.execute(
                """
                INSERT INTO contexts(
                    context_id, title, purpose, scope_json, state, version,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    context["context_id"],
                    context["title"],
                    context["purpose"],
                    encode_json(context["scope"]),
                    context["state"],
                    context["version"],
                    context["created_at"],
                    context["updated_at"],
                ),
            )
            connection.executemany(
                "INSERT INTO context_corpora(context_id, corpus_id) VALUES (?, ?)",
                [
                    (context["context_id"], corpus_id)
                    for corpus_id in context["corpus_ids"]
                ],
            )
            for item in context["items"]:
                destination_sources = []
                for source in item["sources"]:
                    key = (
                        source["corpus_id"],
                        source["document_id"],
                        source["revision_id"],
                        source["projection_id"],
                        source["source_unit_id"],
                    )
                    destination_sources.append(
                        {
                            "corpus_id": source["corpus_id"],
                            "snapshot_id": mapped_sources[key]["snapshot_id"],
                            "document_id": source["document_id"],
                            "revision_id": source["revision_id"],
                            "projection_id": source["projection_id"],
                            "source_unit_id": source["source_unit_id"],
                            "link_role": source["link_role"],
                        }
                    )
                item_input = {
                    "client_ref": item["client_ref"],
                    "kind": item["kind"],
                    "body_text": item["body_text"],
                    "attributes": item["attributes"],
                    "disclosure_state": item["disclosure_state"],
                    "sources": destination_sources,
                    "external_sources": [],
                    "supersedes_item_id": None,
                }
                input_sha256 = hashlib.sha256(encode_json(item_input).encode()).hexdigest()
                connection.execute(
                    """
                    INSERT INTO context_items(
                        item_id, context_id, client_ref, input_sha256, kind,
                        body_text, attributes_json, disclosure_state,
                        lifecycle_state, supersedes_item_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', NULL, ?)
                    """,
                    (
                        item["item_id"],
                        context["context_id"],
                        item["client_ref"],
                        input_sha256,
                        item["kind"],
                        item["body_text"],
                        encode_json(item["attributes"]),
                        item["disclosure_state"],
                        item["created_at"],
                    ),
                )
                for source in destination_sources:
                    key = (
                        source["corpus_id"],
                        source["document_id"],
                        source["revision_id"],
                        source["projection_id"],
                        source["source_unit_id"],
                    )
                    connection.execute(
                        """
                        INSERT INTO context_sources(
                            source_ref_id, item_id, corpus_id, snapshot_id,
                            document_id, revision_id, projection_id,
                            source_unit_id, link_role, source_span_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            f"ctxs_{uuid.uuid4().hex}",
                            item["item_id"],
                            source["corpus_id"],
                            source["snapshot_id"],
                            source["document_id"],
                            source["revision_id"],
                            source["projection_id"],
                            source["source_unit_id"],
                            source["link_role"],
                            encode_json(mapped_sources[key]["source_span"]),
                        ),
                    )
        before_commit()


def import_context_migration_bundle(
    service: Any,
    *,
    bundle: Mapping[str, Any],
    expected_source_heads: object,
    idempotency_key: str,
    expected_targets_absent: bool,
    source_head_reader: SourceHeadReader,
) -> dict[str, Any]:
    if expected_targets_absent is not True:
        raise ContextMigrationValidationError(
            "context migration import requires expected_targets_absent=true"
        )
    _normalized_key, idempotency_key_sha256 = _idempotency_key_hash(idempotency_key)
    data_root = service.data_root
    canonical, bundle_sha256 = canonical_context_migration_bundle(dict(bundle))
    context_ids = [context["context_id"] for context in canonical["contexts"]]
    heads = _canonical_source_heads(
        expected_source_heads,
        bundle_corpora=canonical["corpora"],
    )
    _verify_current_heads(heads, source_head_reader=source_head_reader)

    with context_writer_lock(data_root):
        def coordinated_reader(corpus_id: str) -> Mapping[str, Any]:
            return read_coordinated_source_sync_head(service, corpus_id)

        def verify_coordinated_sources() -> None:
            for head in heads:
                require_no_remote_delete_intent(data_root, head["corpus_id"])
            _verify_current_heads(heads, source_head_reader=coordinated_reader)

        verify_coordinated_sources()
        receipt = _read_receipt(
            data_root,
            idempotency_key_sha256=idempotency_key_sha256,
        )
        if receipt is not None and receipt["bundle_sha256"] != bundle_sha256:
            raise ContextMigrationIdempotencyError(
                "context migration key was already used for another bundle"
            )
        if receipt is not None and receipt["context_ids"] != context_ids:
            raise ContextMigrationIdempotencyError(
                "context migration key was already used for other contexts"
            )
        mapped_sources = _destination_source_mapping(
            data_root,
            canonical["contexts"],
            adapter_registry=service.adapter_registry,
        )
        target_state = _target_state(data_root, canonical)
        if receipt is not None and target_state == "exact":
            if receipt["status"] != "applied":
                _write_receipt(
                    data_root,
                    bundle_sha256=bundle_sha256,
                    context_ids=context_ids,
                    idempotency_key_sha256=idempotency_key_sha256,
                    status="applied",
                )
            return {
                "effect": "current_contexts_imported",
                "bundle_sha256": bundle_sha256,
                "context_count": len(canonical["contexts"]),
                "item_count": sum(len(context["items"]) for context in canonical["contexts"]),
                "source_count": sum(
                    len(item["sources"])
                    for context in canonical["contexts"]
                    for item in context["items"]
                ),
                "replayed": True,
            }
        if receipt is not None and receipt["status"] == "applied":
            raise ContextMigrationConflictError(
                "applied context migration state is no longer exact",
                details={"reason": "applied_state_changed"},
            )
        if target_state != "absent":
            raise ContextMigrationTargetNotEmptyError(
                "context migration target contexts are not absent"
            )
        if receipt is None:
            _write_receipt(
                data_root,
                bundle_sha256=bundle_sha256,
                context_ids=context_ids,
                idempotency_key_sha256=idempotency_key_sha256,
                status="pending",
            )
        _insert_contexts(
            data_root,
            bundle=canonical,
            mapped_sources=mapped_sources,
            before_commit=verify_coordinated_sources,
        )
        _write_receipt(
            data_root,
            bundle_sha256=bundle_sha256,
            context_ids=context_ids,
            idempotency_key_sha256=idempotency_key_sha256,
            status="applied",
        )
    return {
        "effect": "current_contexts_imported",
        "bundle_sha256": bundle_sha256,
        "context_count": len(canonical["contexts"]),
        "item_count": sum(len(context["items"]) for context in canonical["contexts"]),
        "source_count": sum(
            len(item["sources"])
            for context in canonical["contexts"]
            for item in context["items"]
        ),
        "replayed": False,
    }


__all__ = [
    "CONTEXT_MIGRATION_FORMAT",
    "CONTEXT_MIGRATION_MAX_BYTES",
    "CONTEXT_MIGRATION_MAX_RECEIPTS",
    "ContextMigrationConflictError",
    "ContextMigrationError",
    "ContextMigrationIdempotencyError",
    "ContextMigrationTargetNotEmptyError",
    "ContextMigrationValidationError",
    "canonical_context_migration_bundle",
    "export_context_migration_bundle",
    "import_context_migration_bundle",
    "parse_context_migration_bundle",
]
