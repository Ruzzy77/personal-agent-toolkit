"""Deterministic storage and retrieval for Hypes explanation clues."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from . import __version__
from .errors import HypesError, InvalidTicket
from .model import RecheckBasis, RelationDraft, RetentionBasis, StoredScope
from .store import HypesStore, _canonical, _digest, relation_ref

try:
    from ._build import BUILD_ID
except ImportError:
    BUILD_ID = f"{__version__}+owner"

EVIDENCE_REVIEW_DAYS = 90
EXPLICIT_REVIEW_DAYS = 180


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _as_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _stored_relation_payload(row: Any) -> dict[str, Any]:
    return {
        "relation_id": row["relation_id"],
        "scope": json.loads(row["scope_json"]),
        "kind": row["kind"],
        "statement": row["statement"],
        "explanation_pattern": row["explanation_pattern"],
    }


def _public_scope(value: dict[str, Any]) -> dict[str, Any]:
    """Present the legacy stored field as a plain situation."""

    result = dict(value)
    result["situation"] = result.pop("task", None)
    return result


def _relation_payload(row: Any) -> dict[str, Any]:
    result = _stored_relation_payload(row)
    result["scope"] = _public_scope(result["scope"])
    return result


def _relation_storage_digest(row: Any) -> str:
    return _digest(
        {
            **_stored_relation_payload(row),
            "status": row["status"],
            "retention_basis": row["retention_basis"],
            "recheck_basis": row["recheck_basis"],
            "recheck_marked_at": row["recheck_marked_at"],
            "review_after": row["review_after"],
            "revision": row["revision"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
    )


class HypesService:
    def __init__(self, data_root: Path | None = None) -> None:
        self.store = HypesStore(data_root)

    @staticmethod
    def _scope_matches(
        stored: StoredScope,
        requested: StoredScope,
        *,
        include_broader: bool,
    ) -> bool:
        if stored.topic != requested.topic:
            return False
        if not include_broader:
            return stored == requested
        if requested.task is None:
            if stored.task is not None:
                return False
        elif stored.task not in (None, requested.task):
            return False
        if requested.responsibility is None:
            return stored.responsibility is None
        return stored.responsibility in (None, requested.responsibility)

    @staticmethod
    def _relation_value(row: Any, *, now: datetime) -> dict[str, Any]:
        review_due = _as_datetime(row["review_after"]) <= now
        status = (
            "recheck_due"
            if row["status"] == "recheck_due" or review_due
            else "active"
        )
        value = {
            "relation_ref": row["relation_ref"],
            **_relation_payload(row),
            "status": status,
            "retention_basis": row["retention_basis"],
            "review_after": row["review_after"],
            "revision": row["revision"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        if status == "recheck_due":
            reasons: list[str] = []
            if row["status"] == "recheck_due":
                reasons.append(row["recheck_basis"] or "marked_for_recheck")
            if review_due:
                reasons.append("review_due")
            value["recheck_reasons"] = reasons
            value["recheck_marked_at"] = row["recheck_marked_at"]
        return value

    def read(
        self,
        *,
        topic: str,
        task: str | None = None,
        responsibility: str | None = None,
        include_broader: bool = False,
        include_recheck: bool = False,
        limit: int = 20,
    ) -> dict[str, Any]:
        scope = StoredScope(topic=topic, task=task, responsibility=responsibility)
        now = _utcnow()
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM relations "
                "WHERE json_extract(scope_json, '$.topic') = ? "
                "ORDER BY updated_at DESC, relation_ref",
                (scope.topic,),
            ).fetchall()
            matches: list[dict[str, Any]] = []
            for row in rows:
                stored = StoredScope.model_validate_json(row["scope_json"])
                if not self._scope_matches(
                    stored, scope, include_broader=include_broader
                ):
                    continue
                item = self._relation_value(row, now=now)
                if item["status"] == "recheck_due" and not include_recheck:
                    continue
                item["scope_match"] = "exact" if stored == scope else "broader"
                matches.append(item)
            matches.sort(key=lambda item: item["scope_match"] != "exact")
            return {
                "revision": self.store.revision(connection),
                "scope": _public_scope(scope.model_dump(mode="json")),
                "include_broader": include_broader,
                "relations": matches[:limit],
            }

    def revise(
        self,
        *,
        expected_revision: int,
        idempotency_key: str,
        relation: RelationDraft,
        retention_basis: RetentionBasis,
        review_in_days: int | None = None,
    ) -> dict[str, Any]:
        supported_bases = {
            "explicit_user_request",
            "explicit_user_correction",
            "demonstrated_application",
            "confirmed_explanation_outcome",
            "repeated_across_conversations",
        }
        if retention_basis not in supported_bases:
            raise HypesError(
                "invalid_retention_basis", "unsupported retention basis"
            )
        relation = RelationDraft.model_validate(relation.model_dump(mode="json"))
        explanation_kinds = {"helpful_explanation", "unhelpful_explanation"}
        if relation.kind in explanation_kinds and retention_basis not in {
            "explicit_user_request",
            "confirmed_explanation_outcome",
        }:
            raise HypesError(
                "retention_evidence_mismatch",
                "an explanation relation requires a direct save request or confirmed explanation outcome",
            )
        if (
            relation.kind not in explanation_kinds
            and retention_basis == "confirmed_explanation_outcome"
        ):
            raise HypesError(
                "retention_evidence_mismatch",
                "confirmed explanation outcome is only valid for explanation relations",
            )
        if review_in_days is None:
            review_in_days = (
                EXPLICIT_REVIEW_DAYS
                if retention_basis == "explicit_user_request"
                else EVIDENCE_REVIEW_DAYS
            )
        if review_in_days < 1 or review_in_days > 3650:
            raise HypesError(
                "invalid_review_horizon", "review_in_days must be between 1 and 3650"
            )
        request = {
            "expected_revision": expected_revision,
            "relation": relation.model_dump(mode="json"),
            "retention_basis": retention_basis,
            "review_in_days": review_in_days,
        }
        now = _utcnow()
        review_after = now + timedelta(days=review_in_days)
        payload = relation.model_dump(mode="json")
        ref = relation_ref(relation.relation_id, payload["scope"])
        with self.store.connect() as connection:
            self.store.begin_write(connection)
            replay = self.store.replay(
                connection, "revise_v5", idempotency_key, request
            )
            if replay is not None:
                replay["replayed"] = True
                return replay
            self.store.require_revision(connection, expected_revision)
            current = connection.execute(
                "SELECT * FROM relations WHERE relation_ref = ?", (ref,)
            ).fetchone()
            revision = self.store.next_revision(connection)
            created_at = (
                current["created_at"] if current is not None else now.isoformat()
            )
            connection.execute(
                "INSERT INTO relations(relation_ref, relation_id, scope_json, kind, statement, "
                "explanation_pattern, status, retention_basis, recheck_basis, "
                "recheck_marked_at, review_after, revision, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'active', ?, NULL, NULL, ?, ?, ?, ?) "
                "ON CONFLICT(relation_ref) DO UPDATE SET relation_id=excluded.relation_id, "
                "scope_json=excluded.scope_json, kind=excluded.kind, statement=excluded.statement, "
                "explanation_pattern=excluded.explanation_pattern, status='active', "
                "retention_basis=excluded.retention_basis, recheck_basis=NULL, "
                "recheck_marked_at=NULL, review_after=excluded.review_after, "
                "revision=excluded.revision, updated_at=excluded.updated_at",
                (
                    ref,
                    relation.relation_id,
                    _canonical(payload["scope"]),
                    relation.kind,
                    relation.statement,
                    relation.explanation_pattern,
                    retention_basis,
                    review_after.isoformat(),
                    revision,
                    created_at,
                    now.isoformat(),
                ),
            )
            result = {
                "revision": revision,
                "relation_ref": ref,
                "relation_id": relation.relation_id,
                "status": "active",
                "retention_basis": retention_basis,
                "review_after": review_after.isoformat(),
                "replayed": False,
            }
            self.store.record_replay(
                connection, "revise_v5", idempotency_key, request, result
            )
            return result

    def mark_recheck(
        self,
        *,
        expected_revision: int,
        idempotency_key: str,
        relation_ref_value: str,
        recheck_basis: RecheckBasis,
    ) -> dict[str, Any]:
        if recheck_basis not in {
            "explicit_user_correction",
            "incompatible_application_outcome",
            "current_conversation_conflict",
        }:
            raise HypesError("invalid_recheck_basis", "unsupported recheck basis")
        request = {
            "expected_revision": expected_revision,
            "relation_ref": relation_ref_value,
            "recheck_basis": recheck_basis,
        }
        now = _utcnow()
        with self.store.connect() as connection:
            self.store.begin_write(connection)
            replay = self.store.replay(
                connection, "mark_recheck", idempotency_key, request
            )
            if replay is not None:
                replay["replayed"] = True
                return replay
            self.store.require_revision(connection, expected_revision)
            current = connection.execute(
                "SELECT * FROM relations WHERE relation_ref = ?",
                (relation_ref_value,),
            ).fetchone()
            if current is None:
                raise HypesError(
                    "relation_not_found",
                    "the relation ref is not retained; read the current Hypes clues again",
                )
            if current["status"] == "recheck_due":
                result = {
                    "revision": self.store.revision(connection),
                    "relation_ref": relation_ref_value,
                    "status": "recheck_due",
                    "recheck_basis": current["recheck_basis"],
                    "changed": False,
                    "replayed": False,
                }
            else:
                revision = self.store.next_revision(connection)
                connection.execute(
                    "UPDATE relations SET status='recheck_due', recheck_basis=?, "
                    "recheck_marked_at=?, revision=?, updated_at=? WHERE relation_ref=?",
                    (
                        recheck_basis,
                        now.isoformat(),
                        revision,
                        now.isoformat(),
                        relation_ref_value,
                    ),
                )
                result = {
                    "revision": revision,
                    "relation_ref": relation_ref_value,
                    "status": "recheck_due",
                    "recheck_basis": recheck_basis,
                    "changed": True,
                    "replayed": False,
                }
            self.store.record_replay(
                connection, "mark_recheck", idempotency_key, request, result
            )
            return result

    def overview(self, *, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        now = _utcnow()
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM relations ORDER BY updated_at DESC, relation_ref "
                "LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            all_rows = connection.execute("SELECT * FROM relations").fetchall()
            counts = {"active": 0, "recheck_due": 0}
            for row in all_rows:
                counts[self._relation_value(row, now=now)["status"]] += 1
            items = [
                {"item_type": "relation", **self._relation_value(row, now=now)}
                for row in rows
            ]
            total = len(all_rows)
            next_offset = offset + len(items)
            return {
                "revision": self.store.revision(connection),
                "counts": counts,
                "topics": sorted(
                    {json.loads(row["scope_json"])["topic"] for row in all_rows}
                )[:100],
                "items": items,
                "offset": offset,
                "limit": limit,
                "next_offset": next_offset if next_offset < total else None,
                "total_retained_items": total,
            }

    def preview_forget(self, *, relation_refs: list[str] | None = None) -> dict[str, Any]:
        requested_refs = sorted(set(relation_refs or []))
        if not requested_refs:
            raise HypesError(
                "empty_forget_target", "provide at least one relation ref"
            )
        if len(requested_refs) > 50:
            raise HypesError(
                "forget_target_limit", "at most 50 relation refs are allowed"
            )
        now = _utcnow()
        with self.store.connect() as connection:
            revision = self.store.revision(connection)
            placeholders = ",".join("?" for _ in requested_refs)
            rows = connection.execute(
                f"SELECT * FROM relations WHERE relation_ref IN ({placeholders}) "
                "ORDER BY relation_ref",
                requested_refs,
            ).fetchall()
            if not rows:
                raise HypesError(
                    "forget_target_not_found",
                    "none of the requested relation refs are retained",
                )
            found_refs = [row["relation_ref"] for row in rows]
            expires_at = now + timedelta(minutes=10)
            payload = {
                "action": "forget_relations_v5",
                "expected_revision": revision,
                "relation_targets": [
                    {
                        "relation_ref": row["relation_ref"],
                        "relation_revision": row["revision"],
                        "storage_digest": _relation_storage_digest(row),
                    }
                    for row in rows
                ],
                "expires_at": expires_at.isoformat(),
            }
            return {
                "revision": revision,
                "relations": [self._relation_value(row, now=now) for row in rows],
                "missing_relation_refs": sorted(
                    set(requested_refs) - set(found_refs)
                ),
                "forget_ticket": self.store.sign_ticket(connection, payload),
                "expires_at": payload["expires_at"],
            }

    def forget(
        self,
        *,
        expected_revision: int,
        forget_ticket: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        request = {
            "expected_revision": expected_revision,
            "forget_ticket": forget_ticket,
        }
        now = _utcnow()
        with self.store.connect(purge_deleted_content=True) as connection:
            self.store.begin_write(connection)
            replay = self.store.replay(
                connection, "forget_v5", idempotency_key, request
            )
            if replay is not None:
                replay["replayed"] = True
                return replay
            self.store.require_revision(connection, expected_revision)
            payload = self.store.verify_ticket(connection, forget_ticket)
            if payload is None or payload.get("action") != "forget_relations_v5":
                raise InvalidTicket()
            try:
                expires_at = _as_datetime(payload["expires_at"])
            except (KeyError, TypeError, ValueError) as exc:
                raise InvalidTicket() from exc
            if expires_at <= now:
                raise InvalidTicket()
            if payload.get("expected_revision") != expected_revision:
                raise InvalidTicket(
                    "the removal ticket refers to a different Hypes revision"
                )
            targets = payload.get("relation_targets")
            if not isinstance(targets, list) or not all(
                isinstance(value, dict)
                and isinstance(value.get("relation_ref"), str)
                and isinstance(value.get("relation_revision"), int)
                and isinstance(value.get("storage_digest"), str)
                for value in targets
            ):
                raise InvalidTicket()
            for target in targets:
                current = connection.execute(
                    "SELECT * FROM relations WHERE relation_ref = ?",
                    (target["relation_ref"],),
                ).fetchone()
                if (
                    current is None
                    or current["revision"] != target["relation_revision"]
                    or _relation_storage_digest(current) != target["storage_digest"]
                ):
                    raise InvalidTicket(
                        "a previewed relation changed or disappeared; preview the deletion again"
                    )
            relation_refs = [target["relation_ref"] for target in targets]
            placeholders = ",".join("?" for _ in relation_refs)
            cursor = connection.execute(
                f"DELETE FROM relations WHERE relation_ref IN ({placeholders})",
                relation_refs,
            )
            revision = self.store.next_revision(connection)
            result = {
                "revision": revision,
                "removed_relation_refs": relation_refs,
                "removed_relation_count": cursor.rowcount,
                "replayed": False,
            }
            self.store.record_replay(
                connection, "forget_v5", idempotency_key, request, result
            )
            return result

    def status(self) -> dict[str, Any]:
        with self.store.connect() as connection:
            return {
                "version": __version__,
                "build_id": BUILD_ID,
                "schema_version": 5,
                "revision": self.store.revision(connection),
                "revision_boundary": "retained_clues_recheck_and_removal",
                "database_path": str(self.store.database_path),
                "transport_session_state": False,
                "persistent_application_state": True,
                "http_publication_ready": False,
            }
