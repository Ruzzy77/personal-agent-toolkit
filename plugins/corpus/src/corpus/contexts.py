"""Saved context over immutable Corpus source units."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .adapter_registry import AdapterRegistry
from .config import normalize_corpus_id
from .database import (
    context_connection,
    context_read_connection,
    corpus_read_connection,
    encode_json,
    get_corpus,
    list_corpora,
    migrate_context_database,
    utc_now,
)
from .errors import (
    BudgetExceededError,
    ContextConflictError,
    ContextNotFoundError,
    ContextValidationError,
    CorpusError,
    ExtractionError,
    PolicyDeniedError,
)
from .locking import context_writer_lock
from .session_sources import (
    SESSION_SOURCE_FETCH_DEFAULT_CHARS,
    SESSION_SOURCE_PROVIDERS,
    discover_session_records,
    fetch_session_record,
    normalize_session_selector,
    probe_session_record,
)

CONTEXT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
CONTEXT_STATES = {"active", "archived"}
CONTEXT_ITEM_KINDS = {"finding", "relationship", "difference", "question", "gap"}
CONTEXT_SOURCE_ROLES = {"direct", "context", "contrast"}
CONTEXT_AUDIENCES = {"local_cli", "external_mcp"}
CONTEXT_VIEWS = {"restricted", "general"}
CONTEXT_DISCLOSURE_STATES = {"restricted", "general_candidate"}
GENERAL_CANDIDATE_KINDS = {"finding", "relationship", "difference"}

CONTEXT_DEFAULT_LIMIT = 100
CONTEXT_MAX_LIMIT = 100
CONTEXT_MAX_OFFSET = 10_000
CONTEXT_MAX_CORPORA = 20
CONTEXT_MAX_ITEMS_PER_UPDATE = 20
CONTEXT_MAX_SOURCES_PER_UPDATE = 100
CONTEXT_MAX_EXTERNAL_RECORDS_PER_UPDATE = 200
CONTEXT_MAX_UPDATE_BYTES = 64 * 1024
CONTEXT_MAX_EXTERNAL_UPDATE_BYTES = 512 * 1024
CONTEXT_MAX_READ_BYTES = 2 * 1024 * 1024
CONTEXT_MAX_TITLE_CHARS = 200
CONTEXT_MAX_PURPOSE_CHARS = 4_000
CONTEXT_MAX_BODY_CHARS = 12_000
CONTEXT_MAX_IDENTIFIER_CHARS = 200
CONTEXT_MAX_ATTRIBUTES_BYTES = 16 * 1024
CONTEXT_MAX_SCOPE_BYTES = 32 * 1024
CONTEXT_MAX_CHANGE_CANDIDATES = 200
CONTEXT_MAX_EXTERNAL_METADATA_BYTES = 32 * 1024
CONTEXT_MAX_PARTICIPANTS = 50
CONTEXT_MAX_LABEL_IDS = 100
CONTEXT_MAX_ATTACHMENTS = 100

_CREATE_KEYS = {"title", "purpose", "scope", "corpus_ids"}
_ITEM_KEYS = {
    "client_ref",
    "kind",
    "body_text",
    "attributes",
    "disclosure_state",
    "sources",
    "external_sources",
    "supersedes_item_id",
}
_GENERAL_RELEASE_KEYS = {
    "item_ids",
    "public_title",
    "public_purpose",
    "review",
}
_SOURCE_KEYS = {
    "corpus_id",
    "snapshot_id",
    "document_id",
    "revision_id",
    "projection_id",
    "source_unit_id",
    "link_role",
}
_EXTERNAL_SOURCE_KEYS = {
    "corpus_id",
    "binding_id",
    "external_id",
    "link_role",
}
_EXTERNAL_RECORD_KEYS = {
    "external_id",
    "parent_external_id",
    "occurred_at",
    "title",
    "participants",
    "label_ids",
    "attachments",
}
_SESSION_EXTERNAL_RECORD_KEYS = {
    "external_id",
    "parent_external_id",
    "occurred_at",
    "provider_metadata",
    "locator",
    "freshness_identity",
}
_SESSION_PROVIDER_METADATA_KEYS = {
    "session_id",
    "turn_id",
    "cwd",
    "workspace",
    "actor",
    "task_kind",
}
_SESSION_LOCATOR_KEYS = {
    "root_ref",
    "relative_path",
    "session_id",
    "turn_id",
}
_ATTACHMENT_KEYS = {"attachment_id", "name", "mime_type", "size"}
_GMAIL_SELECTOR_KEYS = {"account_ref", "label_id", "label_name"}
_GMAIL_SELECTOR_REQUIRED_KEYS = {"account_ref", "label_id"}
_SHA256_IDENTITY_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_WINDOWS_DRIVE_PATH_RE = re.compile(r"(?i)^[A-Z]:")


def normalize_context_id(context_id: str) -> str:
    normalized = context_id.strip().lower().replace(" ", "-")
    if not CONTEXT_ID_RE.fullmatch(normalized):
        raise ContextValidationError(
            "context id must be 1-64 lowercase letters, digits, dots, underscores, or hyphens",
            details={"context_id": context_id, "normalized": normalized},
        )
    return normalized


def _normalize_timestamp_filter(value: str | None, *, field: str) -> str | None:
    if value is None:
        return None
    normalized = _require_string(value, field=field, maximum=100)
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContextValidationError(
            f"{field} must be an ISO 8601 timestamp with a timezone"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContextValidationError(
            f"{field} must be an ISO 8601 timestamp with a timezone"
        )
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def normalize_source_binding_id(binding_id: str) -> str:
    normalized = binding_id.strip().lower().replace(" ", "-")
    if not CONTEXT_ID_RE.fullmatch(normalized):
        raise ContextValidationError(
            "source binding id must be 1-64 lowercase letters, digits, dots, underscores, "
            "or hyphens",
            details={"binding_id": binding_id, "normalized": normalized},
        )
    return normalized


def _require_string(
    value: object,
    *,
    field: str,
    maximum: int,
) -> str:
    if not isinstance(value, str):
        raise ContextValidationError(
            "context field must be a string",
            details={"field": field},
        )
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        raise ContextValidationError(
            "context string field is empty or too long",
            details={"field": field, "maximum_chars": maximum},
        )
    return normalized


def _require_session_identifier(
    value: object,
    *,
    field: str,
) -> str:
    normalized = _require_string(
        value,
        field=field,
        maximum=CONTEXT_MAX_IDENTIFIER_CHARS,
    )
    if any(
        character in {"/", "\\"} or ord(character) < 32 or ord(character) == 127
        for character in normalized
    ) or normalized.casefold().startswith("file:") or _WINDOWS_DRIVE_PATH_RE.match(
        normalized
    ):
        raise ContextValidationError(
            "session source identifier contains a path separator or control character",
            details={"field": field},
        )
    return normalized


def _require_session_relative_path(value: object, *, field: str) -> str:
    normalized = _require_string(value, field=field, maximum=2_000)
    if (
        normalized.startswith(("/", "\\"))
        or normalized == "~"
        or normalized.startswith(("~/", "~\\"))
        or re.match(r"^~[^/\\]+[/\\]", normalized)
        or normalized.casefold().startswith("file:")
        or _WINDOWS_DRIVE_PATH_RE.match(normalized)
        or "\x00" in normalized
        or ".." in re.split(r"[/\\]", normalized)
    ):
        raise ContextValidationError(
            "session source locator must be a safe relative path",
            details={"field": field},
        )
    return normalized


def _require_json_object(
    value: object,
    *,
    field: str,
    maximum_bytes: int,
) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ContextValidationError(
            "context JSON field must be an object with string keys",
            details={"field": field},
        )
    try:
        serialized = encode_json(value)
    except (TypeError, ValueError) as exc:
        raise ContextValidationError(
            "context JSON field must contain JSON-compatible values",
            details={"field": field},
        ) from exc
    size = len(serialized.encode())
    if size > maximum_bytes:
        raise BudgetExceededError(
            "context JSON field exceeds its serialized budget",
            details={
                "field": field,
                "serialized_bytes": size,
                "maximum_bytes": maximum_bytes,
            },
        )
    return json.loads(serialized)


def _optional_string(
    value: object,
    *,
    field: str,
    maximum: int,
) -> str | None:
    if value is None:
        return None
    return _require_string(value, field=field, maximum=maximum)


def _string_list(
    value: object,
    *,
    field: str,
    maximum_items: int,
    maximum_chars: int = CONTEXT_MAX_IDENTIFIER_CHARS,
) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum_items:
        raise ContextValidationError(
            "context list field is invalid",
            details={"field": field, "maximum_items": maximum_items},
        )
    result = [
        _require_string(item, field=f"{field}[]", maximum=maximum_chars)
        for item in value
    ]
    if len(set(result)) != len(result):
        raise ContextValidationError(
            "context list field must not contain duplicates",
            details={"field": field},
        )
    return result


def _validate_limit_offset(limit: int, offset: int) -> None:
    if not 1 <= limit <= CONTEXT_MAX_LIMIT:
        raise BudgetExceededError(
            "context limit is outside the supported range",
            details={"limit": limit, "maximum": CONTEXT_MAX_LIMIT},
        )
    if not 0 <= offset <= CONTEXT_MAX_OFFSET:
        raise BudgetExceededError(
            "context offset is outside the supported range",
            details={"offset": offset, "maximum": CONTEXT_MAX_OFFSET},
        )


def _validate_audience(audience: str) -> None:
    if audience not in CONTEXT_AUDIENCES:
        raise ContextValidationError(
            "unsupported context audience",
            details={"audience": audience},
        )


def _validate_view(view: str) -> None:
    if view not in CONTEXT_VIEWS:
        raise ContextValidationError(
            "unsupported context view",
            details={"view": view, "allowed": sorted(CONTEXT_VIEWS)},
        )


def _json_dict(value: str) -> dict[str, Any]:
    parsed = json.loads(value)
    return parsed if isinstance(parsed, dict) else {}


class ContextService:
    def __init__(
        self,
        data_root: Path,
        *,
        adapter_registry: AdapterRegistry,
    ) -> None:
        self.data_root = data_root
        self.adapter_registry = adapter_registry

    @property
    def database_path(self) -> Path:
        return self.data_root / "contexts.sqlite3"

    def migrate(self) -> dict[str, Any]:
        with context_writer_lock(self.data_root):
            return migrate_context_database(self.data_root)

    def _normalize_binding_selector(
        self,
        provider_kind: object,
        selector: object,
    ) -> tuple[str, dict[str, Any]]:
        provider = _require_string(
            provider_kind,
            field="provider_kind",
            maximum=64,
        ).lower()
        if provider in SESSION_SOURCE_PROVIDERS:
            return provider, normalize_session_selector(provider, selector)
        if provider != "gmail":
            raise ContextValidationError(
                "unsupported linked source provider",
                details={
                    "provider_kind": provider,
                    "allowed": ["gmail", *sorted(SESSION_SOURCE_PROVIDERS)],
                },
            )
        normalized = _require_json_object(
            selector,
            field="selector",
            maximum_bytes=CONTEXT_MAX_EXTERNAL_METADATA_BYTES,
        )
        if (
            not _GMAIL_SELECTOR_REQUIRED_KEYS.issubset(normalized)
            or set(normalized) - _GMAIL_SELECTOR_KEYS
        ):
            raise ContextValidationError(
                "gmail selector fields are invalid",
                details={
                    "required": sorted(_GMAIL_SELECTOR_REQUIRED_KEYS),
                    "allowed": sorted(_GMAIL_SELECTOR_KEYS),
                },
            )
        result = {
            "account_ref": _require_string(
                normalized["account_ref"],
                field="selector.account_ref",
                maximum=CONTEXT_MAX_IDENTIFIER_CHARS,
            ),
            "label_id": _require_string(
                normalized["label_id"],
                field="selector.label_id",
                maximum=CONTEXT_MAX_IDENTIFIER_CHARS,
            ),
        }
        if "label_name" in normalized:
            result["label_name"] = _require_string(
                normalized["label_name"],
                field="selector.label_name",
                maximum=CONTEXT_MAX_TITLE_CHARS,
            )
        return provider, result

    def _normalize_attachment(self, value: object) -> dict[str, Any]:
        if not isinstance(value, dict) or set(value) - _ATTACHMENT_KEYS:
            raise ContextValidationError(
                "external source attachment fields are invalid",
                details={"allowed": sorted(_ATTACHMENT_KEYS)},
            )
        result: dict[str, Any] = {}
        for field in ("attachment_id", "name", "mime_type"):
            if field in value:
                result[field] = _require_string(
                    value[field],
                    field=f"attachment.{field}",
                    maximum=CONTEXT_MAX_IDENTIFIER_CHARS,
                )
        if "size" in value:
            size = value["size"]
            if not isinstance(size, int) or isinstance(size, bool) or size < 0:
                raise ContextValidationError(
                    "external source attachment size is invalid"
                )
            result["size"] = size
        if not result:
            raise ContextValidationError(
                "external source attachment must contain metadata"
            )
        return result

    def _normalize_session_external_record(
        self,
        value: object,
        *,
        provider_kind: str,
    ) -> dict[str, Any]:
        if (
            not isinstance(value, dict)
            or set(value) != _SESSION_EXTERNAL_RECORD_KEYS
        ):
            raise ContextValidationError(
                "session source record fields are invalid",
                details={"required": sorted(_SESSION_EXTERNAL_RECORD_KEYS)},
            )
        provider_metadata = _require_json_object(
            value["provider_metadata"],
            field="provider_metadata",
            maximum_bytes=CONTEXT_MAX_EXTERNAL_METADATA_BYTES,
        )
        if set(provider_metadata) != _SESSION_PROVIDER_METADATA_KEYS:
            raise ContextValidationError(
                "session source provider metadata fields are invalid",
                details={"required": sorted(_SESSION_PROVIDER_METADATA_KEYS)},
            )
        session_id = _require_session_identifier(
            provider_metadata["session_id"],
            field="provider_metadata.session_id",
        )
        turn_id = _require_session_identifier(
            provider_metadata["turn_id"],
            field="provider_metadata.turn_id",
        )
        actor = _require_string(
            provider_metadata["actor"],
            field="provider_metadata.actor",
            maximum=32,
        )
        if actor not in {"user_task", "subagent_task"}:
            raise ContextValidationError(
                "session source actor is invalid",
                details={"allowed": ["subagent_task", "user_task"]},
            )
        task_kind = _require_string(
            provider_metadata["task_kind"],
            field="provider_metadata.task_kind",
            maximum=64,
        )
        if task_kind != f"{provider_kind}_turn":
            raise ContextValidationError(
                "session source task kind does not match its provider",
                details={"expected": f"{provider_kind}_turn"},
            )
        normalized_metadata = {
            "session_id": session_id,
            "turn_id": turn_id,
            "cwd": _require_string(
                provider_metadata["cwd"],
                field="provider_metadata.cwd",
                maximum=2_000,
            ),
            "workspace": _require_string(
                provider_metadata["workspace"],
                field="provider_metadata.workspace",
                maximum=2_000,
            ),
            "actor": actor,
            "task_kind": task_kind,
        }
        locator = _require_json_object(
            value["locator"],
            field="locator",
            maximum_bytes=CONTEXT_MAX_EXTERNAL_METADATA_BYTES,
        )
        if set(locator) != _SESSION_LOCATOR_KEYS:
            raise ContextValidationError(
                "session source locator fields are invalid",
                details={"required": sorted(_SESSION_LOCATOR_KEYS)},
            )
        normalized_locator = {
            "root_ref": _require_session_identifier(
                locator["root_ref"],
                field="locator.root_ref",
            ),
            "relative_path": _require_session_relative_path(
                locator["relative_path"],
                field="locator.relative_path",
            ),
            "session_id": _require_session_identifier(
                locator["session_id"],
                field="locator.session_id",
            ),
            "turn_id": _require_session_identifier(
                locator["turn_id"],
                field="locator.turn_id",
            ),
        }
        if (
            normalized_locator["session_id"] != session_id
            or normalized_locator["turn_id"] != turn_id
        ):
            raise ContextValidationError(
                "session source locator does not match provider metadata"
            )
        freshness_identity = _require_string(
            value["freshness_identity"],
            field="freshness_identity",
            maximum=100,
        )
        if not _SHA256_IDENTITY_RE.fullmatch(freshness_identity):
            raise ContextValidationError(
                "session source freshness identity must be a sha256 digest"
            )
        record = {
            "external_id": _require_session_identifier(
                value["external_id"],
                field="external_id",
            ),
            "parent_external_id": _require_session_identifier(
                value["parent_external_id"],
                field="parent_external_id",
            ),
            "occurred_at": _require_string(
                value["occurred_at"],
                field="occurred_at",
                maximum=100,
            ),
            "title": None,
            "participants": [],
            "label_ids": [],
            "attachments": [],
            "provider_metadata": normalized_metadata,
            "locator": normalized_locator,
            "freshness_identity": freshness_identity,
            "metadata_observed": True,
        }
        if record["parent_external_id"] != session_id:
            raise ContextValidationError(
                "session source parent id does not match provider metadata"
            )
        canonical_freshness = {
            "external_id": record["external_id"],
            "parent_external_id": record["parent_external_id"],
            "occurred_at": record["occurred_at"],
            "provider_metadata": normalized_metadata,
            "freshness_identity": freshness_identity,
        }
        record["metadata_sha256"] = hashlib.sha256(
            encode_json(canonical_freshness).encode()
        ).hexdigest()
        return record

    def _normalize_external_record(
        self,
        value: object,
        *,
        provider_kind: str,
    ) -> dict[str, Any]:
        if provider_kind in SESSION_SOURCE_PROVIDERS:
            return self._normalize_session_external_record(
                value,
                provider_kind=provider_kind,
            )
        if not isinstance(value, dict) or set(value) - _EXTERNAL_RECORD_KEYS:
            raise ContextValidationError(
                "external source record fields are invalid",
                details={"allowed": sorted(_EXTERNAL_RECORD_KEYS)},
            )
        external_id = _require_string(
            value.get("external_id"),
            field="external_id",
            maximum=CONTEXT_MAX_IDENTIFIER_CHARS,
        )
        participants = _string_list(
            value.get("participants", []),
            field="participants",
            maximum_items=CONTEXT_MAX_PARTICIPANTS,
            maximum_chars=500,
        )
        label_ids = _string_list(
            value.get("label_ids", []),
            field="label_ids",
            maximum_items=CONTEXT_MAX_LABEL_IDS,
        )
        raw_attachments = value.get("attachments", [])
        if not isinstance(raw_attachments, list) or len(raw_attachments) > CONTEXT_MAX_ATTACHMENTS:
            raise ContextValidationError(
                "external source attachments are invalid",
                details={"maximum_items": CONTEXT_MAX_ATTACHMENTS},
            )
        attachments = [
            self._normalize_attachment(attachment)
            for attachment in raw_attachments
        ]
        legacy_record = {
            "external_id": external_id,
            "parent_external_id": _optional_string(
                value.get("parent_external_id"),
                field="parent_external_id",
                maximum=CONTEXT_MAX_IDENTIFIER_CHARS,
            ),
            "occurred_at": _optional_string(
                value.get("occurred_at"),
                field="occurred_at",
                maximum=100,
            ),
            "title": _optional_string(
                value.get("title"),
                field="title",
                maximum=CONTEXT_MAX_TITLE_CHARS,
            ),
            "participants": participants,
            "label_ids": label_ids,
            "attachments": attachments,
        }
        record = {
            **legacy_record,
            "provider_metadata": {},
            "locator": {},
            "freshness_identity": None,
        }
        metadata_observed = any(
            field in value for field in (_EXTERNAL_RECORD_KEYS - {"external_id"})
        )
        serialized = encode_json(legacy_record)
        if len(serialized.encode()) > CONTEXT_MAX_EXTERNAL_METADATA_BYTES:
            raise BudgetExceededError(
                "external source record metadata exceeds its serialized budget",
                details={"maximum_bytes": CONTEXT_MAX_EXTERNAL_METADATA_BYTES},
            )
        record["metadata_sha256"] = hashlib.sha256(serialized.encode()).hexdigest()
        record["metadata_observed"] = metadata_observed
        return record

    @staticmethod
    def _external_source_record_id(binding_id: str, external_id: str) -> str:
        digest = hashlib.sha256(f"{binding_id}\0{external_id}".encode()).hexdigest()
        return f"ext_{digest[:40]}"

    def source_update(
        self,
        *,
        action: str,
        corpus_id: str,
        binding_id: str,
        payload: dict[str, Any],
        confirm_persistent_context_write: bool,
        audience: str = "local_cli",
    ) -> dict[str, Any]:
        _validate_audience(audience)
        if not confirm_persistent_context_write:
            raise ContextValidationError(
                "linked source update requires explicit confirmation"
            )
        corpus_id = normalize_corpus_id(corpus_id)
        get_corpus(self.data_root, corpus_id)
        if audience == "external_mcp":
            self._require_external_visibility([corpus_id])
        binding_id = normalize_source_binding_id(binding_id)
        if action not in {"bind", "observe", "refresh"}:
            raise ContextValidationError(
                "unsupported linked source update action",
                details={
                    "action": action,
                    "allowed": ["bind", "observe", "refresh"],
                },
            )
        if not isinstance(payload, dict):
            raise ContextValidationError("linked source update payload must be an object")
        try:
            serialized = encode_json(payload)
        except (TypeError, ValueError) as exc:
            raise ContextValidationError(
                "linked source update payload must be JSON-compatible"
            ) from exc
        if len(serialized.encode()) > CONTEXT_MAX_EXTERNAL_UPDATE_BYTES:
            raise BudgetExceededError(
                "linked source update payload exceeds its serialized budget",
                details={"maximum_bytes": CONTEXT_MAX_EXTERNAL_UPDATE_BYTES},
            )
        if action == "bind":
            return self._bind_source(
                corpus_id=corpus_id,
                binding_id=binding_id,
                payload=payload,
            )
        if action == "refresh":
            return self._refresh_source_records(
                corpus_id=corpus_id,
                binding_id=binding_id,
                payload=payload,
            )
        return self._observe_source_records(
            corpus_id=corpus_id,
            binding_id=binding_id,
            payload=payload,
        )

    def _bind_source(
        self,
        *,
        corpus_id: str,
        binding_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if set(payload) != {"provider_kind", "selector"}:
            raise ContextValidationError(
                "linked source bind payload fields are invalid",
                details={"required": ["provider_kind", "selector"]},
            )
        provider_kind, selector = self._normalize_binding_selector(
            payload["provider_kind"],
            payload["selector"],
        )
        selector_json = encode_json(selector)
        now = utc_now()
        with (
            context_writer_lock(self.data_root),
            context_connection(self.data_root) as connection,
        ):
            existing = connection.execute(
                "SELECT * FROM corpus_source_bindings WHERE binding_id = ?",
                (binding_id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["corpus_id"] == corpus_id
                    and existing["provider_kind"] == provider_kind
                    and existing["selector_json"] == selector_json
                    and existing["state"] == "active"
                ):
                    return {
                        "binding_id": binding_id,
                        "corpus_id": corpus_id,
                        "provider_kind": provider_kind,
                        "selector": selector,
                        "state": "active",
                        "idempotent_replay": True,
                    }
                raise ContextConflictError(
                    "linked source binding id already exists with different input",
                    details={"reason": "binding_id_payload_mismatch"},
                )
            duplicate = connection.execute(
                """
                SELECT binding_id
                FROM corpus_source_bindings
                WHERE corpus_id = ? AND provider_kind = ? AND selector_json = ?
                """,
                (corpus_id, provider_kind, selector_json),
            ).fetchone()
            if duplicate is not None:
                raise ContextConflictError(
                    "linked source selector is already bound",
                    details={
                        "reason": "selector_already_bound",
                        "binding_id": duplicate["binding_id"],
                    },
                )
            connection.execute(
                """
                INSERT INTO corpus_source_bindings(
                    binding_id, corpus_id, provider_kind, selector_json, state,
                    last_complete_run_id, last_complete_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'active', NULL, NULL, ?, ?)
                """,
                (binding_id, corpus_id, provider_kind, selector_json, now, now),
            )
        return {
            "binding_id": binding_id,
            "corpus_id": corpus_id,
            "provider_kind": provider_kind,
            "selector": selector,
            "state": "active",
            "idempotent_replay": False,
        }

    def _observe_source_records(
        self,
        *,
        corpus_id: str,
        binding_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if set(payload) != {"run_id", "records", "complete"}:
            raise ContextValidationError(
                "linked source observation payload fields are invalid",
                details={"required": ["complete", "records", "run_id"]},
            )
        run_id = _require_session_identifier(
            payload["run_id"],
            field="run_id",
        )
        complete = payload["complete"]
        if not isinstance(complete, bool):
            raise ContextValidationError("linked source complete flag must be a boolean")
        raw_records = payload["records"]
        if (
            not isinstance(raw_records, list)
            or len(raw_records) > CONTEXT_MAX_EXTERNAL_RECORDS_PER_UPDATE
        ):
            raise BudgetExceededError(
                "linked source record count exceeds the update budget",
                details={
                    "maximum_records": CONTEXT_MAX_EXTERNAL_RECORDS_PER_UPDATE,
                },
            )
        now = utc_now()
        with (
            context_writer_lock(self.data_root),
            context_connection(self.data_root) as connection,
        ):
            binding = connection.execute(
                "SELECT * FROM corpus_source_bindings WHERE binding_id = ?",
                (binding_id,),
            ).fetchone()
            if (
                binding is None
                or binding["corpus_id"] != corpus_id
                or binding["state"] != "active"
            ):
                raise ContextNotFoundError("linked source binding does not exist")
            records = [
                self._normalize_external_record(
                    record,
                    provider_kind=binding["provider_kind"],
                )
                for record in raw_records
            ]
            external_ids = [record["external_id"] for record in records]
            if len(set(external_ids)) != len(external_ids):
                raise ContextValidationError(
                    "linked source external ids must be unique per update"
                )
            run = connection.execute(
                "SELECT * FROM external_source_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if run is not None:
                if run["binding_id"] != binding_id:
                    raise ContextConflictError(
                        "linked source run id belongs to another binding",
                        details={"reason": "run_binding_mismatch"},
                    )
                if run["status"] == "complete":
                    total = connection.execute(
                        """
                        SELECT COUNT(*)
                        FROM external_source_records
                        WHERE binding_id = ? AND last_seen_run_id = ?
                        """,
                        (binding_id, run_id),
                    ).fetchone()[0]
                    return {
                        "binding_id": binding_id,
                        "corpus_id": corpus_id,
                        "run_id": run_id,
                        "status": "complete",
                        "observed_in_run": total,
                        "removed_count": 0,
                        "idempotent_replay": True,
                    }
                if run["superseded_at"] is not None:
                    raise ContextConflictError(
                        "linked source observation run is stale",
                        details={"reason": "stale_observation_run"},
                    )
                if run["base_complete_run_id"] != binding["last_complete_run_id"]:
                    raise ContextConflictError(
                        "linked source observation run is stale",
                        details={"reason": "stale_observation_run"},
                    )
            else:
                connection.execute(
                    """
                    UPDATE external_source_runs
                    SET superseded_at = ?
                    WHERE binding_id = ?
                      AND status = 'incomplete'
                      AND superseded_at IS NULL
                    """,
                    (now, binding_id),
                )
                connection.execute(
                    """
                    INSERT INTO external_source_runs(
                        run_id, binding_id, base_complete_run_id,
                        status, started_at, completed_at, superseded_at
                    ) VALUES (?, ?, ?, 'incomplete', ?, NULL, NULL)
                    """,
                    (
                        run_id,
                        binding_id,
                        binding["last_complete_run_id"],
                        now,
                    ),
                )
            for record in records:
                source_record_id = self._external_source_record_id(
                    binding_id,
                    record["external_id"],
                )
                if not record["metadata_observed"]:
                    updated = connection.execute(
                        """
                        UPDATE external_source_records
                        SET membership_state = 'active',
                            last_seen_run_id = ?,
                            last_seen_at = ?
                        WHERE binding_id = ? AND external_id = ?
                        """,
                        (
                            run_id,
                            now,
                            binding_id,
                            record["external_id"],
                        ),
                    ).rowcount
                    if updated:
                        continue
                connection.execute(
                    """
                    INSERT INTO external_source_records(
                        source_record_id, binding_id, external_id,
                        parent_external_id, occurred_at, title,
                        participants_json, label_ids_json, attachments_json,
                        provider_metadata_json, locator_json, freshness_identity,
                        metadata_sha256, membership_state, last_seen_run_id,
                        first_seen_at, last_seen_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
                    ON CONFLICT(binding_id, external_id) DO UPDATE SET
                        parent_external_id = excluded.parent_external_id,
                        occurred_at = excluded.occurred_at,
                        title = excluded.title,
                        participants_json = excluded.participants_json,
                        label_ids_json = excluded.label_ids_json,
                        attachments_json = excluded.attachments_json,
                        provider_metadata_json = excluded.provider_metadata_json,
                        locator_json = excluded.locator_json,
                        freshness_identity = excluded.freshness_identity,
                        metadata_sha256 = excluded.metadata_sha256,
                        membership_state = 'active',
                        last_seen_run_id = excluded.last_seen_run_id,
                        last_seen_at = excluded.last_seen_at
                    """,
                    (
                        source_record_id,
                        binding_id,
                        record["external_id"],
                        record["parent_external_id"],
                        record["occurred_at"],
                        record["title"],
                        encode_json(record["participants"]),
                        encode_json(record["label_ids"]),
                        encode_json(record["attachments"]),
                        encode_json(record["provider_metadata"]),
                        encode_json(record["locator"]),
                        record["freshness_identity"],
                        record["metadata_sha256"],
                        run_id,
                        now,
                        now,
                    ),
                )
            removed_count = 0
            status = "incomplete"
            if complete:
                removed_count = connection.execute(
                    """
                    UPDATE external_source_records
                    SET membership_state = 'removed'
                    WHERE binding_id = ?
                      AND last_seen_run_id != ?
                      AND membership_state = 'active'
                    """,
                    (binding_id, run_id),
                ).rowcount
                connection.execute(
                    """
                    UPDATE external_source_runs
                    SET status = 'complete', completed_at = ?
                    WHERE run_id = ?
                    """,
                    (now, run_id),
                )
                updated_binding = connection.execute(
                    """
                    UPDATE corpus_source_bindings
                    SET last_complete_run_id = ?, last_complete_at = ?, updated_at = ?
                    WHERE binding_id = ?
                      AND last_complete_run_id IS ?
                    """,
                    (
                        run_id,
                        now,
                        now,
                        binding_id,
                        run["base_complete_run_id"]
                        if run is not None
                        else binding["last_complete_run_id"],
                    ),
                ).rowcount
                if updated_binding != 1:
                    raise ContextConflictError(
                        "linked source observation run is stale",
                        details={"reason": "stale_observation_run"},
                    )
                status = "complete"
            observed_in_run = connection.execute(
                """
                SELECT COUNT(*)
                FROM external_source_records
                WHERE binding_id = ? AND last_seen_run_id = ?
                """,
                (binding_id, run_id),
            ).fetchone()[0]
        return {
            "binding_id": binding_id,
            "corpus_id": corpus_id,
            "run_id": run_id,
            "status": status,
            "observed_in_run": observed_in_run,
            "removed_count": removed_count,
            "idempotent_replay": False,
        }

    def _refresh_source_records(
        self,
        *,
        corpus_id: str,
        binding_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if set(payload) != {"run_id"}:
            raise ContextValidationError(
                "linked source refresh payload fields are invalid",
                details={"required": ["run_id"]},
            )
        run_id = _require_session_identifier(
            payload["run_id"],
            field="run_id",
        )
        with context_read_connection(self.data_root) as connection:
            binding = connection.execute(
                "SELECT * FROM corpus_source_bindings WHERE binding_id = ?",
                (binding_id,),
            ).fetchone()
        if (
            binding is None
            or binding["corpus_id"] != corpus_id
            or binding["state"] != "active"
        ):
            raise ContextNotFoundError("linked source binding does not exist")
        provider_kind = binding["provider_kind"]
        if provider_kind not in SESSION_SOURCE_PROVIDERS:
            raise ContextValidationError(
                "provider source refresh is available only for local session records",
                details={
                    "provider_kind": provider_kind,
                    "allowed": sorted(SESSION_SOURCE_PROVIDERS),
                },
            )
        self._observe_source_records(
            corpus_id=corpus_id,
            binding_id=binding_id,
            payload={
                "run_id": run_id,
                "records": [],
                "complete": False,
            },
        )
        discovery = discover_session_records(
            provider_kind,
            _json_dict(binding["selector_json"]),
        )
        records = discovery["records"]
        pages = [
            records[index : index + CONTEXT_MAX_EXTERNAL_RECORDS_PER_UPDATE]
            for index in range(
                0,
                len(records),
                CONTEXT_MAX_EXTERNAL_RECORDS_PER_UPDATE,
            )
        ] or [[]]
        result = None
        for index, page in enumerate(pages):
            is_last = index == len(pages) - 1
            result = self._observe_source_records(
                corpus_id=corpus_id,
                binding_id=binding_id,
                payload={
                    "run_id": run_id,
                    "records": page,
                    "complete": bool(discovery["complete"] and is_last),
                },
            )
        assert result is not None
        return {
            **result,
            "provider_kind": provider_kind,
            "scanned_file_count": discovery["scanned_file_count"],
            "discovered_record_count": len(records),
            "provider_scan_complete": discovery["complete"],
            "issue_count": discovery["issue_count"],
            "issues": discovery["issues"],
            "issues_truncated": discovery["issues_truncated"],
        }

    def source_read(
        self,
        *,
        corpus_id: str,
        binding_id: str | None = None,
        record_state: str = "active",
        occurred_after: str | None = None,
        limit: int = CONTEXT_DEFAULT_LIMIT,
        offset: int = 0,
        audience: str = "local_cli",
    ) -> dict[str, Any]:
        _validate_audience(audience)
        _validate_limit_offset(limit, offset)
        if record_state not in {"active", "removed"}:
            raise ContextValidationError(
                "unsupported linked source record state",
                details={"state": record_state, "allowed": ["active", "removed"]},
            )
        corpus_id = normalize_corpus_id(corpus_id)
        get_corpus(self.data_root, corpus_id)
        if audience == "external_mcp":
            self._require_external_visibility([corpus_id])
        normalized_binding = (
            normalize_source_binding_id(binding_id)
            if binding_id is not None
            else None
        )
        normalized_occurred_after = _normalize_timestamp_filter(
            occurred_after,
            field="occurred_after",
        )
        if not self.database_path.exists():
            return {
                "corpus_id": corpus_id,
                "bindings": [],
                "record_state": record_state,
                "occurred_after": normalized_occurred_after,
                "observed_through": None,
                "offset": offset,
                "limit": limit,
                "returned_count": 0,
                "total_matching": 0,
                "has_more": False,
                "next_offset": None,
                "records": [],
            }
        with context_read_connection(self.data_root) as connection:
            binding_rows = connection.execute(
                """
                SELECT *
                FROM corpus_source_bindings
                WHERE corpus_id = ?
                ORDER BY binding_id
                """,
                (corpus_id,),
            ).fetchall()
            binding_by_id = {row["binding_id"]: row for row in binding_rows}
            if (
                normalized_binding is not None
                and normalized_binding not in binding_by_id
            ):
                raise ContextNotFoundError("linked source binding does not exist")
            selected_ids = (
                [normalized_binding]
                if normalized_binding is not None
                else list(binding_by_id)
            )
            if not selected_ids:
                total_matching = 0
                record_rows = []
            else:
                placeholders = ",".join("?" for _ in selected_ids)
                filters = [
                    f"binding_id IN ({placeholders})",
                    "membership_state = ?",
                ]
                filter_values: list[Any] = [*selected_ids, record_state]
                if normalized_occurred_after is not None:
                    filters.extend(
                        [
                            "occurred_at IS NOT NULL",
                            "julianday(occurred_at) > julianday(?)",
                        ]
                    )
                    filter_values.append(normalized_occurred_after)
                where_clause = " AND ".join(filters)
                total_matching = connection.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM external_source_records
                    WHERE {where_clause}
                    """,
                    filter_values,
                ).fetchone()[0]
                record_rows = connection.execute(
                    f"""
                    SELECT *
                    FROM external_source_records
                    WHERE {where_clause}
                    ORDER BY occurred_at DESC, external_id
                    LIMIT ? OFFSET ?
                    """,
                    (*filter_values, limit, offset),
                ).fetchall()
            count_rows = connection.execute(
                """
                SELECT binding_id, membership_state, COUNT(*) AS count
                FROM external_source_records
                WHERE binding_id IN (
                    SELECT binding_id
                    FROM corpus_source_bindings
                    WHERE corpus_id = ?
                )
                GROUP BY binding_id, membership_state
                """,
                (corpus_id,),
            ).fetchall()
        counts: dict[str, dict[str, int]] = defaultdict(dict)
        for row in count_rows:
            counts[row["binding_id"]][row["membership_state"]] = row["count"]
        bindings = []
        for row in binding_rows:
            bindings.append(
                {
                    "binding_id": row["binding_id"],
                    "corpus_id": row["corpus_id"],
                    "provider_kind": row["provider_kind"],
                    "selector": _json_dict(row["selector_json"]),
                    "state": row["state"],
                    "last_complete_run_id": row["last_complete_run_id"],
                    "last_complete_at": row["last_complete_at"],
                    "active_record_count": counts[row["binding_id"]].get("active", 0),
                    "removed_record_count": counts[row["binding_id"]].get("removed", 0),
                }
            )
        selected_complete_times = [
            binding_by_id[selected_id]["last_complete_at"]
            for selected_id in selected_ids
        ]
        observed_through = None
        if selected_complete_times and all(selected_complete_times):
            observed_through = min(
                selected_complete_times,
                key=lambda value: datetime.fromisoformat(
                    value.replace("Z", "+00:00")
                ),
            )
        records = [
            {
                "source_record_id": row["source_record_id"],
                "binding_id": row["binding_id"],
                "provider_kind": binding_by_id[row["binding_id"]][
                    "provider_kind"
                ],
                "external_id": row["external_id"],
                "parent_external_id": row["parent_external_id"],
                "occurred_at": row["occurred_at"],
                "title": row["title"],
                "participants": json.loads(row["participants_json"]),
                "label_ids": json.loads(row["label_ids_json"]),
                "attachments": json.loads(row["attachments_json"]),
                "provider_metadata": _json_dict(
                    row["provider_metadata_json"]
                ),
                "locator": _json_dict(row["locator_json"]),
                "freshness_identity": row["freshness_identity"],
                "freshness_state": "not_checked",
                "membership_state": row["membership_state"],
                "last_seen_run_id": row["last_seen_run_id"],
                "first_seen_at": row["first_seen_at"],
                "last_seen_at": row["last_seen_at"],
            }
            for row in record_rows
        ]
        next_offset = offset + len(records)
        response = {
            "corpus_id": corpus_id,
            "bindings": bindings,
            "record_state": record_state,
            "occurred_after": normalized_occurred_after,
            "observed_through": observed_through,
            "offset": offset,
            "limit": limit,
            "returned_count": len(records),
            "total_matching": total_matching,
            "has_more": next_offset < total_matching,
            "next_offset": next_offset if next_offset < total_matching else None,
            "records": records,
        }
        self._require_read_budget(response)
        return response

    def source_fetch(
        self,
        *,
        corpus_id: str,
        binding_id: str,
        external_id: str,
        max_chars: int = SESSION_SOURCE_FETCH_DEFAULT_CHARS,
        audience: str = "local_cli",
    ) -> dict[str, Any]:
        _validate_audience(audience)
        corpus_id = normalize_corpus_id(corpus_id)
        get_corpus(self.data_root, corpus_id)
        if audience == "external_mcp":
            self._require_external_visibility([corpus_id])
        binding_id = normalize_source_binding_id(binding_id)
        external_id = _require_string(
            external_id,
            field="external_id",
            maximum=CONTEXT_MAX_IDENTIFIER_CHARS,
        )
        if not self.database_path.exists():
            raise ContextNotFoundError("linked source record does not exist")
        with context_read_connection(self.data_root) as connection:
            row = connection.execute(
                """
                SELECT r.*, b.corpus_id, b.provider_kind,
                       b.selector_json, b.state AS binding_state
                FROM external_source_records r
                JOIN corpus_source_bindings b ON b.binding_id = r.binding_id
                WHERE r.binding_id = ? AND r.external_id = ?
                """,
                (binding_id, external_id),
            ).fetchone()
        if row is None or row["corpus_id"] != corpus_id:
            raise ContextNotFoundError("linked source record does not exist")
        provider_kind = row["provider_kind"]
        if provider_kind not in SESSION_SOURCE_PROVIDERS:
            raise ContextValidationError(
                "exact provider fetch is not available for this linked source",
                details={
                    "provider_kind": provider_kind,
                    "read_with": "provider_connector",
                },
            )
        if row["binding_state"] != "active":
            raise ContextConflictError(
                "linked source binding is archived",
                details={"reason": "binding_archived"},
            )
        result = fetch_session_record(
            provider_kind,
            _json_dict(row["selector_json"]),
            external_id=row["external_id"],
            provider_metadata=_json_dict(row["provider_metadata_json"]),
            locator=_json_dict(row["locator_json"]),
            expected_freshness_identity=row["freshness_identity"],
            max_chars=max_chars,
        )
        result["corpus_id"] = corpus_id
        result["binding_id"] = binding_id
        result["membership_state"] = row["membership_state"]
        self._require_read_budget(result)
        return result

    def _projection_uses_current_adapter(
        self,
        extension: str,
        adapter_id: str,
        adapter_version: str,
        config_hash: str,
    ) -> bool:
        try:
            descriptor = self.adapter_registry.resolve(extension).descriptor
        except ExtractionError:
            return False
        return (
            adapter_id,
            adapter_version,
            config_hash,
        ) == (
            descriptor.adapter_id,
            descriptor.adapter_version,
            descriptor.config_hash,
        )

    def _corpus_policies(self) -> dict[str, str]:
        try:
            corpora = list_corpora(self.data_root)
        except CorpusError:
            return {}
        return {corpus["corpus_id"]: corpus["execution_policy"] for corpus in corpora}

    def _visible_to_external_mcp(
        self,
        corpus_ids: list[str],
        *,
        policies: dict[str, str] | None = None,
    ) -> bool:
        if not corpus_ids:
            return False
        policies = policies if policies is not None else self._corpus_policies()
        return all(policies.get(corpus_id) == "external_host_allowed" for corpus_id in corpus_ids)

    def _require_external_visibility(self, corpus_ids: list[str]) -> None:
        if not self._visible_to_external_mcp(corpus_ids):
            raise ContextNotFoundError("context does not exist")

    def _context_corpus_ids(self, connection, context_id: str) -> list[str]:
        rows = connection.execute(
            """
            SELECT corpus_id
            FROM context_corpora
            WHERE context_id = ?
            ORDER BY corpus_id
            """,
            (context_id,),
        ).fetchall()
        return [row["corpus_id"] for row in rows]

    def _load_context(self, connection, context_id: str):
        row = connection.execute(
            "SELECT * FROM contexts WHERE context_id = ?",
            (context_id,),
        ).fetchone()
        if row is None:
            raise ContextNotFoundError("context does not exist")
        return row

    def _context_summary(
        self,
        row,
        *,
        corpus_ids: list[str],
    ) -> dict[str, Any]:
        return {
            "context_id": row["context_id"],
            "title": row["title"],
            "purpose": row["purpose"],
            "scope": _json_dict(row["scope_json"]),
            "corpus_ids": corpus_ids,
            "state": row["state"],
            "version": row["version"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list(
        self,
        *,
        state: str = "active",
        limit: int = CONTEXT_DEFAULT_LIMIT,
        offset: int = 0,
        audience: str = "local_cli",
    ) -> dict[str, Any]:
        _validate_audience(audience)
        if state not in CONTEXT_STATES:
            raise ContextValidationError(
                "unsupported context state",
                details={"state": state, "allowed": sorted(CONTEXT_STATES)},
            )
        _validate_limit_offset(limit, offset)
        if not self.database_path.exists():
            return {
                "state": state,
                "offset": offset,
                "limit": limit,
                "returned_count": 0,
                "total_matching": 0,
                "has_more": False,
                "next_offset": None,
                "contexts": [],
            }

        with context_read_connection(self.data_root) as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM contexts
                WHERE state = ?
                ORDER BY updated_at DESC, context_id
                """,
                (state,),
            ).fetchall()
            corpora_rows = connection.execute(
                """
                SELECT context_id, corpus_id
                FROM context_corpora
                ORDER BY context_id, corpus_id
                """
            ).fetchall()
        by_context: dict[str, list[str]] = defaultdict(list)
        for corpus_row in corpora_rows:
            by_context[corpus_row["context_id"]].append(corpus_row["corpus_id"])

        policies = self._corpus_policies() if audience == "external_mcp" else {}
        visible_rows = []
        for row in rows:
            corpus_ids = by_context.get(row["context_id"], [])
            if audience == "external_mcp" and not self._visible_to_external_mcp(
                corpus_ids,
                policies=policies,
            ):
                continue
            visible_rows.append((row, corpus_ids))

        total_matching = len(visible_rows)
        page = visible_rows[offset : offset + limit]
        contexts = [self._context_summary(row, corpus_ids=corpus_ids) for row, corpus_ids in page]
        next_offset = offset + len(contexts)
        response = {
            "state": state,
            "offset": offset,
            "limit": limit,
            "returned_count": len(contexts),
            "total_matching": total_matching,
            "has_more": next_offset < total_matching,
            "next_offset": next_offset if next_offset < total_matching else None,
            "contexts": contexts,
        }
        self._require_read_budget(response)
        return response

    def read(
        self,
        *,
        context_id: str | None = None,
        state: str = "active",
        include_history: bool = False,
        limit: int = CONTEXT_DEFAULT_LIMIT,
        offset: int = 0,
        audience: str = "local_cli",
        view: str = "restricted",
    ) -> dict[str, Any]:
        _validate_audience(audience)
        _validate_view(view)
        if view == "general":
            return self._read_general(
                context_id=context_id,
                state=state,
                include_history=include_history,
                limit=limit,
                offset=offset,
                audience=audience,
            )
        if context_id is None:
            response = self.list(
                state=state,
                limit=limit,
                offset=offset,
                audience=audience,
            )
            response["view"] = "restricted"
            return response
        if state not in CONTEXT_STATES:
            raise ContextValidationError(
                "unsupported context state",
                details={"state": state, "allowed": sorted(CONTEXT_STATES)},
            )
        _validate_limit_offset(limit, offset)
        normalized_id = normalize_context_id(context_id)
        if not self.database_path.exists():
            raise ContextNotFoundError("context does not exist")

        with context_read_connection(self.data_root) as connection:
            context_row = self._load_context(connection, normalized_id)
            corpus_rows = connection.execute(
                """
                SELECT *
                FROM context_corpora
                WHERE context_id = ?
                ORDER BY corpus_id
                """,
                (normalized_id,),
            ).fetchall()
            corpus_ids = [row["corpus_id"] for row in corpus_rows]
            if audience == "external_mcp":
                self._require_external_visibility(corpus_ids)
            if context_row["state"] != state:
                raise ContextNotFoundError("context does not exist")

            lifecycle_clause = "" if include_history else "AND lifecycle_state = 'active'"
            total_matching = connection.execute(
                f"""
                SELECT COUNT(*)
                FROM context_items
                WHERE context_id = ? {lifecycle_clause}
                """,
                (normalized_id,),
            ).fetchone()[0]
            item_rows = connection.execute(
                f"""
                SELECT *
                FROM context_items
                WHERE context_id = ? {lifecycle_clause}
                ORDER BY created_at, item_id
                LIMIT ? OFFSET ?
                """,
                (normalized_id, limit, offset),
            ).fetchall()
            item_ids = [row["item_id"] for row in item_rows]
            source_rows = []
            external_source_rows = []
            if item_ids:
                placeholders = ",".join("?" for _ in item_ids)
                source_rows = connection.execute(
                    f"""
                    SELECT *
                    FROM context_sources
                    WHERE item_id IN ({placeholders})
                    ORDER BY item_id, source_ref_id
                    """,
                    item_ids,
                ).fetchall()
                external_source_rows = connection.execute(
                    f"""
                    SELECT *
                    FROM context_external_sources
                    WHERE item_id IN ({placeholders})
                    ORDER BY item_id, source_ref_id
                    """,
                    item_ids,
                ).fetchall()

        sources_by_item: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for source_row in source_rows:
            source = dict(source_row)
            source["source_span"] = _json_dict(source.pop("source_span_json"))
            observation = self._observe_source(source)
            observation.pop("source_span", None)
            source.update(observation)
            sources_by_item[source["item_id"]].append(source)

        external_sources_by_item: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for source_row in external_source_rows:
            source = dict(source_row)
            observation = self._observe_external_source(source)
            source.update(observation)
            source.pop("metadata_sha256", None)
            source.pop("observed_metadata_sha256", None)
            external_sources_by_item[source["item_id"]].append(source)

        items = []
        for item_row in item_rows:
            item = dict(item_row)
            item.pop("input_sha256", None)
            item["attributes"] = _json_dict(item.pop("attributes_json"))
            item["sources"] = sources_by_item.get(item["item_id"], [])
            item["external_sources"] = external_sources_by_item.get(
                item["item_id"],
                [],
            )
            items.append(item)

        observations = []
        for corpus_row in corpus_rows:
            checkpoint = dict(corpus_row)
            observation = self._current_corpus_observation(corpus_row["corpus_id"])
            change = self._inventory_change(checkpoint, observation)
            observations.append(
                {
                    "corpus_id": corpus_row["corpus_id"],
                    "checkpoint": {
                        "scan_id": corpus_row["last_checked_scan_id"],
                        "snapshot_id": corpus_row["last_checked_snapshot_id"],
                        "inventory_hash": corpus_row["last_checked_inventory_hash"],
                        "checked_at": corpus_row["last_checked_at"],
                    },
                    "current": observation,
                    "inventory_change": change,
                }
            )

        next_offset = offset + len(items)
        response = {
            "view": "restricted",
            "context": self._context_summary(context_row, corpus_ids=corpus_ids),
            "include_history": include_history,
            "offset": offset,
            "limit": limit,
            "returned_count": len(items),
            "total_matching": total_matching,
            "has_more": next_offset < total_matching,
            "next_offset": next_offset if next_offset < total_matching else None,
            "items": items,
            "corpus_observations": observations,
        }
        self._require_read_budget(response)
        return response

    def lifecycle_state(self, context_id: str) -> str:
        """Return the persisted lifecycle state for an internal binding check."""

        normalized_id = normalize_context_id(context_id)
        if not self.database_path.exists():
            raise ContextNotFoundError("context does not exist")
        with context_read_connection(self.data_root) as connection:
            row = connection.execute(
                "SELECT state FROM contexts WHERE context_id = ?",
                (normalized_id,),
            ).fetchone()
        if row is None:
            raise ContextNotFoundError("context does not exist")
        return str(row["state"])

    def _read_general(
        self,
        *,
        context_id: str | None,
        state: str,
        include_history: bool,
        limit: int,
        offset: int,
        audience: str,
    ) -> dict[str, Any]:
        if state != "active":
            raise ContextValidationError("general context view supports only active releases")
        if include_history:
            raise ContextValidationError("general context view does not expose release history")
        _validate_limit_offset(limit, offset)
        if not self.database_path.exists():
            if context_id is not None:
                raise ContextNotFoundError("general collection does not exist")
            return {
                "view": "general",
                "offset": offset,
                "limit": limit,
                "returned_count": 0,
                "total_matching": 0,
                "has_more": False,
                "next_offset": None,
                "collections": [],
            }

        with context_read_connection(self.data_root) as connection:
            if context_id is None:
                rows = connection.execute(
                    """
                    SELECT m.public_collection_id, m.public_title,
                           m.public_purpose
                    FROM context_release_manifests m
                    JOIN contexts c ON c.context_id = m.context_id
                    WHERE m.state = 'active' AND c.state = 'active'
                    ORDER BY m.public_title, m.public_collection_id
                    """
                ).fetchall()
                total_matching = len(rows)
                page = rows[offset : offset + limit]
                collections = [
                    {
                        "public_collection_id": row["public_collection_id"],
                        "public_title": row["public_title"],
                        "public_purpose": row["public_purpose"],
                    }
                    for row in page
                ]
                next_offset = offset + len(collections)
                response = {
                    "view": "general",
                    "offset": offset,
                    "limit": limit,
                    "returned_count": len(collections),
                    "total_matching": total_matching,
                    "has_more": next_offset < total_matching,
                    "next_offset": next_offset if next_offset < total_matching else None,
                    "collections": collections,
                }
                self._require_read_budget(response)
                return response

            lookup_id = _require_string(
                context_id,
                field="context_id",
                maximum=CONTEXT_MAX_IDENTIFIER_CHARS,
            )
            if audience == "external_mcp":
                manifest = connection.execute(
                    """
                    SELECT m.*
                    FROM context_release_manifests m
                    JOIN contexts c ON c.context_id = m.context_id
                    WHERE m.state = 'active' AND c.state = 'active'
                      AND m.public_collection_id = ?
                    """,
                    (lookup_id,),
                ).fetchone()
            else:
                manifest = connection.execute(
                    """
                    SELECT m.*
                    FROM context_release_manifests m
                    JOIN contexts c ON c.context_id = m.context_id
                    WHERE m.state = 'active' AND c.state = 'active'
                      AND m.public_collection_id = ?
                    """,
                    (lookup_id,),
                ).fetchone()
                if manifest is None:
                    manifest = connection.execute(
                        """
                        SELECT m.*
                        FROM context_release_manifests m
                        JOIN contexts c ON c.context_id = m.context_id
                        WHERE m.state = 'active' AND c.state = 'active'
                          AND m.context_id = ?
                        """,
                        (lookup_id,),
                    ).fetchone()
            if manifest is None:
                raise ContextNotFoundError("general collection does not exist")
            item_rows = connection.execute(
                """
                SELECT ri.public_id, ri.position, i.item_id, i.kind, i.body_text,
                       i.disclosure_state, i.lifecycle_state
                FROM context_release_items ri
                JOIN context_items i ON i.item_id = ri.item_id
                WHERE ri.release_id = ?
                ORDER BY ri.position
                """,
                (manifest["release_id"],),
            ).fetchall()
            item_ids = [row["item_id"] for row in item_rows]
            source_rows = []
            if item_ids:
                placeholders = ",".join("?" for _ in item_ids)
                source_rows = connection.execute(
                    f"""
                    SELECT *
                    FROM context_sources
                    WHERE item_id IN ({placeholders})
                    ORDER BY item_id, source_ref_id
                    """,
                    item_ids,
                ).fetchall()

        sources_by_item: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in source_rows:
            sources_by_item[row["item_id"]].append(dict(row))

        available_items = []
        for row in item_rows:
            if row["lifecycle_state"] != "active":
                continue
            sources = sources_by_item.get(row["item_id"], [])
            eligible = (
                row["disclosure_state"] == "general_candidate"
                and row["kind"] in GENERAL_CANDIDATE_KINDS
                and bool(sources)
                and all(source["link_role"] == "direct" for source in sources)
            )
            if eligible:
                for source in sources:
                    observation = self._observe_source(source)
                    if observation["dependency_state"] != "valid":
                        eligible = False
                        break
            if not eligible:
                continue
            available_items.append(
                {
                    "public_id": row["public_id"],
                    "kind": row["kind"],
                    "body_text": row["body_text"],
                }
            )

        total_matching = len(available_items)
        items = available_items[offset : offset + limit]
        next_offset = offset + len(items)
        response = {
            "view": "general",
            "collection": {
                "public_collection_id": manifest["public_collection_id"],
                "public_title": manifest["public_title"],
                "public_purpose": manifest["public_purpose"],
            },
            "offset": offset,
            "limit": limit,
            "returned_count": len(items),
            "total_matching": total_matching,
            "has_more": next_offset < total_matching,
            "next_offset": next_offset if next_offset < total_matching else None,
            "items": items,
        }
        self._require_read_budget(response)
        return response

    def update(
        self,
        *,
        action: str,
        context_id: str,
        expected_version: int,
        payload: dict[str, Any],
        confirm_persistent_context_write: bool,
        confirm_general_release_approval: bool = False,
        audience: str = "local_cli",
    ) -> dict[str, Any]:
        _validate_audience(audience)
        if action not in {
            "create",
            "append",
            "supersede",
            "advance_checkpoint",
            "approve_general",
            "archive",
        }:
            raise ContextValidationError(
                "unsupported context update action",
                details={"action": action},
            )
        normalized_id = normalize_context_id(context_id)
        if audience == "external_mcp" and self.database_path.exists():
            with context_read_connection(self.data_root) as connection:
                if action == "create":
                    existing = connection.execute(
                        "SELECT 1 FROM contexts WHERE context_id = ?",
                        (normalized_id,),
                    ).fetchone()
                    if existing is not None:
                        corpus_ids = self._context_corpus_ids(
                            connection,
                            normalized_id,
                        )
                        self._require_external_visibility(corpus_ids)
                else:
                    self._load_context(connection, normalized_id)
                    corpus_ids = self._context_corpus_ids(
                        connection,
                        normalized_id,
                    )
                    self._require_external_visibility(corpus_ids)
        if type(expected_version) is not int or not 0 <= expected_version <= (1 << 63) - 1:
            raise ContextValidationError("context expected_version is outside the supported range")
        if confirm_persistent_context_write is not True:
            raise ContextValidationError("persistent context update requires explicit confirmation")
        if (
            action == "approve_general"
            and confirm_general_release_approval is not True
        ):
            raise ContextValidationError(
                "general release approval requires explicit user confirmation"
            )
        if not isinstance(payload, dict):
            raise ContextValidationError("context update payload must be an object")
        try:
            payload_bytes = encode_json(payload).encode()
        except (TypeError, ValueError) as exc:
            raise ContextValidationError("context update payload must be JSON-compatible") from exc
        if len(payload_bytes) > CONTEXT_MAX_UPDATE_BYTES:
            raise BudgetExceededError(
                "context update payload exceeds the serialized budget",
                details={
                    "serialized_bytes": len(payload_bytes),
                    "maximum_bytes": CONTEXT_MAX_UPDATE_BYTES,
                },
            )
        if action == "create":
            return self._create(
                normalized_id,
                expected_version=expected_version,
                payload=payload,
                audience=audience,
            )
        if not self.database_path.exists():
            raise ContextNotFoundError("context does not exist")
        if action in {"append", "supersede"}:
            return self._write_items(
                normalized_id,
                action=action,
                expected_version=expected_version,
                payload=payload,
            )
        if action == "advance_checkpoint":
            return self._advance_checkpoint(
                normalized_id,
                expected_version=expected_version,
                payload=payload,
            )
        if action == "approve_general":
            return self._approve_general(
                normalized_id,
                expected_version=expected_version,
                payload=payload,
            )
        return self._archive(
            normalized_id,
            expected_version=expected_version,
            payload=payload,
        )

    def _create(
        self,
        context_id: str,
        *,
        expected_version: int,
        payload: dict[str, Any],
        audience: str,
    ) -> dict[str, Any]:
        if expected_version != 0:
            raise ContextConflictError(
                "context create requires expected_version 0",
                details={
                    "reason": "version_mismatch",
                    "expected_version": expected_version,
                },
            )
        unknown = set(payload) - _CREATE_KEYS
        if unknown or set(payload) != _CREATE_KEYS:
            raise ContextValidationError(
                "context create payload fields are invalid",
                details={
                    "required": sorted(_CREATE_KEYS),
                    "unknown": sorted(unknown),
                },
            )
        title = _require_string(
            payload["title"],
            field="title",
            maximum=CONTEXT_MAX_TITLE_CHARS,
        )
        purpose = _require_string(
            payload["purpose"],
            field="purpose",
            maximum=CONTEXT_MAX_PURPOSE_CHARS,
        )
        scope = _require_json_object(
            payload["scope"],
            field="scope",
            maximum_bytes=CONTEXT_MAX_SCOPE_BYTES,
        )
        raw_corpus_ids = payload["corpus_ids"]
        if (
            not isinstance(raw_corpus_ids, list)
            or not 1 <= len(raw_corpus_ids) <= CONTEXT_MAX_CORPORA
            or any(not isinstance(value, str) for value in raw_corpus_ids)
        ):
            raise ContextValidationError("context corpus_ids must be a non-empty bounded list")
        corpus_ids = sorted({normalize_corpus_id(value) for value in raw_corpus_ids})
        if len(corpus_ids) != len(raw_corpus_ids):
            raise ContextValidationError("context corpus_ids must be unique")
        corpora = [get_corpus(self.data_root, corpus_id) for corpus_id in corpus_ids]
        if audience == "external_mcp" and any(
            corpus["execution_policy"] != "external_host_allowed" for corpus in corpora
        ):
            raise PolicyDeniedError("one or more corpora do not permit MCP context persistence")

        now = utc_now()
        scope_json = encode_json(scope)
        with (
            context_writer_lock(self.data_root),
            context_connection(self.data_root) as connection,
        ):
            existing = connection.execute(
                "SELECT * FROM contexts WHERE context_id = ?",
                (context_id,),
            ).fetchone()
            if existing is not None:
                existing_corpora = self._context_corpus_ids(connection, context_id)
                if (
                    existing["title"] == title
                    and existing["purpose"] == purpose
                    and existing["scope_json"] == scope_json
                    and existing_corpora == corpus_ids
                ):
                    result = self._context_summary(
                        existing,
                        corpus_ids=existing_corpora,
                    )
                    result["idempotent_replay"] = True
                    return result
                raise ContextConflictError(
                    "context id already exists with different create input",
                    details={"reason": "context_id_payload_mismatch"},
                )
            connection.execute(
                """
                    INSERT INTO contexts(
                        context_id, title, purpose, scope_json, state,
                        version, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'active', 1, ?, ?)
                    """,
                (context_id, title, purpose, scope_json, now, now),
            )
            connection.executemany(
                """
                    INSERT INTO context_corpora(context_id, corpus_id)
                    VALUES (?, ?)
                    """,
                [(context_id, corpus_id) for corpus_id in corpus_ids],
            )
            created = connection.execute(
                "SELECT * FROM contexts WHERE context_id = ?",
                (context_id,),
            ).fetchone()
        result = self._context_summary(created, corpus_ids=corpus_ids)
        result["idempotent_replay"] = False
        return result

    def _normalize_source(
        self,
        value: object,
        *,
        context_corpus_ids: set[str],
    ) -> dict[str, Any]:
        if not isinstance(value, dict) or set(value) != _SOURCE_KEYS:
            raise ContextValidationError(
                "context source fields are invalid",
                details={"required": sorted(_SOURCE_KEYS)},
            )
        source = {
            field: _require_string(
                value[field],
                field=f"source.{field}",
                maximum=CONTEXT_MAX_IDENTIFIER_CHARS,
            )
            for field in _SOURCE_KEYS
        }
        source["corpus_id"] = normalize_corpus_id(source["corpus_id"])
        if source["corpus_id"] not in context_corpus_ids:
            raise ContextValidationError(
                "context source corpus is outside the context scope",
                details={"corpus_id": source["corpus_id"]},
            )
        if source["link_role"] not in CONTEXT_SOURCE_ROLES:
            raise ContextValidationError(
                "unsupported context source role",
                details={
                    "link_role": source["link_role"],
                    "allowed": sorted(CONTEXT_SOURCE_ROLES),
                },
            )
        return source

    def _normalize_external_source(
        self,
        value: object,
        *,
        context_corpus_ids: set[str],
    ) -> dict[str, Any]:
        if not isinstance(value, dict) or set(value) != _EXTERNAL_SOURCE_KEYS:
            raise ContextValidationError(
                "context external source fields are invalid",
                details={"required": sorted(_EXTERNAL_SOURCE_KEYS)},
            )
        source = {
            field: _require_string(
                value[field],
                field=f"external_source.{field}",
                maximum=CONTEXT_MAX_IDENTIFIER_CHARS,
            )
            for field in _EXTERNAL_SOURCE_KEYS
        }
        source["corpus_id"] = normalize_corpus_id(source["corpus_id"])
        source["binding_id"] = normalize_source_binding_id(source["binding_id"])
        if source["corpus_id"] not in context_corpus_ids:
            raise ContextValidationError(
                "context external source corpus is outside the context scope",
                details={"corpus_id": source["corpus_id"]},
            )
        if source["link_role"] not in CONTEXT_SOURCE_ROLES:
            raise ContextValidationError(
                "unsupported context external source role",
                details={
                    "link_role": source["link_role"],
                    "allowed": sorted(CONTEXT_SOURCE_ROLES),
                },
            )
        return source

    def _normalize_item(
        self,
        value: object,
        *,
        action: str,
        context_corpus_ids: set[str],
    ) -> tuple[
        dict[str, Any],
        list[dict[str, Any]],
        list[dict[str, Any]],
    ]:
        if not isinstance(value, dict):
            raise ContextValidationError("context item must be an object")
        unknown = set(value) - _ITEM_KEYS
        if unknown:
            raise ContextValidationError(
                "context item contains unsupported fields",
                details={"unknown": sorted(unknown)},
            )
        client_ref = _require_string(
            value.get("client_ref"),
            field="client_ref",
            maximum=CONTEXT_MAX_IDENTIFIER_CHARS,
        )
        kind = _require_string(
            value.get("kind"),
            field="kind",
            maximum=32,
        )
        if kind not in CONTEXT_ITEM_KINDS:
            raise ContextValidationError(
                "unsupported context item kind",
                details={"kind": kind, "allowed": sorted(CONTEXT_ITEM_KINDS)},
            )
        body_text = _require_string(
            value.get("body_text"),
            field="body_text",
            maximum=CONTEXT_MAX_BODY_CHARS,
        )
        attributes = _require_json_object(
            value.get("attributes", {}),
            field="attributes",
            maximum_bytes=CONTEXT_MAX_ATTRIBUTES_BYTES,
        )
        disclosure_state = _require_string(
            value.get("disclosure_state", "restricted"),
            field="disclosure_state",
            maximum=32,
        )
        if disclosure_state not in CONTEXT_DISCLOSURE_STATES:
            raise ContextValidationError(
                "unsupported context item disclosure state",
                details={
                    "disclosure_state": disclosure_state,
                    "allowed": sorted(CONTEXT_DISCLOSURE_STATES),
                },
            )
        raw_sources = value.get("sources", [])
        if not isinstance(raw_sources, list):
            raise ContextValidationError("context item sources must be a list")
        sources = [
            self._normalize_source(
                source,
                context_corpus_ids=context_corpus_ids,
            )
            for source in raw_sources
        ]
        raw_external_sources = value.get("external_sources", [])
        if not isinstance(raw_external_sources, list):
            raise ContextValidationError(
                "context item external_sources must be a list"
            )
        external_sources = [
            self._normalize_external_source(
                source,
                context_corpus_ids=context_corpus_ids,
            )
            for source in raw_external_sources
        ]
        source_keys = {(source["corpus_id"], source["source_unit_id"]) for source in sources}
        if len(source_keys) != len(sources):
            raise ContextValidationError("context item source units must be unique within an item")
        external_source_keys = {
            (
                source["corpus_id"],
                source["binding_id"],
                source["external_id"],
            )
            for source in external_sources
        }
        if len(external_source_keys) != len(external_sources):
            raise ContextValidationError(
                "context item external sources must be unique within an item"
            )
        if kind in {"finding", "relationship", "difference"} and not any(
            source["link_role"] == "direct"
            for source in [*sources, *external_sources]
        ):
            raise ContextValidationError(
                "source-linked context item requires a direct source",
                details={"kind": kind},
            )
        if disclosure_state == "general_candidate" and (
            kind not in GENERAL_CANDIDATE_KINDS
            or not sources
            or external_sources
            or any(source["link_role"] != "direct" for source in sources)
        ):
            raise ContextValidationError(
                "general candidate must be a source-linked finding, relationship, or difference "
                "with only direct sources",
                details={
                    "kind": kind,
                    "allowed_kinds": sorted(GENERAL_CANDIDATE_KINDS),
                },
            )
        supersedes_item_id = value.get("supersedes_item_id")
        if action == "supersede":
            supersedes_item_id = _require_string(
                supersedes_item_id,
                field="supersedes_item_id",
                maximum=CONTEXT_MAX_IDENTIFIER_CHARS,
            )
        elif supersedes_item_id is not None:
            raise ContextValidationError("append item may not include supersedes_item_id")

        canonical = {
            "client_ref": client_ref,
            "kind": kind,
            "body_text": body_text,
            "attributes": attributes,
            "disclosure_state": disclosure_state,
            "sources": sources,
            "external_sources": external_sources,
            "supersedes_item_id": supersedes_item_id,
        }
        previous_canonical = dict(canonical)
        previous_canonical.pop("external_sources")
        legacy_canonical = dict(previous_canonical)
        legacy_canonical.pop("disclosure_state")
        item = {
            **canonical,
            "input_sha256": hashlib.sha256(encode_json(canonical).encode()).hexdigest(),
            "previous_input_sha256": hashlib.sha256(
                encode_json(previous_canonical).encode()
            ).hexdigest(),
            "legacy_input_sha256": hashlib.sha256(
                encode_json(legacy_canonical).encode()
            ).hexdigest(),
        }
        return item, sources, external_sources

    def _write_items(
        self,
        context_id: str,
        *,
        action: str,
        expected_version: int,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if set(payload) != {"items"} or not isinstance(payload["items"], list):
            raise ContextValidationError(
                "context item update payload must contain only an items list"
            )
        if not 1 <= len(payload["items"]) <= CONTEXT_MAX_ITEMS_PER_UPDATE:
            raise BudgetExceededError(
                "context item update count is outside the supported range",
                details={"maximum_items": CONTEXT_MAX_ITEMS_PER_UPDATE},
            )

        with (
            context_writer_lock(self.data_root),
            context_connection(self.data_root) as connection,
        ):
            context_row = self._load_context(connection, context_id)
            corpus_ids = self._context_corpus_ids(connection, context_id)
            normalized = [
                self._normalize_item(
                    value,
                    action=action,
                    context_corpus_ids=set(corpus_ids),
                )
                for value in payload["items"]
            ]
            items = [item for item, _sources, _external_sources in normalized]
            total_sources = sum(
                len(sources) + len(external_sources)
                for _item, sources, external_sources in normalized
            )
            if total_sources > CONTEXT_MAX_SOURCES_PER_UPDATE:
                raise BudgetExceededError(
                    "context source link count exceeds the update budget",
                    details={"maximum_sources": CONTEXT_MAX_SOURCES_PER_UPDATE},
                )
            client_refs = [item["client_ref"] for item in items]
            if len(set(client_refs)) != len(client_refs):
                raise ContextValidationError(
                    "context item client_ref values must be unique per update"
                )

            placeholders = ",".join("?" for _ in client_refs)
            existing_rows = connection.execute(
                f"""
                    SELECT client_ref, input_sha256
                    FROM context_items
                    WHERE context_id = ? AND client_ref IN ({placeholders})
                    """,
                (context_id, *client_refs),
            ).fetchall()
            existing = {row["client_ref"]: row["input_sha256"] for row in existing_rows}
            accepted_hashes = {
                item["client_ref"]: {
                    item["input_sha256"],
                    *(
                        [item["previous_input_sha256"]]
                        if not item["external_sources"]
                        else []
                    ),
                    *(
                        [item["legacy_input_sha256"]]
                        if (
                            item["disclosure_state"] == "restricted"
                            and not item["external_sources"]
                        )
                        else []
                    ),
                }
                for item in items
            }
            if existing:
                if len(existing) == len(items) and all(
                    existing[item["client_ref"]] in accepted_hashes[item["client_ref"]]
                    for item in items
                ):
                    return {
                        "context_id": context_id,
                        "version": context_row["version"],
                        "idempotent_replay": True,
                        "client_refs": client_refs,
                    }
                reason = (
                    "client_ref_payload_mismatch"
                    if any(
                        existing.get(item["client_ref"])
                        not in {None, *accepted_hashes[item["client_ref"]]}
                        for item in items
                    )
                    else "partial_client_ref_reuse"
                )
                raise ContextConflictError(
                    "context client_ref conflicts with existing items",
                    details={"reason": reason},
                )
            if context_row["state"] != "active":
                raise ContextConflictError(
                    "archived context cannot be updated",
                    details={"reason": "context_archived"},
                )
            if context_row["version"] != expected_version:
                raise ContextConflictError(
                    "context version is not current",
                    details={
                        "reason": "version_mismatch",
                        "expected_version": expected_version,
                        "current_version": context_row["version"],
                    },
                )

            if action == "supersede":
                target_ids = [item["supersedes_item_id"] for item in items]
                if len(set(target_ids)) != len(target_ids):
                    raise ContextValidationError("supersede targets must be unique per update")
                target_placeholders = ",".join("?" for _ in target_ids)
                target_rows = connection.execute(
                    f"""
                        SELECT item_id, lifecycle_state
                        FROM context_items
                        WHERE context_id = ? AND item_id IN ({target_placeholders})
                        """,
                    (context_id, *target_ids),
                ).fetchall()
                targets = {row["item_id"]: row["lifecycle_state"] for row in target_rows}
                if any(targets.get(target_id) != "active" for target_id in target_ids):
                    raise ContextConflictError(
                        "supersede target is missing or no longer active",
                        details={"reason": "supersede_target_not_active"},
                    )

            observed_sources: list[
                tuple[list[dict[str, Any]], list[dict[str, Any]]]
            ] = []
            for _item, sources, external_sources in normalized:
                item_observations = []
                for source in sources:
                    observation = self._observe_source(source, strict=True)
                    if source["link_role"] in {"direct", "context"} and (
                        observation["dependency_state"] != "valid"
                    ):
                        raise ContextConflictError(
                            "context source is not current",
                            details={
                                "reason": "source_not_current",
                                "corpus_id": source["corpus_id"],
                                "source_unit_id": source["source_unit_id"],
                                "dependency_state": observation["dependency_state"],
                            },
                        )
                    item_observations.append(observation)
                external_observations = []
                for source in external_sources:
                    observation = self._observe_external_source(
                        source,
                        connection=connection,
                        strict=True,
                    )
                    if source["link_role"] in {"direct", "context"} and (
                        observation["dependency_state"] != "valid"
                    ):
                        raise ContextConflictError(
                            "context external source is not current",
                            details={
                                "reason": "external_source_not_current",
                                "corpus_id": source["corpus_id"],
                                "binding_id": source["binding_id"],
                                "external_id": source["external_id"],
                                "dependency_state": observation["dependency_state"],
                            },
                        )
                    external_observations.append(observation)
                observed_sources.append(
                    (item_observations, external_observations)
                )

            now = utc_now()
            inserted = []
            if action == "supersede":
                # A selected item cannot change under an existing general-view approval.
                target_placeholders = ",".join("?" for _ in target_ids)
                connection.execute(
                    f"""
                        UPDATE context_release_manifests
                        SET state = 'superseded'
                        WHERE context_id = ? AND state = 'active'
                          AND release_id IN (
                              SELECT release_id
                              FROM context_release_items
                              WHERE item_id IN ({target_placeholders})
                          )
                        """,
                    (context_id, *target_ids),
                )
            for (item, sources, external_sources), observations in zip(
                normalized,
                observed_sources,
                strict=True,
            ):
                source_observations, external_observations = observations
                if action == "supersede":
                    item_id = item["supersedes_item_id"]
                    connection.execute(
                        "DELETE FROM context_sources WHERE item_id = ?",
                        (item_id,),
                    )
                    connection.execute(
                        "DELETE FROM context_external_sources WHERE item_id = ?",
                        (item_id,),
                    )
                    connection.execute(
                        """
                        UPDATE context_items
                        SET client_ref = ?, input_sha256 = ?, kind = ?, body_text = ?,
                            attributes_json = ?, disclosure_state = ?,
                            lifecycle_state = 'active', supersedes_item_id = NULL
                        WHERE context_id = ? AND item_id = ?
                        """,
                        (
                            item["client_ref"],
                            item["input_sha256"],
                            item["kind"],
                            item["body_text"],
                            encode_json(item["attributes"]),
                            item["disclosure_state"],
                            context_id,
                            item_id,
                        ),
                    )
                else:
                    item_id = f"ctxi_{uuid.uuid4().hex}"
                    connection.execute(
                        """
                        INSERT INTO context_items(
                            item_id, context_id, client_ref, input_sha256, kind,
                            body_text, attributes_json, disclosure_state,
                            lifecycle_state,
                            supersedes_item_id, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
                        """,
                        (
                            item_id,
                            context_id,
                            item["client_ref"],
                            item["input_sha256"],
                            item["kind"],
                            item["body_text"],
                            encode_json(item["attributes"]),
                            item["disclosure_state"],
                            item["supersedes_item_id"],
                            now,
                        ),
                    )
                for source, observation in zip(
                    sources,
                    source_observations,
                    strict=True,
                ):
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
                            item_id,
                            source["corpus_id"],
                            source["snapshot_id"],
                            source["document_id"],
                            source["revision_id"],
                            source["projection_id"],
                            source["source_unit_id"],
                            source["link_role"],
                            encode_json(observation["source_span"]),
                        ),
                    )
                for source, observation in zip(
                    external_sources,
                    external_observations,
                    strict=True,
                ):
                    connection.execute(
                        """
                            INSERT INTO context_external_sources(
                                source_ref_id, item_id, corpus_id, binding_id,
                                source_record_id, link_role,
                                observed_metadata_sha256
                            ) VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                        (
                            f"ctxx_{uuid.uuid4().hex}",
                            item_id,
                            source["corpus_id"],
                            source["binding_id"],
                            observation["source_record_id"],
                            source["link_role"],
                            observation["metadata_sha256"],
                        ),
                    )
                inserted.append(
                    {
                        "item_id": item_id,
                        "client_ref": item["client_ref"],
                        "kind": item["kind"],
                        "disclosure_state": item["disclosure_state"],
                    }
                )
            new_version = context_row["version"] + 1
            connection.execute(
                """
                    UPDATE contexts
                    SET version = ?, updated_at = ?
                    WHERE context_id = ?
                    """,
                (new_version, now, context_id),
            )
        return {
            "context_id": context_id,
            "version": new_version,
            "idempotent_replay": False,
            "items": inserted,
        }

    def _approve_general(
        self,
        context_id: str,
        *,
        expected_version: int,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if set(payload) != _GENERAL_RELEASE_KEYS:
            raise ContextValidationError(
                "general release payload fields are invalid",
                details={"required": sorted(_GENERAL_RELEASE_KEYS)},
            )
        raw_item_ids = payload["item_ids"]
        if (
            not isinstance(raw_item_ids, list)
            or not 1 <= len(raw_item_ids) <= CONTEXT_MAX_LIMIT
        ):
            raise BudgetExceededError(
                "general release item count is outside the supported range",
                details={"maximum_items": CONTEXT_MAX_LIMIT},
            )
        item_ids = [
            _require_string(
                item_id,
                field="item_ids",
                maximum=CONTEXT_MAX_IDENTIFIER_CHARS,
            )
            for item_id in raw_item_ids
        ]
        if len(set(item_ids)) != len(item_ids):
            raise ContextValidationError("general release item_ids must be unique")
        public_title = _require_string(
            payload["public_title"],
            field="public_title",
            maximum=CONTEXT_MAX_TITLE_CHARS,
        )
        public_purpose = _require_string(
            payload["public_purpose"],
            field="public_purpose",
            maximum=CONTEXT_MAX_PURPOSE_CHARS,
        )
        review = _require_json_object(
            payload["review"],
            field="review",
            maximum_bytes=CONTEXT_MAX_ATTRIBUTES_BYTES,
        )
        if not review:
            raise ContextValidationError("general release review must not be empty")
        canonical = {
            "item_ids": item_ids,
            "public_title": public_title,
            "public_purpose": public_purpose,
            "review": review,
        }
        input_sha256 = hashlib.sha256(encode_json(canonical).encode()).hexdigest()

        with (
            context_writer_lock(self.data_root),
            context_connection(self.data_root) as connection,
        ):
            context_row = self._load_context(connection, context_id)
            active_manifest = connection.execute(
                """
                SELECT *
                FROM context_release_manifests
                WHERE context_id = ? AND state = 'active'
                """,
                (context_id,),
            ).fetchone()
            if context_row["state"] != "active":
                raise ContextConflictError(
                    "archived context cannot approve a general release",
                    details={"reason": "context_archived"},
                )
            exact_replay = bool(
                active_manifest is not None
                and active_manifest["input_sha256"] == input_sha256
            )
            if not exact_replay and context_row["version"] != expected_version:
                raise ContextConflictError(
                    "context version is not current",
                    details={
                        "reason": "version_mismatch",
                        "expected_version": expected_version,
                        "current_version": context_row["version"],
                    },
                )

            placeholders = ",".join("?" for _ in item_ids)
            item_rows = connection.execute(
                f"""
                SELECT *
                FROM context_items
                WHERE context_id = ? AND item_id IN ({placeholders})
                """,
                (context_id, *item_ids),
            ).fetchall()
            items_by_id = {row["item_id"]: row for row in item_rows}
            if len(items_by_id) != len(item_ids):
                raise ContextValidationError(
                    "general release contains an item outside the context"
                )
            for item_id in item_ids:
                row = items_by_id[item_id]
                if row["lifecycle_state"] != "active":
                    raise ContextConflictError(
                        "general release item is no longer active",
                        details={
                            "reason": "general_item_not_active",
                            "item_id": item_id,
                        },
                    )
                if (
                    row["disclosure_state"] != "general_candidate"
                    or row["kind"] not in GENERAL_CANDIDATE_KINDS
                ):
                    raise ContextValidationError(
                        "general release item is not an eligible candidate",
                        details={"item_id": item_id},
                    )

            external_source_count = connection.execute(
                f"""
                SELECT COUNT(*)
                FROM context_external_sources
                WHERE item_id IN ({placeholders})
                """,
                item_ids,
            ).fetchone()[0]
            if external_source_count:
                raise ContextValidationError(
                    "general release items cannot retain linked external sources"
                )
            source_rows = connection.execute(
                f"""
                SELECT *
                FROM context_sources
                WHERE item_id IN ({placeholders})
                ORDER BY item_id, source_ref_id
                """,
                item_ids,
            ).fetchall()
            sources_by_item: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for row in source_rows:
                sources_by_item[row["item_id"]].append(dict(row))
            for item_id in item_ids:
                sources = sources_by_item.get(item_id, [])
                if not sources or any(source["link_role"] != "direct" for source in sources):
                    raise ContextValidationError(
                        "general release item must use only direct sources",
                        details={"item_id": item_id},
                    )
                for source in sources:
                    observation = self._observe_source(source, strict=True)
                    if observation["dependency_state"] != "valid":
                        raise ContextConflictError(
                            "general release source is not current",
                            details={
                                "reason": "source_not_current",
                                "item_id": item_id,
                                "dependency_state": observation["dependency_state"],
                            },
                        )

            if exact_replay:
                released_rows = connection.execute(
                    """
                    SELECT item_id, public_id
                    FROM context_release_items
                    WHERE release_id = ?
                    ORDER BY position
                    """,
                    (active_manifest["release_id"],),
                ).fetchall()
                return {
                    "context_id": context_id,
                    "version": context_row["version"],
                    "idempotent_replay": True,
                    "published_externally": False,
                    "public_collection_id": active_manifest["public_collection_id"],
                    "items": [dict(row) for row in released_rows],
                }

            previous_public_ids: dict[str, str] = {}
            public_id_rows = connection.execute(
                f"""
                SELECT ri.item_id, ri.public_id
                FROM context_release_items ri
                JOIN context_release_manifests m
                  ON m.release_id = ri.release_id
                WHERE m.context_id = ? AND ri.item_id IN ({placeholders})
                ORDER BY m.release_number DESC
                """,
                (context_id, *item_ids),
            ).fetchall()
            for row in public_id_rows:
                previous_public_ids.setdefault(row["item_id"], row["public_id"])

            latest_manifest = connection.execute(
                """
                SELECT public_collection_id, release_number
                FROM context_release_manifests
                WHERE context_id = ?
                ORDER BY release_number DESC
                LIMIT 1
                """,
                (context_id,),
            ).fetchone()
            public_collection_id = (
                latest_manifest["public_collection_id"]
                if latest_manifest is not None
                else f"pubc_{uuid.uuid4().hex}"
            )
            release_number = (
                latest_manifest["release_number"] + 1
                if latest_manifest is not None
                else 1
            )
            release_id = f"ctxr_{uuid.uuid4().hex}"
            now = utc_now()
            if active_manifest is not None:
                connection.execute(
                    """
                    UPDATE context_release_manifests
                    SET state = 'superseded'
                    WHERE release_id = ?
                    """,
                    (active_manifest["release_id"],),
                )
            connection.execute(
                """
                INSERT INTO context_release_manifests(
                    release_id, context_id, public_collection_id, input_sha256,
                    release_number, public_title, public_purpose, review_json,
                    state, supersedes_release_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (
                    release_id,
                    context_id,
                    public_collection_id,
                    input_sha256,
                    release_number,
                    public_title,
                    public_purpose,
                    encode_json(review),
                    active_manifest["release_id"] if active_manifest is not None else None,
                    now,
                ),
            )
            released_items = []
            for position, item_id in enumerate(item_ids):
                public_id = previous_public_ids.get(item_id) or f"pubi_{uuid.uuid4().hex}"
                connection.execute(
                    """
                    INSERT INTO context_release_items(
                        release_id, item_id, public_id, position
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (release_id, item_id, public_id, position),
                )
                released_items.append(
                    {
                        "item_id": item_id,
                        "public_id": public_id,
                    }
                )
            new_version = context_row["version"] + 1
            connection.execute(
                """
                UPDATE contexts
                SET version = ?, updated_at = ?
                WHERE context_id = ?
                """,
                (new_version, now, context_id),
            )
        return {
            "context_id": context_id,
            "version": new_version,
            "idempotent_replay": False,
            "published_externally": False,
            "public_collection_id": public_collection_id,
            "items": released_items,
        }

    def _advance_checkpoint(
        self,
        context_id: str,
        *,
        expected_version: int,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if set(payload) != {"observations"} or not isinstance(
            payload["observations"],
            list,
        ):
            raise ContextValidationError(
                "checkpoint payload must contain only an observations list"
            )
        raw_observations = payload["observations"]
        if not 1 <= len(raw_observations) <= CONTEXT_MAX_CORPORA:
            raise BudgetExceededError(
                "checkpoint observation count is outside the supported range",
                details={"maximum_observations": CONTEXT_MAX_CORPORA},
            )
        requested_observations: dict[str, dict[str, str]] = {}
        required_keys = {
            "corpus_id",
            "observed_scan_id",
            "observed_snapshot_id",
            "observed_inventory_hash",
        }
        for raw in raw_observations:
            if not isinstance(raw, dict) or set(raw) != required_keys:
                raise ContextValidationError(
                    "checkpoint observation fields are invalid",
                    details={"required": sorted(required_keys)},
                )
            corpus_id = normalize_corpus_id(
                _require_string(
                    raw["corpus_id"],
                    field="corpus_id",
                    maximum=CONTEXT_MAX_IDENTIFIER_CHARS,
                )
            )
            if corpus_id in requested_observations:
                raise ContextValidationError("checkpoint corpus observations must be unique")
            inventory_hash = _require_string(
                raw["observed_inventory_hash"],
                field="observed_inventory_hash",
                maximum=64,
            )
            if not re.fullmatch(r"[0-9a-f]{64}", inventory_hash):
                raise ContextValidationError(
                    "observed_inventory_hash must be a lowercase SHA-256 digest"
                )
            requested_observations[corpus_id] = {
                "latest_scan_id": _require_string(
                    raw["observed_scan_id"],
                    field="observed_scan_id",
                    maximum=CONTEXT_MAX_IDENTIFIER_CHARS,
                ),
                "current_snapshot_id": _require_string(
                    raw["observed_snapshot_id"],
                    field="observed_snapshot_id",
                    maximum=CONTEXT_MAX_IDENTIFIER_CHARS,
                ),
                "inventory_hash": inventory_hash,
            }
        with (
            context_writer_lock(self.data_root),
            context_connection(self.data_root) as connection,
        ):
            context_row = self._load_context(connection, context_id)
            context_corpus_ids = self._context_corpus_ids(connection, context_id)
            corpus_ids = sorted(requested_observations)
            if not corpus_ids or not set(corpus_ids).issubset(context_corpus_ids):
                raise ContextValidationError("checkpoint corpora must be inside the context scope")
            observations = {
                corpus_id: self._current_corpus_observation(corpus_id) for corpus_id in corpus_ids
            }
            for corpus_id, current in observations.items():
                requested = requested_observations[corpus_id]
                if any(
                    current[field] != requested[field]
                    for field in (
                        "latest_scan_id",
                        "current_snapshot_id",
                        "inventory_hash",
                    )
                ):
                    raise ContextConflictError(
                        "corpus observation changed before checkpoint advance",
                        details={
                            "reason": "observation_changed",
                            "corpus_id": corpus_id,
                            "current": {
                                field: current[field]
                                for field in (
                                    "latest_scan_id",
                                    "current_snapshot_id",
                                    "inventory_hash",
                                )
                            },
                        },
                    )
            existing_rows = connection.execute(
                f"""
                    SELECT *
                    FROM context_corpora
                    WHERE context_id = ?
                      AND corpus_id IN ({",".join("?" for _ in corpus_ids)})
                    """,
                (context_id, *corpus_ids),
            ).fetchall()
            existing = {row["corpus_id"]: row for row in existing_rows}
            same = all(
                existing[corpus_id]["last_checked_scan_id"] == observation["latest_scan_id"]
                and existing[corpus_id]["last_checked_snapshot_id"]
                == observation["current_snapshot_id"]
                and existing[corpus_id]["last_checked_inventory_hash"]
                == observation["inventory_hash"]
                for corpus_id, observation in observations.items()
            )
            if same:
                return {
                    "context_id": context_id,
                    "version": context_row["version"],
                    "idempotent_replay": True,
                    "checkpoints": observations,
                }
            if context_row["state"] != "active":
                raise ContextConflictError(
                    "archived context cannot advance its checkpoint",
                    details={"reason": "context_archived"},
                )
            if context_row["version"] != expected_version:
                raise ContextConflictError(
                    "context version is not current",
                    details={
                        "reason": "version_mismatch",
                        "expected_version": expected_version,
                        "current_version": context_row["version"],
                    },
                )
            now = utc_now()
            for corpus_id in observations:
                connection.execute(
                    """
                        UPDATE context_corpora
                        SET last_checked_scan_id = ?,
                            last_checked_snapshot_id = ?,
                            last_checked_inventory_hash = ?,
                            last_checked_at = ?
                        WHERE context_id = ? AND corpus_id = ?
                        """,
                    (
                        requested_observations[corpus_id]["latest_scan_id"],
                        requested_observations[corpus_id]["current_snapshot_id"],
                        requested_observations[corpus_id]["inventory_hash"],
                        now,
                        context_id,
                        corpus_id,
                    ),
                )
            new_version = context_row["version"] + 1
            connection.execute(
                """
                    UPDATE contexts
                    SET version = ?, updated_at = ?
                    WHERE context_id = ?
                    """,
                (new_version, now, context_id),
            )
        return {
            "context_id": context_id,
            "version": new_version,
            "idempotent_replay": False,
            "checkpoints": observations,
        }

    def _archive(
        self,
        context_id: str,
        *,
        expected_version: int,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if payload:
            raise ContextValidationError("archive payload must be empty")
        with (
            context_writer_lock(self.data_root),
            context_connection(self.data_root) as connection,
        ):
            context_row = self._load_context(connection, context_id)
            if context_row["state"] == "archived":
                return {
                    "context_id": context_id,
                    "state": "archived",
                    "version": context_row["version"],
                    "idempotent_replay": True,
                }
            if context_row["version"] != expected_version:
                raise ContextConflictError(
                    "context version is not current",
                    details={
                        "reason": "version_mismatch",
                        "expected_version": expected_version,
                        "current_version": context_row["version"],
                    },
                )
            now = utc_now()
            new_version = context_row["version"] + 1
            connection.execute(
                """
                    UPDATE contexts
                    SET state = 'archived', version = ?, updated_at = ?
                    WHERE context_id = ?
                    """,
                (new_version, now, context_id),
            )
        return {
            "context_id": context_id,
            "state": "archived",
            "version": new_version,
            "idempotent_replay": False,
        }

    def _observe_external_source(
        self,
        source: dict[str, Any],
        *,
        connection=None,
        strict: bool = False,
    ) -> dict[str, Any]:
        owns_connection = connection is None
        context_manager = None
        if owns_connection:
            context_manager = context_read_connection(self.data_root)
            try:
                connection = context_manager.__enter__()
            except (ContextNotFoundError, CorpusError):
                if strict:
                    raise ContextValidationError(
                        "context external source registry is unavailable"
                    ) from None
                return {
                    "dependency_state": "source_unavailable",
                    "source_record_id": None,
                }
        try:
            if source.get("source_record_id"):
                row = connection.execute(
                    """
                    SELECT r.*, b.corpus_id, b.provider_kind, b.state AS binding_state,
                           b.selector_json
                    FROM external_source_records r
                    JOIN corpus_source_bindings b ON b.binding_id = r.binding_id
                    WHERE r.source_record_id = ?
                    """,
                    (source["source_record_id"],),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT r.*, b.corpus_id, b.provider_kind, b.state AS binding_state,
                           b.selector_json
                    FROM external_source_records r
                    JOIN corpus_source_bindings b ON b.binding_id = r.binding_id
                    WHERE r.binding_id = ? AND r.external_id = ?
                    """,
                    (source["binding_id"], source["external_id"]),
                ).fetchone()
        finally:
            if owns_connection and context_manager is not None:
                context_manager.__exit__(None, None, None)
        if row is None or row["corpus_id"] != source["corpus_id"]:
            if strict:
                raise ContextValidationError(
                    "context external source is not available in the declared corpus",
                    details={
                        "corpus_id": source["corpus_id"],
                        "binding_id": source.get("binding_id"),
                        "external_id": source.get("external_id"),
                    },
                )
            return {
                "dependency_state": "source_unavailable",
                "source_record_id": source.get("source_record_id"),
            }
        if row["binding_state"] != "active":
            dependency_state = "binding_archived"
        elif row["membership_state"] != "active":
            dependency_state = (
                "source_removed"
                if row["provider_kind"] in SESSION_SOURCE_PROVIDERS
                else "label_membership_changed"
            )
        elif (
            source.get("observed_metadata_sha256")
            and source["observed_metadata_sha256"] != row["metadata_sha256"]
        ):
            dependency_state = (
                "source_changed"
                if row["provider_kind"] in SESSION_SOURCE_PROVIDERS
                else "metadata_changed"
            )
        elif row["provider_kind"] in SESSION_SOURCE_PROVIDERS:
            dependency_state = probe_session_record(
                row["provider_kind"],
                _json_dict(row["selector_json"]),
                external_id=row["external_id"],
                provider_metadata=_json_dict(row["provider_metadata_json"]),
                locator=_json_dict(row["locator_json"]),
                expected_freshness_identity=row["freshness_identity"],
            )
        else:
            dependency_state = "valid"
        return {
            "dependency_state": dependency_state,
            "source_record_id": row["source_record_id"],
            "provider_kind": row["provider_kind"],
            "binding_id": row["binding_id"],
            "external_id": row["external_id"],
            "parent_external_id": row["parent_external_id"],
            "occurred_at": row["occurred_at"],
            "title": row["title"],
            "participants": json.loads(row["participants_json"]),
            "label_ids": json.loads(row["label_ids_json"]),
            "attachments": json.loads(row["attachments_json"]),
            "provider_metadata": _json_dict(row["provider_metadata_json"]),
            "locator": _json_dict(row["locator_json"]),
            "freshness_identity": row["freshness_identity"],
            "freshness_state": dependency_state,
            "metadata_sha256": row["metadata_sha256"],
            "membership_state": row["membership_state"],
            "last_seen_run_id": row["last_seen_run_id"],
            "last_seen_at": row["last_seen_at"],
        }

    def _observe_source(
        self,
        source: dict[str, Any],
        *,
        strict: bool = False,
    ) -> dict[str, Any]:
        try:
            get_corpus(self.data_root, source["corpus_id"])
            with corpus_read_connection(
                self.data_root,
                source["corpus_id"],
            ) as connection:
                row = connection.execute(
                    """
                    SELECT u.unit_id, u.revision_id, u.projection_id,
                           u.source_anchor_json, r.document_id,
                           r.source_size, r.source_modified_ns,
                           r.source_changed_ns, r.source_device, r.source_inode,
                           d.relative_path, d.current_revision_id, d.logical_size,
                           d.modified_ns, d.changed_ns, d.device, d.inode,
                           d.deleted_at, d.extension,
                           p.adapter_id, p.adapter_version, p.config_hash,
                           active.projection_id AS active_projection_id
                    FROM source_units u
                    JOIN revisions r ON r.revision_id = u.revision_id
                    JOIN documents d ON d.document_id = r.document_id
                    JOIN extraction_projections p
                      ON p.projection_id = u.projection_id
                    JOIN snapshot_documents sd
                      ON sd.snapshot_id = ?
                     AND sd.document_id = d.document_id
                     AND sd.revision_id = u.revision_id
                     AND sd.projection_id = u.projection_id
                    LEFT JOIN extraction_projections active
                      ON active.revision_id = u.revision_id
                     AND active.is_active = 1
                    WHERE u.unit_id = ?
                      AND u.revision_id = ?
                      AND u.projection_id = ?
                      AND d.document_id = ?
                    """,
                    (
                        source["snapshot_id"],
                        source["source_unit_id"],
                        source["revision_id"],
                        source["projection_id"],
                        source["document_id"],
                    ),
                ).fetchone()
        except CorpusError:
            if strict:
                raise ContextValidationError(
                    "context source corpus is unavailable",
                    details={"corpus_id": source["corpus_id"]},
                ) from None
            return {
                "dependency_state": "corpus_unavailable",
                "relative_path": None,
                "source_span": {},
            }
        if row is None:
            if strict:
                raise ContextValidationError(
                    "context source tuple is not available in the declared snapshot",
                    details={
                        "corpus_id": source["corpus_id"],
                        "source_unit_id": source["source_unit_id"],
                    },
                )
            return {
                "dependency_state": "source_unavailable",
                "relative_path": None,
                "source_span": {},
            }

        source_observation_current = (
            row["source_size"] == row["logical_size"]
            and row["source_modified_ns"] == row["modified_ns"]
            and row["source_changed_ns"] == row["changed_ns"]
            and row["source_device"] == row["device"]
            and row["source_inode"] == row["inode"]
        )
        adapter_current = self._projection_uses_current_adapter(
            row["extension"],
            row["adapter_id"],
            row["adapter_version"],
            row["config_hash"],
        )
        if row["deleted_at"] is not None:
            dependency_state = "document_missing"
        elif source["link_role"] == "contrast":
            dependency_state = "historical_available"
        elif row["revision_id"] != row["current_revision_id"]:
            dependency_state = "stale_source_revision"
        elif not source_observation_current:
            dependency_state = "stale_source_observation"
        elif row["projection_id"] != row["active_projection_id"]:
            dependency_state = "stale_extraction_projection"
        elif not adapter_current:
            dependency_state = "stale_extraction_adapter"
        else:
            dependency_state = "valid"
        try:
            source_anchor = json.loads(row["source_anchor_json"])
        except (TypeError, json.JSONDecodeError):
            source_anchor = {}
        source_span = source_anchor.get("source_span") if isinstance(source_anchor, dict) else {}
        if not isinstance(source_span, dict):
            source_span = {}
        return {
            "dependency_state": dependency_state,
            "relative_path": row["relative_path"],
            "source_span": source_span,
        }

    def _inventory_hash(self, connection) -> str:
        rows = connection.execute(
            """
            SELECT document_id, relative_path_nfc, logical_size, allocated_size,
                   modified_ns, changed_ns, device, inode, is_dataless,
                   residency_state
            FROM documents
            WHERE deleted_at IS NULL AND eligibility_state = 'supported'
            ORDER BY document_id
            """
        ).fetchall()
        return hashlib.sha256(encode_json([dict(row) for row in rows]).encode()).hexdigest()

    def _current_corpus_observation(self, corpus_id: str) -> dict[str, Any]:
        try:
            with corpus_read_connection(self.data_root, corpus_id) as connection:
                scan = connection.execute(
                    """
                    SELECT scan_id, completed_at, status
                    FROM scan_runs
                    ORDER BY rowid DESC
                    LIMIT 1
                    """
                ).fetchone()
                snapshot = connection.execute(
                    """
                    SELECT snapshot_id, coverage_state
                    FROM snapshots
                    WHERE state = 'complete'
                    ORDER BY rowid DESC
                    LIMIT 1
                    """
                ).fetchone()
                inventory_hash = self._inventory_hash(connection)
        except CorpusError:
            return {
                "available": False,
                "latest_scan_id": None,
                "scan_completed_at": None,
                "scan_status": None,
                "inventory_complete": False,
                "current_snapshot_id": None,
                "snapshot_coverage_state": None,
                "inventory_hash": None,
            }
        return {
            "available": True,
            "latest_scan_id": scan["scan_id"] if scan else None,
            "scan_completed_at": scan["completed_at"] if scan else None,
            "scan_status": scan["status"] if scan else None,
            "inventory_complete": bool(scan is not None and scan["status"] == "complete"),
            "current_snapshot_id": snapshot["snapshot_id"] if snapshot else None,
            "snapshot_coverage_state": snapshot["coverage_state"] if snapshot else None,
            "inventory_hash": inventory_hash,
        }

    def _snapshot_mapping(self, connection, snapshot_id: str | None) -> dict[str, tuple]:
        if snapshot_id is None:
            return {}
        rows = connection.execute(
            """
            SELECT document_id, revision_id, projection_id
            FROM snapshot_documents
            WHERE snapshot_id = ?
            ORDER BY document_id
            """,
            (snapshot_id,),
        ).fetchall()
        return {row["document_id"]: (row["revision_id"], row["projection_id"]) for row in rows}

    def _scan_change_events(
        self,
        connection,
        *,
        baseline_scan_id: str,
        current_scan_id: str,
    ) -> tuple[list[dict[str, Any]], bool]:
        rows = connection.execute(
            """
            SELECT payload_json
            FROM events
            WHERE event_type = 'scan_inventory_delta'
            ORDER BY rowid
            """
        ).fetchall()
        baseline_seen = False
        current_seen = False
        truncated = False
        merged: dict[str, set[str]] = defaultdict(set)
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            scan_id = payload.get("scan_id") if isinstance(payload, dict) else None
            if scan_id == baseline_scan_id:
                baseline_seen = True
                continue
            if not baseline_seen:
                continue
            if payload.get("truncated"):
                truncated = True
            changes = payload.get("changes")
            if isinstance(changes, list):
                for change in changes:
                    if not isinstance(change, dict):
                        continue
                    document_id = change.get("document_id")
                    kinds = change.get("change_types")
                    if not isinstance(document_id, str) or not isinstance(kinds, list):
                        continue
                    merged[document_id].update(kind for kind in kinds if isinstance(kind, str))
            if scan_id == current_scan_id:
                current_seen = True
                break
        candidates = [
            {
                "document_id": document_id,
                "change_types": sorted(change_types),
            }
            for document_id, change_types in sorted(merged.items())
        ]
        return candidates, bool(truncated or not baseline_seen or not current_seen)

    def _inventory_change(
        self,
        checkpoint: dict[str, Any],
        current: dict[str, Any],
    ) -> dict[str, Any]:
        baseline_scan_id = checkpoint.get("last_checked_scan_id")
        baseline_snapshot_id = checkpoint.get("last_checked_snapshot_id")
        baseline_hash = checkpoint.get("last_checked_inventory_hash")
        current_scan_id = current.get("latest_scan_id")
        current_snapshot_id = current.get("current_snapshot_id")
        if baseline_scan_id is None:
            return {
                "checkpoint_missing": True,
                "inventory_changed": None,
                "inventory_hash_changed": None,
                "change_candidates": [],
                "mapping_changes": [],
                "unclassified_inventory_change": True,
            }
        if not current.get("available"):
            return {
                "checkpoint_missing": False,
                "inventory_changed": None,
                "inventory_hash_changed": None,
                "change_candidates": [],
                "mapping_changes": [],
                "unclassified_inventory_change": True,
            }
        if current_scan_id == baseline_scan_id and current_snapshot_id == baseline_snapshot_id:
            hash_changed = current.get("inventory_hash") != baseline_hash
            return {
                "checkpoint_missing": False,
                "inventory_changed": hash_changed,
                "inventory_hash_changed": hash_changed,
                "change_candidates": [],
                "mapping_changes": [],
                "unclassified_inventory_change": hash_changed,
            }

        try:
            with corpus_read_connection(
                self.data_root,
                checkpoint["corpus_id"],
            ) as connection:
                if baseline_scan_id == current_scan_id:
                    candidates = []
                    event_unclassified = False
                else:
                    candidates, event_unclassified = self._scan_change_events(
                        connection,
                        baseline_scan_id=baseline_scan_id,
                        current_scan_id=current_scan_id,
                    )
                baseline_mapping = self._snapshot_mapping(
                    connection,
                    baseline_snapshot_id,
                )
                current_mapping = self._snapshot_mapping(
                    connection,
                    current_snapshot_id,
                )
                candidate_ids = [candidate["document_id"] for candidate in candidates]
                candidate_metadata = {}
                if candidate_ids:
                    placeholders = ",".join("?" for _ in candidate_ids)
                    rows = connection.execute(
                        f"""
                        SELECT document_id, relative_path, residency_state,
                               eligibility_state, current_revision_id, deleted_at
                        FROM documents
                        WHERE document_id IN ({placeholders})
                        """,
                        candidate_ids,
                    ).fetchall()
                    candidate_metadata = {row["document_id"]: dict(row) for row in rows}
                for candidate in candidates:
                    candidate.update(candidate_metadata.get(candidate["document_id"], {}))
        except CorpusError:
            candidates = []
            event_unclassified = True
            baseline_mapping = {}
            current_mapping = {}

        mapping_changes = []
        for document_id in sorted(set(baseline_mapping) | set(current_mapping)):
            before = baseline_mapping.get(document_id)
            after = current_mapping.get(document_id)
            if before == after:
                continue
            mapping_changes.append(
                {
                    "document_id": document_id,
                    "before_revision_id": before[0] if before else None,
                    "before_projection_id": before[1] if before else None,
                    "current_revision_id": after[0] if after else None,
                    "current_projection_id": after[1] if after else None,
                }
            )
        combined_document_ids = {candidate["document_id"] for candidate in candidates} | {
            change["document_id"] for change in mapping_changes
        }
        truncated = len(combined_document_ids) > CONTEXT_MAX_CHANGE_CANDIDATES
        if truncated:
            allowed = set(sorted(combined_document_ids)[:CONTEXT_MAX_CHANGE_CANDIDATES])
            candidates = [
                candidate for candidate in candidates if candidate["document_id"] in allowed
            ]
            mapping_changes = [
                change for change in mapping_changes if change["document_id"] in allowed
            ]
        hash_changed = current.get("inventory_hash") != baseline_hash
        inventory_changed = bool(hash_changed or candidates or mapping_changes)
        return {
            "checkpoint_missing": False,
            "inventory_changed": inventory_changed,
            "inventory_hash_changed": hash_changed,
            "change_candidates": candidates,
            "mapping_changes": mapping_changes,
            "change_candidates_truncated": truncated,
            "unclassified_inventory_change": bool(
                event_unclassified
                or truncated
                or not current.get("inventory_complete")
                or (hash_changed and not candidates and not mapping_changes)
            ),
        }

    def _require_read_budget(self, response: dict[str, Any]) -> None:
        size = len(encode_json(response).encode())
        if size > CONTEXT_MAX_READ_BYTES:
            raise BudgetExceededError(
                "context response exceeds the serialized response budget",
                details={
                    "serialized_bytes": size,
                    "maximum_bytes": CONTEXT_MAX_READ_BYTES,
                },
            )
