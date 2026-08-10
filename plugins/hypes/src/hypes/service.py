"""Deterministic Hypes cognitive-model operations."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from . import __version__
from .errors import HypesError, InvalidTicket
from .model import CognitiveScope, EvidenceKind, RelationDraft
from .store import HypesStore, _now

try:
    from ._build import BUILD_ID
except ImportError:
    BUILD_ID = f"{__version__}+owner"


def _row_value(row: Any) -> dict[str, Any]:
    return {
        "relation_id": row["relation_id"],
        "scope": json.loads(row["scope_json"]),
        "kind": row["kind"],
        "statement": row["statement"],
        "explanation_pattern": row["explanation_pattern"],
        "status": row["status"],
        "evidence_kinds": json.loads(row["evidence_kinds_json"]),
        "evidence_count": len(json.loads(row["episode_digests_json"])),
        "revision": row["revision"],
        "updated_at": row["updated_at"],
    }


class HypesService:
    def __init__(self, data_root: Path | None = None) -> None:
        self.store = HypesStore(data_root)

    def read(
        self,
        *,
        topic: str,
        task: str | None = None,
        responsibility: str | None = None,
        include_recheck: bool = False,
        limit: int = 20,
    ) -> dict[str, Any]:
        scope = CognitiveScope(topic=topic, task=task, responsibility=responsibility)
        with self.store.connect() as connection:
            statuses = ["active"]
            if include_recheck:
                statuses.append("recheck_due")
            placeholders = ",".join("?" for _ in statuses)
            rows = connection.execute(
                f"SELECT * FROM relations WHERE status IN ({placeholders}) "
                "ORDER BY updated_at DESC, relation_id LIMIT ?",
                (*statuses, limit * 4),
            ).fetchall()
            matches: list[dict[str, Any]] = []
            for row in rows:
                stored = CognitiveScope.model_validate_json(row["scope_json"])
                if stored.topic != scope.topic:
                    continue
                if scope.task is not None and stored.task not in (None, scope.task):
                    continue
                if (
                    scope.responsibility is not None
                    and stored.responsibility not in (None, scope.responsibility)
                ):
                    continue
                matches.append(_row_value(row))
                if len(matches) == limit:
                    break
            return {
                "revision": self.store.revision(connection),
                "scope": scope.model_dump(mode="json"),
                "relations": matches,
                "use_boundary": (
                    "Use these as revisable explanation clues, not as facts about the whole person."
                ),
            }

    def observe(
        self,
        *,
        expected_revision: int,
        observation_id: str,
        idempotency_key: str,
        episode_key: str,
        evidence_kind: EvidenceKind,
        relation: RelationDraft,
    ) -> dict[str, Any]:
        request = {
            "expected_revision": expected_revision,
            "observation_id": observation_id,
            "episode_key": episode_key,
            "evidence_kind": evidence_kind,
            "relation": relation.model_dump(mode="json"),
        }
        with self.store.connect() as connection:
            self.store.begin_write(connection)
            replay = self.store.replay(
                connection, "observe", idempotency_key, request
            )
            if replay is not None:
                replay["replayed"] = True
                return replay
            self.store.require_revision(connection, expected_revision)
            episode_digest = self.store.episode_digest(connection, episode_key)
            payload = relation.model_dump(mode="json")
            payload_json = json.dumps(
                payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            existing_observation = connection.execute(
                "SELECT payload_json, evidence_kind, episode_digest FROM observations "
                "WHERE observation_id = ?",
                (observation_id,),
            ).fetchone()
            if existing_observation is not None:
                same = (
                    existing_observation["payload_json"] == payload_json
                    and existing_observation["evidence_kind"] == evidence_kind
                    and existing_observation["episode_digest"] == episode_digest
                )
                if not same:
                    raise HypesError(
                        "observation_conflict",
                        "the observation id was already used with different content",
                    )
                raise HypesError(
                    "observation_already_recorded",
                    "the observation id was already recorded; replay with the original idempotency key",
                )
            else:
                connection.execute(
                    "INSERT INTO observations(observation_id, relation_id, payload_json, "
                    "payload_digest, episode_digest, evidence_kind, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        observation_id,
                        relation.relation_id,
                        payload_json,
                        hashlib.sha256(payload_json.encode()).hexdigest(),
                        episode_digest,
                        evidence_kind,
                        _now(),
                    ),
                )

            agreeing = connection.execute(
                "SELECT DISTINCT episode_digest, evidence_kind FROM observations "
                "WHERE relation_id = ? AND payload_json = ?",
                (relation.relation_id, payload_json),
            ).fetchall()
            evidence_kinds = sorted({row["evidence_kind"] for row in agreeing})
            episode_digests = sorted({row["episode_digest"] for row in agreeing})
            current = connection.execute(
                "SELECT * FROM relations WHERE relation_id = ?",
                (relation.relation_id,),
            ).fetchone()
            strong = evidence_kind in {"explicit_correction", "applied_outcome"}
            repeated = len(episode_digests) >= 2 and "repeated_observation" in evidence_kinds
            status = "active" if strong or repeated else "pending"
            if current is not None:
                current_payload = {
                    "relation_id": current["relation_id"],
                    "scope": json.loads(current["scope_json"]),
                    "kind": current["kind"],
                    "statement": current["statement"],
                    "explanation_pattern": current["explanation_pattern"],
                }
                if current["status"] in {"active", "recheck_due"} and current_payload != payload:
                    status = (
                        "active" if evidence_kind == "explicit_correction" else "recheck_due"
                    )
                elif current["status"] == "active" and status == "pending":
                    status = "active"

            revision = self.store.next_revision(connection)
            now = _now()
            if current is None:
                connection.execute(
                    "INSERT INTO relations(relation_id, scope_json, kind, statement, "
                    "explanation_pattern, status, evidence_kinds_json, episode_digests_json, "
                    "revision, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        relation.relation_id,
                        relation.scope.model_dump_json(),
                        relation.kind,
                        relation.statement,
                        relation.explanation_pattern,
                        status,
                        json.dumps(evidence_kinds),
                        json.dumps(episode_digests),
                        revision,
                        now,
                        now,
                    ),
                )
            elif status == "recheck_due":
                connection.execute(
                    "UPDATE relations SET status = 'recheck_due', revision = ?, updated_at = ? "
                    "WHERE relation_id = ?",
                    (revision, now, relation.relation_id),
                )
            else:
                connection.execute(
                    "UPDATE relations SET scope_json = ?, kind = ?, statement = ?, "
                    "explanation_pattern = ?, status = ?, evidence_kinds_json = ?, "
                    "episode_digests_json = ?, revision = ?, updated_at = ? "
                    "WHERE relation_id = ?",
                    (
                        relation.scope.model_dump_json(),
                        relation.kind,
                        relation.statement,
                        relation.explanation_pattern,
                        status,
                        json.dumps(evidence_kinds),
                        json.dumps(episode_digests),
                        revision,
                        now,
                        relation.relation_id,
                    ),
                )
            result = {
                "revision": revision,
                "relation_id": relation.relation_id,
                "status": status,
                "promoted": status == "active" and (strong or repeated),
                "replayed": False,
            }
            self.store.record_replay(
                connection, "observe", idempotency_key, request, result
            )
            return result

    def revise(
        self,
        *,
        expected_revision: int,
        idempotency_key: str,
        relation: RelationDraft,
    ) -> dict[str, Any]:
        request = {
            "expected_revision": expected_revision,
            "relation": relation.model_dump(mode="json"),
        }
        with self.store.connect() as connection:
            self.store.begin_write(connection)
            replay = self.store.replay(connection, "revise", idempotency_key, request)
            if replay is not None:
                replay["replayed"] = True
                return replay
            self.store.require_revision(connection, expected_revision)
            current = connection.execute(
                "SELECT * FROM relations WHERE relation_id = ?",
                (relation.relation_id,),
            ).fetchone()
            revision = self.store.next_revision(connection)
            now = _now()
            created_at = current["created_at"] if current is not None else now
            episode_digests = "[]"
            connection.execute(
                "INSERT INTO relations(relation_id, scope_json, kind, statement, "
                "explanation_pattern, status, evidence_kinds_json, episode_digests_json, "
                "revision, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?) "
                "ON CONFLICT(relation_id) DO UPDATE SET scope_json=excluded.scope_json, "
                "kind=excluded.kind, statement=excluded.statement, "
                "explanation_pattern=excluded.explanation_pattern, status='active', "
                "evidence_kinds_json=excluded.evidence_kinds_json, revision=excluded.revision, "
                "updated_at=excluded.updated_at",
                (
                    relation.relation_id,
                    relation.scope.model_dump_json(),
                    relation.kind,
                    relation.statement,
                    relation.explanation_pattern,
                    json.dumps(["explicit_correction"]),
                    episode_digests,
                    revision,
                    created_at,
                    now,
                ),
            )
            result = {
                "revision": revision,
                "relation_id": relation.relation_id,
                "status": "active",
                "replayed": False,
            }
            self.store.record_replay(
                connection, "revise", idempotency_key, request, result
            )
            return result

    def overview(self) -> dict[str, Any]:
        with self.store.connect() as connection:
            counts = {
                row["status"]: row["count"]
                for row in connection.execute(
                    "SELECT status, COUNT(*) AS count FROM relations GROUP BY status"
                ).fetchall()
            }
            topics = [
                row["topic"]
                for row in connection.execute(
                    "SELECT DISTINCT json_extract(scope_json, '$.topic') AS topic "
                    "FROM relations WHERE status IN ('active', 'recheck_due') "
                    "ORDER BY topic LIMIT 100"
                ).fetchall()
            ]
            return {
                "revision": self.store.revision(connection),
                "counts": counts,
                "topics": topics,
                "stores_raw_conversation": False,
                "transport_session_state": False,
            }

    def preview_forget(self, relation_ids: list[str]) -> dict[str, Any]:
        unique_ids = sorted(set(relation_ids))
        with self.store.connect() as connection:
            revision = self.store.revision(connection)
            placeholders = ",".join("?" for _ in unique_ids)
            rows = (
                connection.execute(
                    f"SELECT * FROM relations WHERE relation_id IN ({placeholders})",
                    unique_ids,
                ).fetchall()
                if unique_ids
                else []
            )
            found_ids = [row["relation_id"] for row in rows]
            expires_at = datetime.now(UTC) + timedelta(minutes=10)
            payload = {
                "action": "forget",
                "expected_revision": revision,
                "relation_ids": found_ids,
                "expires_at": expires_at.isoformat(),
            }
            return {
                "revision": revision,
                "items": [_row_value(row) for row in rows],
                "missing_relation_ids": sorted(set(unique_ids) - set(found_ids)),
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
        with self.store.connect() as connection:
            self.store.begin_write(connection)
            replay = self.store.replay(connection, "forget", idempotency_key, request)
            if replay is not None:
                replay["replayed"] = True
                return replay
            self.store.require_revision(connection, expected_revision)
            payload = self.store.verify_ticket(connection, forget_ticket)
            if payload is None or payload.get("action") != "forget":
                raise InvalidTicket()
            try:
                expires_at = datetime.fromisoformat(payload["expires_at"])
            except (KeyError, TypeError, ValueError) as exc:
                raise InvalidTicket() from exc
            if expires_at <= datetime.now(UTC):
                raise InvalidTicket()
            if payload.get("expected_revision") != expected_revision:
                raise InvalidTicket("the forget ticket targets a different model revision")
            relation_ids = payload.get("relation_ids")
            if not isinstance(relation_ids, list) or not all(
                isinstance(value, str) for value in relation_ids
            ):
                raise InvalidTicket()
            placeholders = ",".join("?" for _ in relation_ids)
            if relation_ids:
                connection.execute(
                    f"DELETE FROM observations WHERE relation_id IN ({placeholders})",
                    relation_ids,
                )
                cursor = connection.execute(
                    f"DELETE FROM relations WHERE relation_id IN ({placeholders})",
                    relation_ids,
                )
                removed = cursor.rowcount
            else:
                removed = 0
            revision = self.store.next_revision(connection)
            result = {
                "revision": revision,
                "removed_relation_ids": relation_ids,
                "removed_count": removed,
                "replayed": False,
            }
            self.store.record_replay(
                connection, "forget", idempotency_key, request, result
            )
            return result

    def status(self) -> dict[str, Any]:
        with self.store.connect() as connection:
            return {
                "version": __version__,
                "build_id": BUILD_ID,
                "revision": self.store.revision(connection),
                "database_path": str(self.store.database_path),
                "transport_session_state": False,
                "persistent_application_state": True,
                "http_publication_ready": False,
            }
