"""Canonical read projection over contexts, sources, and editable folders."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import sqlite3
import stat
import unicodedata
import uuid
from collections import defaultdict
from contextlib import ExitStack, closing, suppress
from pathlib import Path
from typing import Any

from .config import RuntimePaths, WorkspaceRuntimePaths, private_directory
from .contexts import CONTEXT_MAX_LIMIT
from .database import (
    connect,
    context_read_connection,
    encode_json,
    list_corpora,
    space_connection,
    space_read_connection,
    utc_now,
    workspace_read_connection,
)
from .errors import (
    BudgetExceededError,
    SpaceConflictError,
    SpaceNotFoundError,
    SpaceValidationError,
)
from .locking import (
    context_writer_lock,
    source_workspace_registry_lock,
    space_writer_lock,
    workspace_writer_lock,
    writer_lock,
)
from .migrations import backup_database_to_private_subdirectory

SPACE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
SPACE_AUDIENCES = {"local_cli", "external_mcp"}
SPACE_ACCESS_SCOPES = {"local_only", "remote_allowed"}
SPACE_PERMISSIONS = {"read_only", "read_write"}
SPACE_DEFAULT_LIMIT = 100
SPACE_MAX_LIMIT = 100
SPACE_MAX_OFFSET = 10_000
SPACE_MAX_SERIALIZED_BYTES = 1024 * 1024
SPACE_MIGRATION_MAX_PLAN_BYTES = 4 * 1024 * 1024
SPACE_CONTEXT_MAX_ITEMS = 1_000
SPACE_CONTEXT_MAX_AFFECTED_ITEMS = 200
SPACE_CONTEXT_MODES = {"auto", "build", "refresh"}
SPACE_CONTEXT_ACTIONS = {"append", "supersede", "advance_checkpoint"}
SPACE_REFERENCE_MAX_CHARS = 8_192
SPACE_MIGRATION_ID_RE = re.compile(r"^mig_[0-9a-f]{32}$")
SPACE_CUTOVER_ID_RE = re.compile(r"^cut_[0-9a-f]{32}$")
SOURCE_UID_RE = re.compile(r"^src_[0-9a-f]{32}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def encode_space_reference(kind: str, payload: dict[str, Any]) -> str:
    if kind not in {"read", "cursor"}:
        raise SpaceValidationError("unsupported Space reference kind")
    canonical = {"version": 1, **payload}
    encoded = base64.urlsafe_b64encode(encode_json(canonical).encode()).decode().rstrip("=")
    reference = f"{kind}1.{encoded}"
    if len(reference) > SPACE_REFERENCE_MAX_CHARS:
        raise BudgetExceededError("Space reference exceeds the serialized budget")
    return reference


def decode_space_reference(kind: str, reference: str) -> dict[str, Any]:
    prefix = f"{kind}1."
    if (
        kind not in {"read", "cursor"}
        or not isinstance(reference, str)
        or not reference.startswith(prefix)
        or len(reference) > SPACE_REFERENCE_MAX_CHARS
    ):
        raise SpaceValidationError("Space reference is invalid")
    encoded = reference[len(prefix) :]
    if not encoded:
        raise SpaceValidationError("Space reference is invalid")
    try:
        padded = encoded + ("=" * (-len(encoded) % 4))
        decoded = base64.b64decode(padded, altchars=b"-_", validate=True)
        payload = json.loads(decoded.decode())
    except (binascii.Error, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise SpaceValidationError("Space reference is invalid") from exc
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise SpaceValidationError("Space reference is invalid")
    return {key: value for key, value in payload.items() if key != "version"}


def normalize_space_id(space_id: str) -> str:
    if not isinstance(space_id, str):
        raise SpaceValidationError("space id must be a string")
    normalized = space_id.strip().lower().replace(" ", "-")
    if not SPACE_ID_RE.fullmatch(normalized):
        raise SpaceValidationError(
            "space id must be 1-64 lowercase letters, digits, dots, underscores, or hyphens",
            details={"space_id": space_id, "normalized": normalized},
        )
    return normalized


def _validate_audience(audience: str) -> None:
    if audience not in SPACE_AUDIENCES:
        raise SpaceValidationError(
            "unsupported space audience",
            details={"audience": audience, "allowed": sorted(SPACE_AUDIENCES)},
        )


def _validate_page(limit: int, offset: int) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= SPACE_MAX_LIMIT:
        raise SpaceValidationError(
            "space limit is outside the supported range",
            details={"limit": limit, "maximum": SPACE_MAX_LIMIT},
        )
    if (
        isinstance(offset, bool)
        or not isinstance(offset, int)
        or not 0 <= offset <= SPACE_MAX_OFFSET
    ):
        raise SpaceValidationError(
            "space offset is outside the supported range",
            details={"offset": offset, "maximum": SPACE_MAX_OFFSET},
        )


def _access_scope(execution_policy: str) -> str:
    # The v1 registries use execution_policy.  The canonical Space contract does
    # not expose that storage vocabulary.
    return "remote_allowed" if execution_policy == "external_host_allowed" else "local_only"


def _canonical_root(value: str) -> Path:
    return Path(value).expanduser().resolve(strict=False)


def _serialized_size(value: object) -> int:
    return len(encode_json(value).encode())


def _normalize_migration_policy(value: object) -> dict[str, dict[str, str]]:
    if value is None:
        return {
            "context_access_scopes": {},
            "connection_access_scopes": {},
        }
    if not isinstance(value, dict) or set(value) != {
        "context_access_scopes",
        "connection_access_scopes",
    }:
        raise SpaceValidationError(
            "migration policy must contain context_access_scopes and connection_access_scopes"
        )
    result: dict[str, dict[str, str]] = {}
    for field in ("context_access_scopes", "connection_access_scopes"):
        raw = value[field]
        if not isinstance(raw, dict):
            raise SpaceValidationError(
                "migration policy access scopes must be objects",
                details={"field": field},
            )
        normalized: dict[str, str] = {}
        for key, scope in raw.items():
            if not isinstance(key, str) or not key or len(key) > 160:
                raise SpaceValidationError(
                    "migration policy contains an invalid key",
                    details={"field": field},
                )
            if scope not in SPACE_ACCESS_SCOPES:
                raise SpaceValidationError(
                    "migration policy contains an invalid access scope",
                    details={
                        "field": field,
                        "key": key,
                        "allowed": sorted(SPACE_ACCESS_SCOPES),
                    },
                )
            normalized[key] = scope
        result[field] = normalized
    return result


def _normalize_identifier_policy(value: object) -> dict[str, dict[str, str]]:
    if value is None:
        return {"context_id_replacements": {}}
    if not isinstance(value, dict) or set(value) != {"context_id_replacements"}:
        raise SpaceValidationError("identifier cutover policy must contain context_id_replacements")
    raw = value["context_id_replacements"]
    if not isinstance(raw, dict):
        raise SpaceValidationError("context_id_replacements must be an object")
    replacements: dict[str, str] = {}
    for old_id, new_id in raw.items():
        if not isinstance(old_id, str) or not old_id:
            raise SpaceValidationError("context_id_replacements contains an invalid source id")
        if not isinstance(new_id, str):
            raise SpaceValidationError("context_id_replacements contains an invalid target id")
        replacements[old_id] = normalize_space_id(new_id)
    return {"context_id_replacements": replacements}


class SpaceService:
    """Project the current registries as one Space-oriented read model.

    This layer deliberately has no legacy identifier aliases.  A linked source
    is addressed through its context-derived ``space_id``; its registry key is
    not part of the canonical response.
    """

    def __init__(
        self,
        data_root: Path,
        *,
        contexts: Any,
        context_skills: Any,
        workspaces: Any,
    ) -> None:
        self.data_root = data_root
        self.contexts = contexts
        self.context_skills = context_skills
        self.workspaces = workspaces

    def _contexts(self, *, state: str) -> list[dict[str, Any]]:
        contexts: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = self.contexts.list(
                state=state,
                limit=CONTEXT_MAX_LIMIT,
                offset=offset,
                audience="local_cli",
            )
            contexts.extend(page["contexts"])
            next_offset = page["next_offset"]
            if next_offset is None:
                return contexts
            offset = int(next_offset)

    def _active_contexts(self) -> list[dict[str, Any]]:
        return self._contexts(state="active")

    def _work_folders(self) -> list[dict[str, Any]]:
        return list(self.workspaces.list(audience="local_cli")["work_folders"])

    @staticmethod
    def _connection_groups(
        *,
        corpora: list[dict[str, Any]],
        work_folders: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        groups: dict[Path, dict[str, Any]] = {}

        for corpus in corpora:
            root = _canonical_root(corpus["source_root"])
            group = groups.setdefault(
                root,
                {
                    "root": root,
                    "sources": [],
                    "work_folders": [],
                    "access_scopes": set(),
                },
            )
            group["sources"].append(corpus)
            group["access_scopes"].add(_access_scope(corpus["execution_policy"]))

        for work_folder in work_folders:
            root = _canonical_root(work_folder["root_path"])
            group = groups.setdefault(
                root,
                {
                    "root": root,
                    "sources": [],
                    "work_folders": [],
                    "access_scopes": set(),
                },
            )
            group["work_folders"].append(work_folder)
            group["access_scopes"].add(_access_scope(work_folder["execution_policy"]))

        return sorted(
            groups.values(),
            key=lambda group: (
                0 if group["work_folders"] else 1,
                str(group["root"]),
            ),
        )

    @staticmethod
    def _assign_connection_ids(groups: list[dict[str, Any]]) -> None:
        if len(groups) == 1:
            groups[0]["connection_id"] = "main"
            return

        counters: defaultdict[str, int] = defaultdict(int)
        for group in groups:
            if group["sources"] and group["work_folders"]:
                prefix = "main"
            elif group["work_folders"]:
                prefix = "work"
            else:
                prefix = "source"
            counters[prefix] += 1
            suffix = counters[prefix]
            group["connection_id"] = prefix if suffix == 1 else f"{prefix}-{suffix}"

    @staticmethod
    def _project_connection(
        group: dict[str, Any],
        *,
        audience: str,
    ) -> dict[str, Any] | None:
        scopes = set(group["access_scopes"])
        access_scope = "remote_allowed" if scopes == {"remote_allowed"} else "local_only"
        if audience == "external_mcp" and access_scope != "remote_allowed":
            return None

        sources = list(group["sources"])
        work_folders = list(group["work_folders"])
        roles = [
            role
            for role, present in (
                ("source", bool(sources)),
                ("work", bool(work_folders)),
            )
            if present
        ]
        permission = "read_write" if work_folders else "read_only"
        work_folder = work_folders[0] if work_folders else None
        if work_folder is not None:
            display_name = work_folder["display_name"]
            connection_state = work_folder["connection_state"]
            connection_reason = work_folder["connection_reason"]
            current_file = work_folder["current_file"]
            generation = work_folder["generation"]
        else:
            display_name = group["root"].name
            connection_state = "registered"
            connection_reason = None
            current_file = None
            generation = 1

        result: dict[str, Any] = {
            "connection_id": group["connection_id"],
            "display_name": display_name,
            "roles": roles,
            "access_scope": access_scope,
            "permission": permission,
            "index_mode": "indexed" if sources else "not_indexed",
            "connection_state": connection_state,
            "connection_reason": connection_reason,
            "current_file": current_file,
            "generation": generation,
            "write_state": (
                "unknown" if work_folder is not None and connection_state == "connected" else None
            ),
            "configuration_state": ("ready" if len(scopes) == 1 else "access_scope_conflict"),
        }
        if audience == "local_cli":
            result["location"] = str(group["root"])
        return result

    @staticmethod
    def _context_access_scope(connections: list[dict[str, Any]]) -> str:
        remote_work = any(
            "work" in connection["roles"] and connection["access_scope"] == "remote_allowed"
            for connection in connections
        )
        if remote_work:
            return "remote_allowed"
        source_connections = [
            connection for connection in connections if "source" in connection["roles"]
        ]
        if source_connections and all(
            connection["access_scope"] == "remote_allowed" for connection in source_connections
        ):
            return "remote_allowed"
        return "local_only"

    def _project_space(
        self,
        *,
        space_id: str,
        context: dict[str, Any] | None,
        corpora: list[dict[str, Any]],
        work_folders: list[dict[str, Any]],
        audience: str,
    ) -> dict[str, Any] | None:
        groups = self._connection_groups(corpora=corpora, work_folders=work_folders)
        self._assign_connection_ids(groups)
        all_connections = [
            projected
            for group in groups
            if (projected := self._project_connection(group, audience="local_cli")) is not None
        ]
        context_access_scope = (
            self._context_access_scope(all_connections) if context is not None else None
        )
        visible_connections = [
            projected
            for group in groups
            if (projected := self._project_connection(group, audience=audience)) is not None
        ]
        remote_visible = bool(visible_connections) or context_access_scope == "remote_allowed"
        if audience == "external_mcp" and not remote_visible:
            return None

        primary_work = next(
            (connection for connection in visible_connections if "work" in connection["roles"]),
            None,
        )
        result: dict[str, Any] = {
            "space_id": space_id,
            "_context_id": context["context_id"] if context is not None else None,
            "display_name": context["title"] if context is not None else space_id,
            "state": context["state"] if context is not None else "active",
            "access_scope": (
                "remote_allowed"
                if remote_visible
                and audience == "external_mcp"
                or context_access_scope == "remote_allowed"
                or any(
                    connection["access_scope"] == "remote_allowed"
                    for connection in visible_connections
                )
                else "local_only"
            ),
            "context": (
                {
                    "title": context["title"],
                    "purpose": context["purpose"],
                    "access_scope": context_access_scope,
                    "version": context["version"],
                    "updated_at": context["updated_at"],
                    "skill": self.context_skills.read(
                        context_id=context["context_id"],
                        audience=audience,
                        include_instructions=False,
                        require_context=False,
                    ),
                }
                if context is not None
                else None
            ),
            "connections": visible_connections,
            "primary_work_connection_id": (
                primary_work["connection_id"] if primary_work is not None else None
            ),
            "current_file": (primary_work["current_file"] if primary_work is not None else None),
        }
        return result

    def _legacy_spaces(self, *, audience: str) -> list[dict[str, Any]]:
        _validate_audience(audience)
        corpora = list_corpora(self.data_root)
        corpora_by_id = {corpus["corpus_id"]: corpus for corpus in corpora}
        contexts = self._active_contexts()
        work_folders = self._work_folders()
        work_by_context: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        for work_folder in work_folders:
            work_by_context[work_folder["context_id"]].append(work_folder)

        linked_corpus_ids: set[str] = set()
        projected: list[dict[str, Any]] = []
        occupied_ids: set[str] = set()
        for context in contexts:
            space_id = normalize_space_id(context["context_id"])
            if space_id in occupied_ids:
                raise SpaceConflictError(
                    "multiple active records resolve to the same space id",
                    details={"space_id": space_id},
                )
            occupied_ids.add(space_id)
            linked = [
                corpora_by_id[corpus_id]
                for corpus_id in context["corpus_ids"]
                if corpus_id in corpora_by_id
            ]
            linked_corpus_ids.update(corpus["corpus_id"] for corpus in linked)
            space = self._project_space(
                space_id=space_id,
                context=context,
                corpora=linked,
                work_folders=work_by_context.get(context["context_id"], []),
                audience=audience,
            )
            if space is not None:
                projected.append(space)

        for corpus in corpora:
            if corpus["corpus_id"] in linked_corpus_ids:
                continue
            space_id = normalize_space_id(corpus["corpus_id"])
            if space_id in occupied_ids:
                raise SpaceConflictError(
                    "an unlinked source conflicts with an active space id",
                    details={"space_id": space_id},
                )
            occupied_ids.add(space_id)
            space = self._project_space(
                space_id=space_id,
                context=None,
                corpora=[corpus],
                work_folders=[],
                audience=audience,
            )
            if space is not None:
                projected.append(space)

        return sorted(projected, key=lambda space: space["space_id"])

    @staticmethod
    def _receipt_connection_bindings(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
        bindings: dict[str, dict[str, Any]] = {}
        for space in plan.get("spaces", []):
            for connection in space.get("connections", []):
                connection_uid = connection.get("connection_uid")
                if isinstance(connection_uid, str):
                    bindings[connection_uid] = connection
        return bindings

    def _stored_spaces(self, *, audience: str) -> list[dict[str, Any]] | None:
        """Read the materialized registry when a complete receipt owns it.

        The shadow registry is authoritative for canonical policy.  The old
        registries remain temporary backing stores for Context content and live
        work-folder state until identifier cutover, but their execution_policy
        values no longer derive Access Scope after this switch.
        """

        path = self.data_root / "spaces.sqlite3"
        if not path.exists():
            return None

        with space_read_connection(self.data_root) as connection:
            receipt = connection.execute(
                """
                SELECT * FROM space_migration_receipts
                WHERE state = 'complete'
                ORDER BY created_at, migration_id
                LIMIT 1
                """
            ).fetchone()
            counts = self._registry_counts(connection)
            if receipt is None:
                if any(counts.values()):
                    raise SpaceConflictError(
                        "canonical Space registry has no complete migration receipt",
                        details={"reason": "unmanaged_registry_state", "counts": counts},
                    )
                return None

            try:
                plan = json.loads(receipt["plan_json"])
            except (TypeError, ValueError) as exc:
                raise SpaceConflictError(
                    "canonical Space registry receipt is invalid",
                    details={
                        "reason": "invalid_migration_receipt",
                        "migration_id": receipt["migration_id"],
                    },
                ) from exc
            if not isinstance(plan, dict) or not self._registry_matches_plan(connection, plan):
                raise SpaceConflictError(
                    "canonical Space registry changed after migration",
                    details={
                        "reason": "space_registry_drift",
                        "migration_id": receipt["migration_id"],
                    },
                )

            space_rows = [
                dict(row)
                for row in connection.execute("SELECT * FROM spaces ORDER BY space_id").fetchall()
            ]
            connection_rows = [
                dict(row)
                for row in connection.execute(
                    """
                SELECT connection.*, resource.root_path, resource.locator_json
                FROM connections AS connection
                JOIN resources AS resource
                  ON resource.resource_uid = connection.resource_uid
                ORDER BY connection.space_uid, connection.connection_id
                """
                ).fetchall()
            ]

        contexts = {
            context["context_id"]: context
            for state in ("active", "archived")
            for context in self._contexts(state=state)
        }
        work_folders = {
            work_folder["workspace_id"]: work_folder for work_folder in self._work_folders()
        }
        receipt_bindings = self._receipt_connection_bindings(plan)
        rows_by_space: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in connection_rows:
            rows_by_space[row["space_uid"]].append(row)

        projected: list[dict[str, Any]] = []
        for row in space_rows:
            context_id = row["context_id"]
            context = contexts.get(context_id) if context_id is not None else None
            if context_id is not None and context is None:
                raise SpaceConflictError(
                    "canonical Space Context backing is missing",
                    details={
                        "reason": "context_backing_missing",
                        "space_id": row["space_id"],
                    },
                )

            all_connections: list[dict[str, Any]] = []
            for connection_row in rows_by_space.get(row["space_uid"], []):
                source_role = bool(connection_row["source_role"])
                work_role = bool(connection_row["work_role"])
                roles = [
                    role
                    for role, present in (("source", source_role), ("work", work_role))
                    if present
                ]
                connection_state = "registered"
                connection_reason = None
                current_file = None
                connection_generation = int(connection_row["generation"])
                if work_role:
                    try:
                        locator = json.loads(connection_row["locator_json"])
                    except (TypeError, ValueError):
                        locator = {}
                    locator_workspace_id = (
                        locator.get("workspace_id") if isinstance(locator, dict) else None
                    )
                    if isinstance(locator_workspace_id, str):
                        workspace_ids = [locator_workspace_id]
                    else:
                        receipt_binding = receipt_bindings.get(
                            connection_row["connection_uid"],
                            {},
                        )
                        workspace_ids = receipt_binding.get("workspace_registry_ids", [])
                    if not isinstance(workspace_ids, list) or len(workspace_ids) != 1:
                        connection_state = "unavailable"
                        connection_reason = "workspace_binding_missing"
                        if connection_row["current_relative_path"] is not None:
                            current_file = {
                                "relative_path": connection_row["current_relative_path"],
                                "state": "unavailable",
                                "reason": connection_reason,
                            }
                    else:
                        work_folder = work_folders.get(workspace_ids[0])
                        if work_folder is None:
                            connection_state = "unavailable"
                            connection_reason = "workspace_missing"
                            if connection_row["current_relative_path"] is not None:
                                current_file = {
                                    "relative_path": connection_row["current_relative_path"],
                                    "state": "unavailable",
                                    "reason": connection_reason,
                                }
                        else:
                            connection_state = work_folder["connection_state"]
                            connection_reason = work_folder["connection_reason"]
                            current_file = work_folder["current_file"]
                            connection_generation = int(work_folder["generation"])

                projected_connection: dict[str, Any] = {
                    "connection_id": connection_row["connection_id"],
                    "display_name": connection_row["display_name"],
                    "roles": roles,
                    "access_scope": connection_row["access_scope"],
                    "permission": connection_row["permission"],
                    "index_mode": connection_row["index_mode"],
                    "connection_state": connection_state,
                    "connection_reason": connection_reason,
                    "current_file": current_file,
                    "generation": connection_generation,
                    "write_state": (
                        "unknown" if work_role and connection_state == "connected" else None
                    ),
                    "configuration_state": (
                        "ready" if connection_reason is None else "backing_unavailable"
                    ),
                    "_primary_work": bool(connection_row["primary_work"]),
                }
                if audience == "local_cli":
                    projected_connection["location"] = connection_row["root_path"]
                all_connections.append(projected_connection)

            visible_connections = [
                connection
                for connection in all_connections
                if audience == "local_cli" or connection["access_scope"] == "remote_allowed"
            ]
            context_access_scope = row["context_access_scope"] if context is not None else None
            remote_visible = bool(visible_connections) or context_access_scope == "remote_allowed"
            if audience == "external_mcp" and not remote_visible:
                continue

            primary_work = next(
                (
                    connection
                    for connection in visible_connections
                    if connection.get("_primary_work") is True
                ),
                None,
            )
            for connection in visible_connections:
                connection.pop("_primary_work", None)
            projected.append(
                {
                    "space_id": row["space_id"],
                    "_context_id": context_id,
                    "display_name": row["display_name"],
                    "state": context["state"] if context is not None else row["state"],
                    "access_scope": (
                        "remote_allowed"
                        if context_access_scope == "remote_allowed"
                        or any(
                            item["access_scope"] == "remote_allowed" for item in visible_connections
                        )
                        else "local_only"
                    ),
                    "context": (
                        {
                            "title": context["title"],
                            "purpose": context["purpose"],
                            "access_scope": context_access_scope,
                            "version": context["version"],
                            "updated_at": context["updated_at"],
                            "skill": self.context_skills.read(
                                context_id=context["context_id"],
                                audience=audience,
                                include_instructions=False,
                                require_context=False,
                            ),
                        }
                        if context is not None
                        else None
                    ),
                    "connections": visible_connections,
                    "primary_work_connection_id": (
                        primary_work["connection_id"] if primary_work is not None else None
                    ),
                    "current_file": (
                        primary_work["current_file"] if primary_work is not None else None
                    ),
                }
            )
        return projected

    def _spaces(self, *, audience: str) -> list[dict[str, Any]]:
        _validate_audience(audience)
        stored = self._stored_spaces(audience=audience)
        if stored is not None:
            return stored
        return self._legacy_spaces(audience=audience)

    @staticmethod
    def _public_space(space: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in space.items() if not key.startswith("_")}

    def resolve_connection(
        self,
        *,
        space_id: str,
        connection_id: str | None,
        audience: str,
        capability: str,
    ) -> dict[str, Any]:
        """Resolve one canonical Connection to private v1 backing records.

        This is an internal service boundary.  Callers may return ``space`` and
        ``connection`` but must never return the underscore-prefixed backing
        fields.
        """

        _validate_audience(audience)
        if capability not in {"read", "write", "source"}:
            raise SpaceValidationError("unsupported Space Connection capability")
        self._complete_registry_plan()
        normalized_space_id = normalize_space_id(space_id)
        space = next(
            (
                candidate
                for candidate in self._spaces(audience=audience)
                if candidate["space_id"] == normalized_space_id
            ),
            None,
        )
        if space is None:
            raise SpaceNotFoundError(
                "space does not exist",
                details={"space_id": normalized_space_id},
            )
        visible = list(space["connections"])
        selected_id: str | None
        if connection_id is not None:
            selected_id = normalize_space_id(connection_id)
        elif capability == "write":
            selected_id = space.get("primary_work_connection_id")
        elif len(visible) == 1:
            selected_id = visible[0]["connection_id"]
        else:
            selected_id = None
        if selected_id is None:
            raise SpaceValidationError(
                "a Connection must be selected for this Space operation",
                details={
                    "space_id": normalized_space_id,
                    "available_connection_ids": [item["connection_id"] for item in visible],
                },
            )
        selected = next(
            (item for item in visible if item["connection_id"] == selected_id),
            None,
        )
        if selected is None:
            raise SpaceNotFoundError(
                "Space Connection does not exist",
                details={
                    "space_id": normalized_space_id,
                    "connection_id": selected_id,
                },
            )
        roles = set(selected["roles"])
        if capability == "source" and (
            "source" not in roles or selected["index_mode"] != "indexed"
        ):
            raise SpaceValidationError(
                "selected Connection has no searchable Source role",
                details={
                    "space_id": normalized_space_id,
                    "connection_id": selected_id,
                },
            )
        if capability == "write" and (
            "work" not in roles or selected["permission"] != "read_write"
        ):
            raise SpaceValidationError(
                "selected Connection is not writable",
                details={
                    "space_id": normalized_space_id,
                    "connection_id": selected_id,
                },
            )

        receipt, registry_plan = self._complete_registry_plan()
        del receipt
        plan_connections = self._receipt_connection_bindings(registry_plan)
        plan_resources = {
            item["resource_uid"]: item
            for item in registry_plan.get("resources", [])
            if isinstance(item, dict) and isinstance(item.get("resource_uid"), str)
        }
        with space_read_connection(self.data_root) as connection:
            row = connection.execute(
                """
                SELECT connection.*, resource.locator_json
                FROM connections AS connection
                JOIN resources AS resource
                  ON resource.resource_uid = connection.resource_uid
                JOIN spaces AS space ON space.space_uid = connection.space_uid
                WHERE space.space_id = ? AND connection.connection_id = ?
                """,
                (normalized_space_id, selected_id),
            ).fetchone()
        if row is None:
            raise SpaceConflictError(
                "canonical Connection backing is missing",
                details={
                    "space_id": normalized_space_id,
                    "connection_id": selected_id,
                    "reason": "connection_backing_missing",
                },
            )
        try:
            locator = json.loads(row["locator_json"])
        except (TypeError, ValueError):
            locator = {}
        if not isinstance(locator, dict):
            locator = {}
        planned_connection = plan_connections.get(row["connection_uid"], {})
        planned_resource = plan_resources.get(row["resource_uid"], {})

        workspace_ids: list[str] = []
        if isinstance(locator.get("workspace_id"), str):
            workspace_ids.append(locator["workspace_id"])
        raw_workspace_ids = planned_connection.get("workspace_registry_ids", [])
        if isinstance(raw_workspace_ids, list):
            workspace_ids.extend(value for value in raw_workspace_ids if isinstance(value, str))
        existing_workspaces = {item["workspace_id"] for item in self._work_folders()}
        workspace_ids = sorted(set(workspace_ids) & existing_workspaces)

        source_ids: list[str] = []
        if isinstance(locator.get("source_uid"), str):
            source_ids.append(locator["source_uid"])
        raw_source_ids = planned_resource.get("source_registry_ids", [])
        if isinstance(raw_source_ids, list):
            source_ids.extend(value for value in raw_source_ids if isinstance(value, str))
        existing_sources = {item["corpus_id"] for item in list_corpora(self.data_root)}
        source_ids = sorted(set(source_ids) & existing_sources)

        if "work" in roles and len(workspace_ids) != 1:
            raise SpaceConflictError(
                "canonical Work Connection backing is unavailable",
                details={
                    "space_id": normalized_space_id,
                    "connection_id": selected_id,
                    "reason": "workspace_binding_missing",
                },
            )
        if "source" in roles and not source_ids:
            raise SpaceConflictError(
                "canonical Source Connection backing is unavailable",
                details={
                    "space_id": normalized_space_id,
                    "connection_id": selected_id,
                    "reason": "source_binding_missing",
                },
            )
        return {
            "space": self._public_space(space),
            "connection": dict(selected),
            "_workspace_id": workspace_ids[0] if workspace_ids else None,
            "_source_ids": source_ids,
        }

    def resolve_source_connections(
        self,
        *,
        space_id: str,
        connection_id: str | None,
        audience: str,
    ) -> list[dict[str, Any]]:
        _validate_audience(audience)
        normalized_space_id = normalize_space_id(space_id)
        if connection_id is not None:
            return [
                self.resolve_connection(
                    space_id=normalized_space_id,
                    connection_id=connection_id,
                    audience=audience,
                    capability="source",
                )
            ]
        self._complete_registry_plan()
        space = next(
            (
                candidate
                for candidate in self._spaces(audience=audience)
                if candidate["space_id"] == normalized_space_id
            ),
            None,
        )
        if space is None:
            raise SpaceNotFoundError(
                "space does not exist",
                details={"space_id": normalized_space_id},
            )
        source_connection_ids = [
            item["connection_id"]
            for item in space["connections"]
            if "source" in item["roles"] and item["index_mode"] == "indexed"
        ]
        if not source_connection_ids:
            raise SpaceValidationError(
                "Space has no searchable Source Connection",
                details={"space_id": normalized_space_id},
            )
        return [
            self.resolve_connection(
                space_id=normalized_space_id,
                connection_id=source_connection_id,
                audience=audience,
                capability="source",
            )
            for source_connection_id in source_connection_ids
        ]

    @staticmethod
    def _context_status(detail: dict[str, Any]) -> tuple[str, str | None]:
        for observation in detail.get("corpus_observations", []):
            current = observation.get("current", {})
            if current.get("available") is False:
                return ("error", "source_unavailable")

        for item in detail.get("items", []):
            for source in [*item.get("sources", []), *item.get("external_sources", [])]:
                if source.get("dependency_state") not in {None, "valid"}:
                    return ("refresh_needed", "source_changed")
                if source.get("freshness_state") not in {None, "valid"}:
                    return ("refresh_needed", "source_changed")

        for observation in detail.get("corpus_observations", []):
            change = observation.get("inventory_change", {})
            if (
                change.get("checkpoint_missing")
                or change.get("inventory_changed") is True
                or change.get("inventory_hash_changed") is True
                or change.get("unclassified_inventory_change")
                or change.get("change_candidates")
                or change.get("mapping_changes")
            ):
                return ("refresh_needed", "source_changed")
        return ("ready", None)

    def _attach_context_detail(
        self,
        space: dict[str, Any],
        *,
        audience: str,
        limit: int,
        offset: int,
    ) -> None:
        summary = space["context"]
        if summary is None:
            return
        context_id = space.get("_context_id")
        if not isinstance(context_id, str):
            raise SpaceConflictError(
                "canonical Space Context binding is missing",
                details={
                    "reason": "context_backing_missing",
                    "space_id": space["space_id"],
                },
            )
        detail = self.contexts.read(
            context_id=context_id,
            state=space["state"],
            include_history=False,
            limit=limit,
            offset=offset,
            audience="local_cli",
            view="restricted",
        )
        status, reason = self._context_status(detail)
        items = [
            {
                "item_id": item["item_id"],
                "kind": item["kind"],
                "body_text": item["body_text"],
                "attributes": item["attributes"],
                "created_at": item["created_at"],
            }
            for item in detail["items"]
        ]
        summary.update(
            {
                "status": status,
                "status_reason": reason,
                "items": items,
                "offset": detail["offset"],
                "limit": detail["limit"],
                "returned_count": len(items),
                "has_more": detail["has_more"],
                "next_offset": detail["next_offset"],
                "skill": self.context_skills.read(
                    context_id=context_id,
                    audience=audience,
                    include_instructions=True,
                    require_context=False,
                ),
            }
        )
        if audience == "local_cli":
            summary["total_matching"] = detail["total_matching"]

    def list(
        self,
        *,
        audience: str = "local_cli",
        limit: int = SPACE_DEFAULT_LIMIT,
        offset: int = 0,
    ) -> dict[str, Any]:
        _validate_page(limit, offset)
        spaces = self._spaces(audience=audience)
        page = [self._public_space(space) for space in spaces[offset : offset + limit]]
        next_offset = offset + len(page)
        response = {
            "offset": offset,
            "limit": limit,
            "returned_count": len(page),
            "has_more": next_offset < len(spaces),
            "next_offset": next_offset if next_offset < len(spaces) else None,
            "spaces": page,
        }
        if _serialized_size(response) > SPACE_MAX_SERIALIZED_BYTES:
            raise BudgetExceededError("space list exceeds the response budget")
        return response

    def get(
        self,
        *,
        space_id: str,
        audience: str = "local_cli",
        context_limit: int = SPACE_DEFAULT_LIMIT,
        context_offset: int = 0,
    ) -> dict[str, Any]:
        normalized_id = normalize_space_id(space_id)
        _validate_page(context_limit, context_offset)
        space = next(
            (
                candidate
                for candidate in self._spaces(audience=audience)
                if candidate["space_id"] == normalized_id
            ),
            None,
        )
        if space is None:
            raise SpaceNotFoundError(
                "space does not exist",
                details={"space_id": normalized_id},
            )
        self._attach_context_detail(
            space,
            audience=audience,
            limit=context_limit,
            offset=context_offset,
        )
        response = {"space": self._public_space(space)}
        if _serialized_size(response) > SPACE_MAX_SERIALIZED_BYTES:
            raise BudgetExceededError("space detail exceeds the response budget")
        return response

    def _context_space(self, space_id: str) -> dict[str, Any]:
        normalized_id = normalize_space_id(space_id)
        space = next(
            (
                candidate
                for candidate in self._spaces(audience="local_cli")
                if candidate["space_id"] == normalized_id
            ),
            None,
        )
        if space is None:
            raise SpaceNotFoundError(
                "space does not exist",
                details={"space_id": normalized_id},
            )
        context_id = space.get("_context_id")
        if not isinstance(context_id, str) or space.get("context") is None:
            raise SpaceValidationError(
                "space has no Context to build or refresh",
                details={"space_id": normalized_id, "reason": "context_missing"},
            )
        if space.get("state") != "active":
            raise SpaceConflictError(
                "archived Space Context cannot be built or refreshed",
                details={"space_id": normalized_id, "reason": "space_archived"},
            )
        return space

    def _source_connection_ids(self, space: dict[str, Any]) -> dict[str, str]:
        """Map private source registry IDs to canonical Connection IDs.

        Before identifier cutover the immutable migration receipt owns this
        mapping.  After cutover the resource locator owns the new source UID.
        Neither representation is returned by the Remote Space projection.
        """

        receipt, registry_plan = self._complete_registry_plan()
        del receipt
        resources_from_plan = {
            item["resource_uid"]: item
            for item in registry_plan.get("resources", [])
            if isinstance(item, dict) and isinstance(item.get("resource_uid"), str)
        }
        with space_read_connection(self.data_root) as connection:
            row = connection.execute(
                "SELECT space_uid FROM spaces WHERE space_id = ?",
                (space["space_id"],),
            ).fetchone()
            if row is None:
                raise SpaceConflictError(
                    "canonical Space registry is missing the selected Space",
                    details={
                        "space_id": space["space_id"],
                        "reason": "space_registry_drift",
                    },
                )
            connection_rows = connection.execute(
                """
                SELECT connection.connection_id, connection.source_role,
                       resource.resource_uid, resource.locator_json
                FROM connections AS connection
                JOIN resources AS resource
                  ON resource.resource_uid = connection.resource_uid
                WHERE connection.space_uid = ?
                ORDER BY connection.connection_id
                """,
                (row["space_uid"],),
            ).fetchall()

        result: dict[str, str] = {}
        for row in connection_rows:
            if not row["source_role"]:
                continue
            source_ids: list[str] = []
            try:
                locator = json.loads(row["locator_json"])
            except (TypeError, ValueError):
                locator = {}
            if isinstance(locator, dict) and isinstance(locator.get("source_uid"), str):
                source_ids.append(locator["source_uid"])
            planned_resource = resources_from_plan.get(row["resource_uid"], {})
            planned_source_ids = planned_resource.get("source_registry_ids", [])
            if isinstance(planned_source_ids, list):
                source_ids.extend(
                    source_id for source_id in planned_source_ids if isinstance(source_id, str)
                )
            for source_id in source_ids:
                previous = result.setdefault(source_id, row["connection_id"])
                if previous != row["connection_id"]:
                    raise SpaceConflictError(
                        "one Context source resolves to multiple Connections",
                        details={
                            "space_id": space["space_id"],
                            "reason": "source_connection_ambiguous",
                        },
                    )
        return result

    def _context_detail_all_items(self, context_id: str) -> dict[str, Any]:
        first = self.contexts.read(
            context_id=context_id,
            state="active",
            include_history=False,
            limit=CONTEXT_MAX_LIMIT,
            offset=0,
            audience="local_cli",
            view="restricted",
        )
        total = int(first["total_matching"])
        if total > SPACE_CONTEXT_MAX_ITEMS:
            return {**first, "items": first["items"], "items_truncated": True}
        items = list(first["items"])
        offset = len(items)
        while offset < total:
            page = self.contexts.read(
                context_id=context_id,
                state="active",
                include_history=False,
                limit=CONTEXT_MAX_LIMIT,
                offset=offset,
                audience="local_cli",
                view="restricted",
            )
            items.extend(page["items"])
            offset += len(page["items"])
            if not page["items"]:
                raise SpaceConflictError(
                    "Context pagination stopped before all active items were read",
                    details={"reason": "context_pagination_changed"},
                )
        return {**first, "items": items, "items_truncated": False}

    def context_plan(
        self,
        *,
        space_id: str,
        mode: str = "auto",
    ) -> dict[str, Any]:
        """Plan a local Context build or refresh without changing Context state."""

        if mode not in SPACE_CONTEXT_MODES:
            raise SpaceValidationError(
                "unsupported Context maintenance mode",
                details={"mode": mode, "allowed": sorted(SPACE_CONTEXT_MODES)},
            )
        # Build/refresh depends on explicit canonical Access Scope rather than
        # the legacy source execution policy, so require the stored registry.
        self._complete_registry_plan()
        space = self._context_space(space_id)
        context_id = space["_context_id"]
        detail = self._context_detail_all_items(context_id)
        total_items = int(detail["total_matching"])
        resolved_mode = "build" if total_items == 0 else "refresh"
        if mode != "auto" and mode != resolved_mode:
            raise SpaceConflictError(
                "requested Context maintenance mode does not match current Context state",
                details={
                    "space_id": space["space_id"],
                    "requested_mode": mode,
                    "current_mode": resolved_mode,
                    "reason": "context_mode_changed",
                },
            )

        source_connections = self._source_connection_ids(space)
        blockers: list[dict[str, Any]] = []
        if detail.get("items_truncated"):
            blockers.append(
                {
                    "code": "context_item_limit_exceeded",
                    "item_count": total_items,
                    "maximum_items": SPACE_CONTEXT_MAX_ITEMS,
                }
            )

        affected_items: list[dict[str, Any]] = []
        affected_truncated = False
        for item in detail["items"]:
            issues: list[dict[str, Any]] = []
            for source in item.get("sources", []):
                dependency_state = source.get("dependency_state")
                freshness_state = source.get("freshness_state")
                if dependency_state == "valid" and freshness_state in {None, "valid"}:
                    continue
                issues.append(
                    {
                        "source_kind": "indexed_file",
                        "connection_id": source_connections.get(source.get("corpus_id")),
                        "source_unit_id": source.get("source_unit_id"),
                        "dependency_state": dependency_state,
                        "freshness_state": freshness_state,
                    }
                )
            for source in item.get("external_sources", []):
                dependency_state = source.get("dependency_state")
                freshness_state = source.get("freshness_state")
                if dependency_state == "valid" and freshness_state in {None, "valid"}:
                    continue
                issues.append(
                    {
                        "source_kind": "provider_record",
                        "connection_id": source_connections.get(source.get("corpus_id")),
                        "binding_id": source.get("binding_id"),
                        "external_id": source.get("external_id"),
                        "dependency_state": dependency_state,
                        "freshness_state": freshness_state,
                    }
                )
            if not issues:
                continue
            if len(affected_items) >= SPACE_CONTEXT_MAX_AFFECTED_ITEMS:
                affected_truncated = True
                continue
            affected_items.append(
                {
                    "item_id": item["item_id"],
                    "client_ref": item["client_ref"],
                    "kind": item["kind"],
                    "body_text": item["body_text"],
                    "attributes": item["attributes"],
                    "source_issues": issues,
                }
            )
        if affected_truncated:
            blockers.append(
                {
                    "code": "affected_item_limit_exceeded",
                    "maximum_items": SPACE_CONTEXT_MAX_AFFECTED_ITEMS,
                }
            )

        source_reviews: list[dict[str, Any]] = []
        checkpoint_observations: list[dict[str, Any]] = []
        inventory_review_required = False
        for observation in detail.get("corpus_observations", []):
            corpus_id = observation["corpus_id"]
            connection_id = source_connections.get(corpus_id)
            if connection_id is None:
                blockers.append(
                    {
                        "code": "context_source_connection_missing",
                        "source_registry_id": corpus_id,
                    }
                )
            current = observation["current"]
            if current.get("available") is False:
                blockers.append(
                    {
                        "code": "context_source_unavailable",
                        "connection_id": connection_id,
                    }
                )
            elif not all(
                isinstance(current.get(field), str)
                for field in ("latest_scan_id", "current_snapshot_id", "inventory_hash")
            ):
                blockers.append(
                    {
                        "code": "context_source_not_indexed",
                        "connection_id": connection_id,
                    }
                )
            else:
                checkpoint_observations.append(
                    {
                        "corpus_id": corpus_id,
                        "observed_scan_id": current["latest_scan_id"],
                        "observed_snapshot_id": current["current_snapshot_id"],
                        "observed_inventory_hash": current["inventory_hash"],
                    }
                )
            change = observation["inventory_change"]
            requires_review = bool(
                change.get("checkpoint_missing")
                or change.get("inventory_changed") is True
                or change.get("inventory_hash_changed") is True
                or change.get("unclassified_inventory_change")
                or change.get("change_candidates")
                or change.get("mapping_changes")
            )
            inventory_review_required = inventory_review_required or requires_review
            source_reviews.append(
                {
                    "connection_id": connection_id,
                    "checkpoint": observation["checkpoint"],
                    "current": current,
                    "change": change,
                    "review_required": requires_review,
                }
            )

        if blockers:
            context_status, context_status_reason = "error", blockers[0]["code"]
        elif resolved_mode == "build":
            context_status, context_status_reason = "refresh_needed", "context_empty"
        elif affected_items or inventory_review_required:
            context_status, context_status_reason = "refresh_needed", "source_changed"
        else:
            context_status, context_status_reason = "ready", None

        if resolved_mode == "build":
            next_action = "review local Sources and append confirmed Context items"
        elif affected_items:
            next_action = "review and supersede only affected Context items"
        elif inventory_review_required:
            next_action = "advance the checkpoint after local review"
        else:
            next_action = "no Context change is needed"

        canonical = {
            "plan_version": 1,
            "space_id": space["space_id"],
            "context_id": context_id,
            "mode": resolved_mode,
            "context_version": detail["context"]["version"],
            "context_item_count": total_items,
            "context_status": context_status,
            "context_status_reason": context_status_reason,
            "affected_items": affected_items,
            "affected_items_truncated": affected_truncated,
            "source_reviews": source_reviews,
            "inventory_review_required": inventory_review_required,
            "checkpoint_payload": {"observations": checkpoint_observations},
            "blockers": blockers,
        }
        input_sha256 = hashlib.sha256(encode_json(canonical).encode()).hexdigest()
        response = {
            "operation": "space-context-build-refresh-v1",
            "input_sha256": input_sha256,
            "ready": not blockers,
            **canonical,
            "changes_context": False,
            "reads_source_content": False,
            "requires_local_review": context_status != "ready",
            "next_action": next_action,
        }
        if _serialized_size(response) > SPACE_MIGRATION_MAX_PLAN_BYTES:
            raise BudgetExceededError("Context build or refresh plan exceeds the response budget")
        return response

    def context_apply(
        self,
        *,
        space_id: str,
        mode: str,
        action: str,
        expected_input_sha256: str,
        payload: dict[str, Any] | None,
        confirm_context_write: bool,
    ) -> dict[str, Any]:
        """Apply one confirmed Context change against an exact local plan."""

        if action not in SPACE_CONTEXT_ACTIONS:
            raise SpaceValidationError(
                "unsupported Context maintenance action",
                details={"action": action, "allowed": sorted(SPACE_CONTEXT_ACTIONS)},
            )
        if not isinstance(expected_input_sha256, str) or not SHA256_RE.fullmatch(
            expected_input_sha256
        ):
            raise SpaceValidationError("expected Context plan hash must be a lowercase SHA-256")
        if confirm_context_write is not True:
            raise SpaceValidationError("Context build or refresh requires explicit confirmation")

        plan = self.context_plan(space_id=space_id, mode=mode)
        if plan["input_sha256"] != expected_input_sha256:
            raise SpaceConflictError(
                "Context changed after build or refresh planning",
                details={
                    "space_id": plan["space_id"],
                    "reason": "context_plan_changed",
                    "expected_input_sha256": expected_input_sha256,
                    "current_input_sha256": plan["input_sha256"],
                },
            )
        if not plan["ready"]:
            raise SpaceConflictError(
                "Context build or refresh plan has blockers",
                details={
                    "space_id": plan["space_id"],
                    "reason": "context_plan_blocked",
                    "blockers": plan["blockers"],
                },
            )

        if action == "advance_checkpoint":
            if payload is not None and payload != {}:
                raise SpaceValidationError(
                    "checkpoint advance uses the exact observations from the plan"
                )
            if plan["mode"] == "build":
                raise SpaceConflictError(
                    "an empty Context must contain reviewed items before checkpoint advance",
                    details={"reason": "context_build_empty"},
                )
            if plan["affected_items"]:
                raise SpaceConflictError(
                    "affected Context items must be refreshed before checkpoint advance",
                    details={
                        "reason": "affected_items_not_refreshed",
                        "item_ids": [item["item_id"] for item in plan["affected_items"]],
                    },
                )
            update_payload = plan["checkpoint_payload"]
        else:
            if not isinstance(payload, dict):
                raise SpaceValidationError("Context item apply requires a JSON payload")
            update_payload = payload
            if action == "supersede":
                if plan["mode"] != "refresh":
                    raise SpaceConflictError(
                        "Context build cannot supersede an existing item",
                        details={"reason": "context_build_has_no_items"},
                    )
                raw_items = payload.get("items")
                if not isinstance(raw_items, list):
                    raise SpaceValidationError(
                        "Context supersede payload must contain an items list"
                    )
                affected_ids = {item["item_id"] for item in plan["affected_items"]}
                requested_ids = {
                    item.get("supersedes_item_id") for item in raw_items if isinstance(item, dict)
                }
                if not requested_ids or None in requested_ids or not requested_ids <= affected_ids:
                    raise SpaceValidationError(
                        "Context refresh may supersede only items affected in the exact plan",
                        details={"affected_item_ids": sorted(affected_ids)},
                    )

        result = self.contexts.update(
            action=action,
            context_id=plan["context_id"],
            expected_version=plan["context_version"],
            payload=update_payload,
            confirm_persistent_context_write=True,
            audience="local_cli",
        )
        next_plan = self.context_plan(space_id=plan["space_id"], mode="auto")
        return {
            "space_id": plan["space_id"],
            "mode": plan["mode"],
            "action": action,
            "result": result,
            "next": {
                "input_sha256": next_plan["input_sha256"],
                "mode": next_plan["mode"],
                "context_version": next_plan["context_version"],
                "context_status": next_plan["context_status"],
                "context_status_reason": next_plan["context_status_reason"],
                "affected_item_count": len(next_plan["affected_items"]),
                "inventory_review_required": next_plan["inventory_review_required"],
                "next_action": next_plan["next_action"],
            },
        }

    @staticmethod
    def _stable_uid(prefix: str, *parts: str) -> str:
        payload = "\0".join(("corpus-space-migration-v1", prefix, *parts)).encode()
        return f"{prefix}_{hashlib.sha256(payload).hexdigest()[:32]}"

    def _recovery_counts(self) -> dict[str, dict[str, int]]:
        if not (self.data_root / "workspaces.sqlite3").exists():
            return {}
        with workspace_read_connection(self.data_root) as connection:
            rows = connection.execute(
                """
                SELECT workspace_id, state, COUNT(*) AS count
                FROM workspace_recoveries
                GROUP BY workspace_id, state
                ORDER BY workspace_id, state
                """
            ).fetchall()
        counts: defaultdict[str, dict[str, int]] = defaultdict(dict)
        for row in rows:
            counts[row["workspace_id"]][row["state"]] = int(row["count"])
        return dict(counts)

    def migration_plan(self, *, policy: object = None) -> dict[str, Any]:
        """Build a deterministic, read-only plan for the active Space registry.

        This first migration materializes the canonical registry without
        changing source indexes, context rows, workspace rows, recovery bytes,
        or user files.  Legacy identifier cutover is a later guarded step.
        """

        spaces = self._spaces(audience="local_cli")
        normalized_policy = _normalize_migration_policy(policy)
        context_policy = normalized_policy["context_access_scopes"]
        connection_policy = normalized_policy["connection_access_scopes"]
        used_context_policy_keys: set[str] = set()
        used_connection_policy_keys: set[str] = set()
        policy_requirements: list[dict[str, Any]] = []
        corpora = list_corpora(self.data_root)
        work_folders = self._work_folders()
        archived_contexts = self._contexts(state="archived")
        roots_to_sources: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        for corpus in corpora:
            roots_to_sources[str(_canonical_root(corpus["source_root"]))].append(corpus)
        roots_to_work: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        for work_folder in work_folders:
            roots_to_work[str(_canonical_root(work_folder["root_path"]))].append(work_folder)

        resources: dict[str, dict[str, Any]] = {}
        planned_spaces: list[dict[str, Any]] = []
        blockers: list[dict[str, Any]] = []
        recoveries = self._recovery_counts()
        source_spaces: defaultdict[str, set[str]] = defaultdict(set)
        represented_workspace_ids: set[str] = set()

        for space in spaces:
            space_uid = self._stable_uid("spc", space["space_id"])
            planned_connections: list[dict[str, Any]] = []
            for connection in space["connections"]:
                policy_key = f"{space['space_id']}/{connection['connection_id']}"
                declared_connection_scope = connection_policy.get(policy_key)
                connection_scope = declared_connection_scope or connection["access_scope"]
                if declared_connection_scope is not None:
                    used_connection_policy_keys.add(policy_key)
                else:
                    blockers.append(
                        {
                            "code": "connection_access_scope_not_declared",
                            "space_id": space["space_id"],
                            "connection_id": connection["connection_id"],
                        }
                    )
                policy_requirements.append(
                    {
                        "kind": "connection",
                        "key": policy_key,
                        "suggested_access_scope": connection["access_scope"],
                        "declared_access_scope": declared_connection_scope,
                    }
                )
                location = str(_canonical_root(connection["location"]))
                source_rows = roots_to_sources.get(location, [])
                work_rows = [
                    row
                    for row in roots_to_work.get(location, [])
                    if row["context_id"] == space["space_id"]
                ]
                for source in source_rows:
                    source_spaces[source["corpus_id"]].add(space["space_id"])
                provider_kinds = {str(source["provider_kind"]) for source in source_rows} or {
                    "filesystem"
                }
                provider_kind = sorted(provider_kinds)[0]
                if len(provider_kinds) != 1:
                    blockers.append(
                        {
                            "code": "resource_provider_conflict",
                            "space_id": space["space_id"],
                            "connection_id": connection["connection_id"],
                        }
                    )
                source_scopes = {encode_json(source["source_scope"]) for source in source_rows}
                source_scope = (
                    json.loads(next(iter(source_scopes)))
                    if source_scopes
                    else {
                        "exclude_directory_names": [],
                        "exclude_path_prefixes": [],
                    }
                )
                if len(source_scopes) > 1:
                    blockers.append(
                        {
                            "code": "resource_source_scope_conflict",
                            "space_id": space["space_id"],
                            "connection_id": connection["connection_id"],
                        }
                    )
                root_device: int | None = None
                root_inode: int | None = None
                try:
                    observed_root = os.stat(location, follow_symlinks=False)
                    if not stat.S_ISDIR(observed_root.st_mode):
                        raise NotADirectoryError(location)
                    root_device = int(observed_root.st_dev)
                    root_inode = int(observed_root.st_ino)
                except OSError as exc:
                    blockers.append(
                        {
                            "code": "resource_unavailable",
                            "space_id": space["space_id"],
                            "connection_id": connection["connection_id"],
                            "reason": type(exc).__name__,
                        }
                    )
                resource_uid = self._stable_uid(
                    "res",
                    provider_kind,
                    location,
                )
                resource = resources.setdefault(
                    resource_uid,
                    {
                        "resource_uid": resource_uid,
                        "resource_kind": "filesystem",
                        "provider_kind": provider_kind,
                        "location": location,
                        "location_nfc": unicodedata.normalize("NFC", location),
                        "root_device": root_device,
                        "root_inode": root_inode,
                        "source_scope": source_scope,
                        "source_registry_ids": sorted(
                            source["corpus_id"] for source in source_rows
                        ),
                    },
                )
                resource["source_registry_ids"] = sorted(
                    {
                        *resource["source_registry_ids"],
                        *(source["corpus_id"] for source in source_rows),
                    }
                )
                if (
                    resource["provider_kind"] != provider_kind
                    or resource["location_nfc"] != unicodedata.normalize("NFC", location)
                    or resource["root_device"] != root_device
                    or resource["root_inode"] != root_inode
                    or encode_json(resource["source_scope"]) != encode_json(source_scope)
                ):
                    blockers.append(
                        {
                            "code": "resource_projection_conflict",
                            "space_id": space["space_id"],
                            "connection_id": connection["connection_id"],
                        }
                    )
                connection_uid = self._stable_uid(
                    "con",
                    space_uid,
                    resource_uid,
                )
                recovery_counts: defaultdict[str, int] = defaultdict(int)
                for work_row in work_rows:
                    represented_workspace_ids.add(work_row["workspace_id"])
                    for state, count in recoveries.get(work_row["workspace_id"], {}).items():
                        recovery_counts[state] += count
                current_file = connection["current_file"]
                current_relative_path = (
                    current_file.get("relative_path") if isinstance(current_file, dict) else None
                )
                planned = {
                    "connection_uid": connection_uid,
                    "connection_id": connection["connection_id"],
                    "display_name": connection["display_name"],
                    "resource_uid": resource_uid,
                    "roles": connection["roles"],
                    "access_scope": connection_scope,
                    "permission": connection["permission"],
                    "index_mode": connection["index_mode"],
                    "primary_work": connection["connection_id"]
                    == space["primary_work_connection_id"],
                    "current_file": current_file,
                    "current_relative_path": current_relative_path,
                    "workspace_registry_ids": sorted(row["workspace_id"] for row in work_rows),
                    "recovery_counts": dict(sorted(recovery_counts.items())),
                }
                planned_connections.append(planned)
                if (
                    connection["configuration_state"] != "ready"
                    and declared_connection_scope is None
                ):
                    blockers.append(
                        {
                            "code": "connection_access_scope_conflict",
                            "space_id": space["space_id"],
                            "connection_id": connection["connection_id"],
                        }
                    )
                if "work" in connection["roles"] and connection["connection_state"] not in {
                    "connected",
                    "registered",
                }:
                    blockers.append(
                        {
                            "code": "work_connection_unavailable",
                            "space_id": space["space_id"],
                            "connection_id": connection["connection_id"],
                            "reason": connection["connection_reason"],
                        }
                    )
            context_status = "ready"
            context_status_reason = None
            selected_context_access_scope = "local_only"
            if space["context"] is not None:
                declared_context_scope = context_policy.get(space["space_id"])
                selected_context_access_scope = (
                    declared_context_scope or space["context"]["access_scope"]
                )
                if declared_context_scope is not None:
                    used_context_policy_keys.add(space["space_id"])
                else:
                    blockers.append(
                        {
                            "code": "context_access_scope_not_declared",
                            "space_id": space["space_id"],
                        }
                    )
                policy_requirements.append(
                    {
                        "kind": "context",
                        "key": space["space_id"],
                        "suggested_access_scope": space["context"]["access_scope"],
                        "declared_access_scope": declared_context_scope,
                    }
                )
                context_detail = self.contexts.read(
                    context_id=space["space_id"],
                    state="active",
                    include_history=False,
                    limit=1,
                    offset=0,
                    audience="local_cli",
                    view="restricted",
                )
                context_status, context_status_reason = self._context_status(context_detail)
            planned_spaces.append(
                {
                    "space_uid": space_uid,
                    "space_id": space["space_id"],
                    "display_name": space["display_name"],
                    "state": space["state"],
                    "context_id": space["space_id"] if space["context"] else None,
                    "context_access_scope": selected_context_access_scope,
                    "context_status": context_status,
                    "context_status_reason": context_status_reason,
                    "connections": planned_connections,
                }
            )

        unused_context_policy_keys = sorted(set(context_policy) - used_context_policy_keys)
        unused_connection_policy_keys = sorted(set(connection_policy) - used_connection_policy_keys)
        if unused_context_policy_keys or unused_connection_policy_keys:
            raise SpaceValidationError(
                "migration policy contains keys that are not in the current projection",
                details={
                    "unused_context_keys": unused_context_policy_keys,
                    "unused_connection_keys": unused_connection_policy_keys,
                },
            )

        target_space_ids = {space["space_id"] for space in planned_spaces}
        legacy_source_ids_for_cutover = [
            {
                "source_registry_id": corpus["corpus_id"],
                "represented_in_spaces": sorted(source_spaces[corpus["corpus_id"]]),
            }
            for corpus in corpora
            if corpus["corpus_id"] not in target_space_ids
        ]
        deferred_archived_contexts = [
            {
                "context_id": context["context_id"],
                "title": context["title"],
                "corpus_ids": context["corpus_ids"],
            }
            for context in archived_contexts
        ]
        deferred_workspaces = [
            {
                "workspace_registry_id": work_folder["workspace_id"],
                "context_id": work_folder["context_id"],
                "context_state": work_folder["context_state"],
            }
            for work_folder in work_folders
            if work_folder["workspace_id"] not in represented_workspace_ids
        ]
        canonical = {
            "plan_version": 1,
            "scope": "active_space_registry_only",
            "resources": sorted(resources.values(), key=lambda item: item["resource_uid"]),
            "spaces": planned_spaces,
            "legacy_source_ids_for_cutover": legacy_source_ids_for_cutover,
            "deferred_archived_contexts": deferred_archived_contexts,
            "deferred_workspaces": deferred_workspaces,
            "policy": normalized_policy,
            "policy_requirements": sorted(
                policy_requirements,
                key=lambda item: (item["kind"], item["key"]),
            ),
            "blockers": blockers,
        }
        input_sha256 = hashlib.sha256(encode_json(canonical).encode()).hexdigest()
        return {
            "migration": "space-registry-v1",
            "input_sha256": input_sha256,
            "ready": not blockers,
            **canonical,
            "changes_files": False,
            "changes_registry": False,
            "legacy_identifier_cutover": False,
            "next_action": "apply with the exact input hash and explicit confirmation",
        }

    def _complete_registry_plan(self) -> tuple[dict[str, Any], dict[str, Any]]:
        if not (self.data_root / "spaces.sqlite3").exists():
            raise SpaceConflictError(
                "canonical Space registry must be materialized before identifier cutover",
                details={"reason": "space_registry_missing"},
            )
        with space_read_connection(self.data_root) as connection:
            receipt = connection.execute(
                """
                SELECT * FROM space_migration_receipts
                WHERE state = 'complete'
                ORDER BY created_at, migration_id
                LIMIT 1
                """
            ).fetchone()
            if receipt is None:
                raise SpaceConflictError(
                    "canonical Space registry must be complete before identifier cutover",
                    details={"reason": "space_registry_incomplete"},
                )
            try:
                plan = json.loads(receipt["plan_json"])
            except (TypeError, ValueError) as exc:
                raise SpaceConflictError(
                    "canonical Space registry receipt is invalid",
                    details={"reason": "invalid_migration_receipt"},
                ) from exc
            if not isinstance(plan, dict) or not self._registry_matches_plan(connection, plan):
                raise SpaceConflictError(
                    "canonical Space registry changed after migration",
                    details={
                        "reason": "space_registry_drift",
                        "migration_id": receipt["migration_id"],
                    },
                )
        return dict(receipt), plan

    def _workspace_identifier_rewrites(
        self,
        source_uids: dict[str, str],
    ) -> list[dict[str, Any]]:
        rewrites: list[dict[str, Any]] = []
        for work_folder in self._work_folders():
            workspace_id = work_folder["workspace_id"]
            paths = WorkspaceRuntimePaths(self.data_root, workspace_id)
            entries = self.workspaces._read_index_change_journal(paths)
            matching_paths = sorted(
                relative_path
                for relative_path, entry in entries.items()
                if entry.get("source_corpus_id") in source_uids
            )
            if not matching_paths:
                continue
            remapped = {
                relative_path: {
                    **entry,
                    "source_corpus_id": source_uids.get(
                        entry.get("source_corpus_id"),
                        entry.get("source_corpus_id"),
                    ),
                }
                for relative_path, entry in entries.items()
            }
            rewrites.append(
                {
                    "workspace_id": workspace_id,
                    "matching_paths": matching_paths,
                    "entry_count": len(entries),
                    "before_sha256": hashlib.sha256(encode_json(entries).encode()).hexdigest(),
                    "after_sha256": hashlib.sha256(encode_json(remapped).encode()).hexdigest(),
                }
            )
        return rewrites

    def _golden_identifier_rewrites(
        self,
        source_uids: dict[str, str],
        *,
        blockers: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        rewrites: list[dict[str, Any]] = []
        root = self.data_root / "goldens"
        if not root.exists():
            return rewrites
        for source_registry_id, source_uid in sorted(source_uids.items()):
            source_directory = root / source_registry_id
            if not source_directory.exists():
                continue
            if source_directory.is_symlink() or not source_directory.is_dir():
                blockers.append(
                    {
                        "code": "golden_directory_unsafe",
                        "source_registry_id": source_registry_id,
                    }
                )
                continue
            destination = root / source_uid
            if destination.exists():
                blockers.append(
                    {
                        "code": "golden_destination_exists",
                        "source_registry_id": source_registry_id,
                        "source_uid": source_uid,
                    }
                )
                continue
            files: list[dict[str, Any]] = []
            for path in sorted(source_directory.rglob("*")):
                relative_path = path.relative_to(source_directory).as_posix()
                if path.is_symlink():
                    blockers.append(
                        {
                            "code": "golden_entry_unsupported",
                            "source_registry_id": source_registry_id,
                            "relative_path": relative_path,
                        }
                    )
                    continue
                if path.is_dir():
                    continue
                if not path.is_file() or path.suffix != ".json":
                    blockers.append(
                        {
                            "code": "golden_entry_unsupported",
                            "source_registry_id": source_registry_id,
                            "relative_path": relative_path,
                        }
                    )
                    continue
                try:
                    encoded = path.read_bytes()
                    value = json.loads(encoded.decode("utf-8"))
                except (OSError, UnicodeError, ValueError) as exc:
                    blockers.append(
                        {
                            "code": "golden_entry_invalid",
                            "source_registry_id": source_registry_id,
                            "relative_path": relative_path,
                            "reason": type(exc).__name__,
                        }
                    )
                    continue
                subject = value.get("subject") if isinstance(value, dict) else None
                if not isinstance(subject, dict) or subject.get("corpus_id") != source_registry_id:
                    blockers.append(
                        {
                            "code": "golden_subject_mismatch",
                            "source_registry_id": source_registry_id,
                            "relative_path": relative_path,
                        }
                    )
                    continue
                remapped = {**value, "subject": {**subject, "corpus_id": source_uid}}
                files.append(
                    {
                        "relative_path": relative_path,
                        "before_sha256": hashlib.sha256(encoded).hexdigest(),
                        "after_sha256": hashlib.sha256(
                            (json.dumps(remapped, ensure_ascii=False, indent=2) + "\n").encode()
                        ).hexdigest(),
                    }
                )
            rewrites.append(
                {
                    "source_registry_id": source_registry_id,
                    "source_uid": source_uid,
                    "files": files,
                }
            )
        return rewrites

    def identifier_cutover_plan(self, *, policy: object = None) -> dict[str, Any]:
        """Plan removal of human source-registry identifiers without aliases."""

        receipt, registry_plan = self._complete_registry_plan()
        normalized_policy = _normalize_identifier_policy(policy)
        context_replacements = normalized_policy["context_id_replacements"]
        blockers: list[dict[str, Any]] = []
        requirements: list[dict[str, Any]] = []
        corpora = list_corpora(self.data_root)
        resource_by_source_id: dict[str, dict[str, Any]] = {}
        for resource in registry_plan.get("resources", []):
            for source_registry_id in resource.get("source_registry_ids", []):
                if source_registry_id in resource_by_source_id:
                    blockers.append(
                        {
                            "code": "source_registry_binding_ambiguous",
                            "source_registry_id": source_registry_id,
                        }
                    )
                resource_by_source_id[source_registry_id] = resource

        mappings: list[dict[str, Any]] = []
        source_uids: dict[str, str] = {}
        for corpus in corpora:
            source_registry_id = corpus["corpus_id"]
            if SOURCE_UID_RE.fullmatch(source_registry_id):
                blockers.append(
                    {
                        "code": "source_identifier_already_cut_over",
                        "source_uid": source_registry_id,
                    }
                )
                continue
            resource = resource_by_source_id.get(source_registry_id)
            if resource is None:
                blockers.append(
                    {
                        "code": "source_registry_binding_missing",
                        "source_registry_id": source_registry_id,
                    }
                )
                continue
            source_uid = self._stable_uid(
                "src",
                resource["resource_uid"],
                source_registry_id,
            )
            source_uids[source_registry_id] = source_uid
            source_runtime = self.data_root / "corpora" / source_registry_id
            destination_runtime = self.data_root / "corpora" / source_uid
            root_device: int | None = None
            root_inode: int | None = None
            try:
                observed = os.stat(source_runtime, follow_symlinks=False)
                if not stat.S_ISDIR(observed.st_mode):
                    raise NotADirectoryError(source_runtime)
                root_device = int(observed.st_dev)
                root_inode = int(observed.st_ino)
            except OSError as exc:
                blockers.append(
                    {
                        "code": "source_runtime_missing",
                        "source_registry_id": source_registry_id,
                        "reason": type(exc).__name__,
                    }
                )
            if destination_runtime.exists():
                blockers.append(
                    {
                        "code": "source_runtime_destination_exists",
                        "source_registry_id": source_registry_id,
                        "source_uid": source_uid,
                    }
                )
            mappings.append(
                {
                    "source_registry_id": source_registry_id,
                    "source_uid": source_uid,
                    "resource_uid": resource["resource_uid"],
                    "source_root": corpus["source_root"],
                    "runtime_device": root_device,
                    "runtime_inode": root_inode,
                }
            )

        context_reference_counts: dict[str, dict[str, int]] = {}
        if (self.data_root / "contexts.sqlite3").exists():
            with context_read_connection(self.data_root) as connection:
                for source_registry_id in sorted(source_uids):
                    context_reference_counts[source_registry_id] = {
                        table: int(
                            connection.execute(
                                f"SELECT COUNT(*) FROM {table} WHERE corpus_id = ?",
                                (source_registry_id,),
                            ).fetchone()[0]
                        )
                        for table in (
                            "context_corpora",
                            "context_sources",
                            "corpus_source_bindings",
                            "context_external_sources",
                        )
                    }
        else:
            for source_registry_id in sorted(source_uids):
                context_reference_counts[source_registry_id] = {
                    table: 0
                    for table in (
                        "context_corpora",
                        "context_sources",
                        "corpus_source_bindings",
                        "context_external_sources",
                    )
                }

        all_contexts = [
            *self._contexts(state="active"),
            *self._contexts(state="archived"),
        ]
        context_ids = {context["context_id"] for context in all_contexts}
        active_space_ids = {space["space_id"] for space in registry_plan.get("spaces", [])}
        legacy_source_ids = {
            item["source_registry_id"]
            for item in registry_plan.get("legacy_source_ids_for_cutover", [])
        }
        required_context_ids = sorted(context_ids & legacy_source_ids)
        replacement_rows: list[dict[str, Any]] = []
        for old_id in required_context_ids:
            replacement = context_replacements.get(old_id)
            represented_spaces = sorted(
                {
                    represented
                    for item in registry_plan.get("legacy_source_ids_for_cutover", [])
                    if item.get("source_registry_id") == old_id
                    for represented in item.get("represented_in_spaces", [])
                }
            )
            suggested = (
                f"{represented_spaces[0]}-archive"
                if len(represented_spaces) == 1
                else f"{old_id}-archive"
            )
            requirements.append(
                {
                    "kind": "archived_context_id",
                    "key": old_id,
                    "suggested_replacement": suggested,
                    "declared_replacement": replacement,
                }
            )
            if replacement is None:
                blockers.append(
                    {
                        "code": "legacy_context_id_not_replaced",
                        "context_id": old_id,
                    }
                )
                continue
            if replacement in context_ids or replacement in active_space_ids:
                blockers.append(
                    {
                        "code": "context_id_replacement_conflict",
                        "context_id": old_id,
                        "replacement": replacement,
                    }
                )
                continue
            replacement_rows.append({"from": old_id, "to": replacement})

        unused_context_replacements = sorted(set(context_replacements) - set(required_context_ids))
        if unused_context_replacements:
            raise SpaceValidationError(
                "identifier cutover policy contains unused Context ids",
                details={"unused_context_ids": unused_context_replacements},
            )

        deferred_workspace_conflicts = [
            {
                "workspace_id": work_folder["workspace_id"],
                "context_id": work_folder["context_id"],
            }
            for work_folder in self._work_folders()
            if work_folder["workspace_id"] in required_context_ids
            or work_folder["context_id"] in required_context_ids
        ]
        if deferred_workspace_conflicts:
            blockers.append(
                {
                    "code": "legacy_workspace_identifier_requires_separate_migration",
                    "workspaces": deferred_workspace_conflicts,
                }
            )

        unsupported_state: list[dict[str, Any]] = []
        for source_registry_id in sorted(source_uids):
            candidates = (
                self.data_root / "source-sync" / source_registry_id,
                self.data_root / "source-sync-epochs" / f"{source_registry_id}.json",
                self.data_root / "remote-deletions" / f"{source_registry_id}.json",
            )
            for candidate in candidates:
                if candidate.exists():
                    unsupported_state.append(
                        {
                            "source_registry_id": source_registry_id,
                            "state_kind": candidate.parent.name,
                        }
                    )
        if unsupported_state:
            blockers.append(
                {
                    "code": "remote_source_state_requires_separate_migration",
                    "entries": unsupported_state,
                }
            )

        resource_locators: dict[str, dict[str, Any]] = {
            resource["resource_uid"]: {} for resource in registry_plan.get("resources", [])
        }
        for mapping in mappings:
            resource_locators[mapping["resource_uid"]]["source_uid"] = mapping["source_uid"]
        for space in registry_plan.get("spaces", []):
            for connection in space.get("connections", []):
                workspace_ids = connection.get("workspace_registry_ids", [])
                if not workspace_ids:
                    continue
                if len(workspace_ids) != 1:
                    blockers.append(
                        {
                            "code": "workspace_registry_binding_ambiguous",
                            "space_id": space["space_id"],
                            "connection_id": connection["connection_id"],
                        }
                    )
                    continue
                locator = resource_locators[connection["resource_uid"]]
                existing_workspace_id = locator.get("workspace_id")
                if existing_workspace_id not in {None, workspace_ids[0]}:
                    blockers.append(
                        {
                            "code": "workspace_resource_binding_conflict",
                            "resource_uid": connection["resource_uid"],
                        }
                    )
                    continue
                locator["workspace_id"] = workspace_ids[0]

        workspace_rewrites = self._workspace_identifier_rewrites(source_uids)
        golden_rewrites = self._golden_identifier_rewrites(
            source_uids,
            blockers=blockers,
        )
        canonical = {
            "plan_version": 1,
            "scope": "source-identifiers-and-dependent-private-state",
            "space_registry_migration_id": receipt["migration_id"],
            "space_registry_input_sha256": receipt["input_sha256"],
            "source_mappings": sorted(
                mappings,
                key=lambda item: item["source_registry_id"],
            ),
            "context_reference_counts": context_reference_counts,
            "context_id_replacements": replacement_rows,
            "resource_locators": [
                {
                    "resource_uid": resource_uid,
                    "locator": locator,
                }
                for resource_uid, locator in sorted(resource_locators.items())
            ],
            "workspace_journal_rewrites": workspace_rewrites,
            "golden_rewrites": golden_rewrites,
            "policy": normalized_policy,
            "policy_requirements": requirements,
            "unsupported_remote_state": unsupported_state,
            "blockers": blockers,
        }
        input_sha256 = hashlib.sha256(encode_json(canonical).encode()).hexdigest()
        return {
            "migration": "source-identifier-cutover-v1",
            "input_sha256": input_sha256,
            "ready": not blockers,
            **canonical,
            "changes_private_registry": True,
            "changes_private_runtime_names": True,
            "changes_user_files": False,
            "keeps_legacy_aliases": False,
            "next_action": "apply with the exact input hash and explicit confirmation",
        }

    @staticmethod
    def _ensure_identifier_cutover_schema(connection: Any) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS source_identifier_cutovers (
                cutover_id TEXT PRIMARY KEY,
                input_sha256 TEXT NOT NULL,
                state TEXT NOT NULL
                    CHECK (state IN ('prepared', 'applying', 'complete', 'rolled_back')),
                plan_json TEXT NOT NULL,
                rollback_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_source_identifier_cutover_active
                ON source_identifier_cutovers((1))
                WHERE state IN ('prepared', 'applying', 'complete')
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_source_identifier_cutover_input
                ON source_identifier_cutovers(input_sha256, created_at, cutover_id)
            """
        )

    @classmethod
    def _active_identifier_cutover(cls, connection: Any) -> Any:
        cls._ensure_identifier_cutover_schema(connection)
        return connection.execute(
            """
            SELECT * FROM source_identifier_cutovers
            WHERE state IN ('prepared', 'applying', 'complete')
            ORDER BY created_at, cutover_id
            LIMIT 1
            """
        ).fetchone()

    def _cutover_rollback_snapshot(self, plan: dict[str, Any]) -> dict[str, Any]:
        workspace_journals: dict[str, Any] = {}
        for rewrite in plan["workspace_journal_rewrites"]:
            workspace_id = rewrite["workspace_id"]
            entries = self.workspaces._read_index_change_journal(
                WorkspaceRuntimePaths(self.data_root, workspace_id)
            )
            digest = hashlib.sha256(encode_json(entries).encode()).hexdigest()
            if digest != rewrite["before_sha256"]:
                raise SpaceConflictError(
                    "work-folder source journal changed after cutover planning",
                    details={
                        "reason": "workspace_journal_changed",
                        "workspace_id": workspace_id,
                    },
                )
            workspace_journals[workspace_id] = entries

        golden_files: dict[str, dict[str, dict[str, Any]]] = {}
        for rewrite in plan["golden_rewrites"]:
            source_registry_id = rewrite["source_registry_id"]
            source_directory = self.data_root / "goldens" / source_registry_id
            files: dict[str, dict[str, Any]] = {}
            for item in rewrite["files"]:
                path = source_directory / item["relative_path"]
                encoded = path.read_bytes()
                if hashlib.sha256(encoded).hexdigest() != item["before_sha256"]:
                    raise SpaceConflictError(
                        "golden annotation changed after cutover planning",
                        details={
                            "reason": "golden_changed",
                            "source_registry_id": source_registry_id,
                            "relative_path": item["relative_path"],
                        },
                    )
                files[item["relative_path"]] = {
                    "data_base64": base64.b64encode(encoded).decode("ascii"),
                    "mode": stat.S_IMODE(path.stat(follow_symlinks=False).st_mode),
                }
            golden_files[source_registry_id] = files
        return {
            "workspace_journals": workspace_journals,
            "golden_files": golden_files,
            "database_backups": {},
        }

    def _backup_cutover_databases(self, cutover_id: str) -> dict[str, str]:
        backups: dict[str, str] = {}
        with private_directory(self.data_root) as data_descriptor:
            for database_name in (
                "catalog.sqlite",
                "contexts.sqlite3",
                "workspaces.sqlite3",
                "spaces.sqlite3",
            ):
                path = self.data_root / database_name
                if not path.exists():
                    continue
                backup_name = f"source-identifiers-{cutover_id}-{database_name}"
                with closing(connect(path)) as connection:
                    backup = backup_database_to_private_subdirectory(
                        connection,
                        parent_descriptor=data_descriptor,
                        backup_directory_name="migration-backups",
                        backup_directory=self.data_root / "migration-backups",
                        backup_name=backup_name,
                    )
                backups[database_name] = str(backup.relative_to(self.data_root))
        return backups

    @staticmethod
    def _atomic_private_bytes(path: Path, data: bytes, *, mode: int) -> None:
        temporary_name = f".{path.name}.{uuid.uuid4().hex}.tmp"
        descriptor: int | None = None
        with private_directory(path.parent) as parent_descriptor:
            try:
                descriptor = os.open(
                    temporary_name,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    mode,
                    dir_fd=parent_descriptor,
                )
                view = memoryview(data)
                while view:
                    try:
                        written = os.write(descriptor, view)
                    except InterruptedError:
                        continue
                    if written <= 0:
                        raise OSError("private cutover write made no progress")
                    view = view[written:]
                os.fchmod(descriptor, mode)
                os.fsync(descriptor)
                os.close(descriptor)
                descriptor = None
                os.replace(
                    temporary_name,
                    path.name,
                    src_dir_fd=parent_descriptor,
                    dst_dir_fd=parent_descriptor,
                )
                os.fsync(parent_descriptor)
            finally:
                if descriptor is not None:
                    os.close(descriptor)
                with suppress(FileNotFoundError):
                    os.unlink(temporary_name, dir_fd=parent_descriptor)

    def _apply_workspace_journal_rewrites(self, plan: dict[str, Any]) -> None:
        source_uids = {
            item["source_registry_id"]: item["source_uid"] for item in plan["source_mappings"]
        }
        for rewrite in plan["workspace_journal_rewrites"]:
            workspace_id = rewrite["workspace_id"]
            paths = WorkspaceRuntimePaths(self.data_root, workspace_id)
            entries = self.workspaces._read_index_change_journal(paths)
            current_sha256 = hashlib.sha256(encode_json(entries).encode()).hexdigest()
            if current_sha256 == rewrite["after_sha256"]:
                continue
            if current_sha256 != rewrite["before_sha256"]:
                raise SpaceConflictError(
                    "work-folder source journal changed during identifier cutover",
                    details={
                        "reason": "workspace_journal_changed",
                        "workspace_id": workspace_id,
                    },
                )
            remapped = {
                relative_path: {
                    **entry,
                    "source_corpus_id": source_uids.get(
                        entry.get("source_corpus_id"),
                        entry.get("source_corpus_id"),
                    ),
                }
                for relative_path, entry in entries.items()
            }
            self.workspaces._write_index_change_journal(paths, remapped)
            observed = self.workspaces._read_index_change_journal(paths)
            if (
                hashlib.sha256(encode_json(observed).encode()).hexdigest()
                != rewrite["after_sha256"]
            ):
                raise SpaceConflictError(
                    "work-folder source journal did not verify after identifier cutover",
                    details={
                        "reason": "workspace_journal_verification_failed",
                        "workspace_id": workspace_id,
                    },
                )

    def _apply_golden_rewrites(self, plan: dict[str, Any]) -> None:
        for rewrite in plan["golden_rewrites"]:
            old_id = rewrite["source_registry_id"]
            source_uid = rewrite["source_uid"]
            old_directory = self.data_root / "goldens" / old_id
            new_directory = self.data_root / "goldens" / source_uid
            if old_directory.exists() and new_directory.exists():
                raise SpaceConflictError(
                    "both old and new golden directories exist",
                    details={"reason": "golden_directory_conflict", "source_uid": source_uid},
                )
            directory = new_directory if new_directory.exists() else old_directory
            if not directory.is_dir() or directory.is_symlink():
                raise SpaceConflictError(
                    "golden directory is unavailable during identifier cutover",
                    details={"reason": "golden_directory_missing", "source_uid": source_uid},
                )
            for item in rewrite["files"]:
                path = directory / item["relative_path"]
                encoded = path.read_bytes()
                current_sha256 = hashlib.sha256(encoded).hexdigest()
                if current_sha256 == item["after_sha256"]:
                    continue
                if current_sha256 != item["before_sha256"]:
                    raise SpaceConflictError(
                        "golden annotation changed during identifier cutover",
                        details={
                            "reason": "golden_changed",
                            "source_uid": source_uid,
                            "relative_path": item["relative_path"],
                        },
                    )
                value = json.loads(encoded.decode("utf-8"))
                value["subject"] = {**value["subject"], "corpus_id": source_uid}
                remapped = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()
                if hashlib.sha256(remapped).hexdigest() != item["after_sha256"]:
                    raise SpaceConflictError(
                        "golden annotation rewrite does not match its cutover plan",
                        details={"reason": "golden_plan_mismatch"},
                    )
                mode = stat.S_IMODE(path.stat(follow_symlinks=False).st_mode)
                self._atomic_private_bytes(path, remapped, mode=mode)
            if directory == old_directory:
                with private_directory(self.data_root / "goldens") as parent_descriptor:
                    os.rename(
                        old_id,
                        source_uid,
                        src_dir_fd=parent_descriptor,
                        dst_dir_fd=parent_descriptor,
                    )
                    os.fsync(parent_descriptor)

    def _apply_corpus_runtime_renames(self, plan: dict[str, Any]) -> None:
        with private_directory(self.data_root / "corpora") as parent_descriptor:
            for mapping in plan["source_mappings"]:
                old_id = mapping["source_registry_id"]
                source_uid = mapping["source_uid"]
                old_exists = True
                new_exists = True
                try:
                    old_stat = os.stat(old_id, dir_fd=parent_descriptor, follow_symlinks=False)
                except FileNotFoundError:
                    old_exists = False
                    old_stat = None
                try:
                    new_stat = os.stat(source_uid, dir_fd=parent_descriptor, follow_symlinks=False)
                except FileNotFoundError:
                    new_exists = False
                    new_stat = None
                if old_exists == new_exists:
                    raise SpaceConflictError(
                        "source runtime identifier state is ambiguous",
                        details={
                            "reason": "source_runtime_state_conflict",
                            "source_uid": source_uid,
                        },
                    )
                observed = new_stat if new_exists else old_stat
                if (
                    observed is None
                    or not stat.S_ISDIR(observed.st_mode)
                    or int(observed.st_dev) != mapping["runtime_device"]
                    or int(observed.st_ino) != mapping["runtime_inode"]
                ):
                    raise SpaceConflictError(
                        "source runtime changed after identifier cutover planning",
                        details={
                            "reason": "source_runtime_changed",
                            "source_uid": source_uid,
                        },
                    )
                if old_exists:
                    os.rename(
                        old_id,
                        source_uid,
                        src_dir_fd=parent_descriptor,
                        dst_dir_fd=parent_descriptor,
                    )
            os.fsync(parent_descriptor)

    def _apply_identifier_database_transaction(
        self,
        plan: dict[str, Any],
        *,
        cutover_id: str,
    ) -> None:
        catalog_path = self.data_root / "catalog.sqlite"
        with closing(connect(catalog_path)) as connection:
            attached_schemas: list[str] = []
            if (self.data_root / "contexts.sqlite3").exists():
                connection.execute(
                    "ATTACH DATABASE ? AS contexts_db",
                    (str(self.data_root / "contexts.sqlite3"),),
                )
                attached_schemas.append("contexts_db")
            if (self.data_root / "workspaces.sqlite3").exists():
                connection.execute(
                    "ATTACH DATABASE ? AS workspaces_db",
                    (str(self.data_root / "workspaces.sqlite3"),),
                )
                attached_schemas.append("workspaces_db")
            connection.execute(
                "ATTACH DATABASE ? AS spaces_db", (str(self.data_root / "spaces.sqlite3"),)
            )
            attached_schemas.append("spaces_db")
            try:
                connection.execute("BEGIN IMMEDIATE")
                for mapping in plan["source_mappings"]:
                    updated = connection.execute(
                        "UPDATE corpora SET corpus_id = ? WHERE corpus_id = ?",
                        (mapping["source_uid"], mapping["source_registry_id"]),
                    ).rowcount
                    if updated != 1:
                        raise SpaceConflictError(
                            "source registry changed during identifier cutover",
                            details={
                                "reason": "source_registry_changed",
                                "source_registry_id": mapping["source_registry_id"],
                            },
                        )
                    if "contexts_db" in attached_schemas:
                        for table in (
                            "context_corpora",
                            "context_sources",
                            "corpus_source_bindings",
                            "context_external_sources",
                        ):
                            connection.execute(
                                f"UPDATE contexts_db.{table} SET corpus_id = ? WHERE corpus_id = ?",
                                (mapping["source_uid"], mapping["source_registry_id"]),
                            )

                for replacement in plan["context_id_replacements"]:
                    old_id = replacement["from"]
                    new_id = replacement["to"]
                    inserted = connection.execute(
                        """
                        INSERT INTO contexts_db.contexts(
                            context_id, title, purpose, scope_json, state,
                            version, created_at, updated_at
                        )
                        SELECT ?, title, purpose, scope_json, state,
                               version, created_at, updated_at
                        FROM contexts_db.contexts
                        WHERE context_id = ?
                        """,
                        (new_id, old_id),
                    ).rowcount
                    if inserted != 1:
                        raise SpaceConflictError(
                            "archived Context changed during identifier cutover",
                            details={"reason": "context_registry_changed", "context_id": old_id},
                        )
                    for table in (
                        "context_corpora",
                        "context_items",
                        "context_release_manifests",
                    ):
                        connection.execute(
                            f"UPDATE contexts_db.{table} SET context_id = ? WHERE context_id = ?",
                            (new_id, old_id),
                        )
                    if "workspaces_db" in attached_schemas:
                        connection.execute(
                            """
                            UPDATE workspaces_db.workspaces
                            SET context_id = ? WHERE context_id = ?
                            """,
                            (new_id, old_id),
                        )
                    deleted = connection.execute(
                        "DELETE FROM contexts_db.contexts WHERE context_id = ?",
                        (old_id,),
                    ).rowcount
                    if deleted != 1:
                        raise SpaceConflictError(
                            "archived Context could not be replaced during identifier cutover",
                            details={"reason": "context_registry_changed", "context_id": old_id},
                        )

                for resource in plan["resource_locators"]:
                    updated = connection.execute(
                        """
                        UPDATE spaces_db.resources
                        SET locator_json = ?, updated_at = ?
                        WHERE resource_uid = ? AND locator_json = '{}'
                        """,
                        (
                            encode_json(resource["locator"]),
                            utc_now(),
                            resource["resource_uid"],
                        ),
                    ).rowcount
                    if updated != 1:
                        raise SpaceConflictError(
                            "canonical resource binding changed during identifier cutover",
                            details={
                                "reason": "space_registry_drift",
                                "resource_uid": resource["resource_uid"],
                            },
                        )

                updated_receipt = connection.execute(
                    """
                    UPDATE spaces_db.source_identifier_cutovers
                    SET state = 'complete', updated_at = ?
                    WHERE cutover_id = ? AND state = 'applying'
                    """,
                    (utc_now(), cutover_id),
                ).rowcount
                if updated_receipt != 1:
                    raise SpaceConflictError(
                        "identifier cutover receipt changed before commit",
                        details={"reason": "cutover_receipt_changed"},
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                for schema_name in reversed(attached_schemas):
                    with suppress(sqlite3.DatabaseError):
                        connection.execute(f"DETACH DATABASE {schema_name}")

    def _rollback_identifier_database_transaction(
        self,
        plan: dict[str, Any],
        *,
        cutover_id: str,
    ) -> None:
        catalog_path = self.data_root / "catalog.sqlite"
        with closing(connect(catalog_path)) as connection:
            attached_schemas: list[str] = []
            if (self.data_root / "contexts.sqlite3").exists():
                connection.execute(
                    "ATTACH DATABASE ? AS contexts_db",
                    (str(self.data_root / "contexts.sqlite3"),),
                )
                attached_schemas.append("contexts_db")
            if (self.data_root / "workspaces.sqlite3").exists():
                connection.execute(
                    "ATTACH DATABASE ? AS workspaces_db",
                    (str(self.data_root / "workspaces.sqlite3"),),
                )
                attached_schemas.append("workspaces_db")
            connection.execute(
                "ATTACH DATABASE ? AS spaces_db",
                (str(self.data_root / "spaces.sqlite3"),),
            )
            attached_schemas.append("spaces_db")
            try:
                connection.execute("BEGIN IMMEDIATE")
                for mapping in plan["source_mappings"]:
                    updated = connection.execute(
                        "UPDATE corpora SET corpus_id = ? WHERE corpus_id = ?",
                        (mapping["source_registry_id"], mapping["source_uid"]),
                    ).rowcount
                    if updated != 1:
                        raise SpaceConflictError(
                            "source registry changed before identifier rollback",
                            details={
                                "reason": "source_registry_changed",
                                "source_uid": mapping["source_uid"],
                            },
                        )
                    if "contexts_db" in attached_schemas:
                        for table in (
                            "context_corpora",
                            "context_sources",
                            "corpus_source_bindings",
                            "context_external_sources",
                        ):
                            connection.execute(
                                f"UPDATE contexts_db.{table} SET corpus_id = ? WHERE corpus_id = ?",
                                (mapping["source_registry_id"], mapping["source_uid"]),
                            )

                for replacement in reversed(plan["context_id_replacements"]):
                    old_id = replacement["from"]
                    new_id = replacement["to"]
                    inserted = connection.execute(
                        """
                        INSERT INTO contexts_db.contexts(
                            context_id, title, purpose, scope_json, state,
                            version, created_at, updated_at
                        )
                        SELECT ?, title, purpose, scope_json, state,
                               version, created_at, updated_at
                        FROM contexts_db.contexts
                        WHERE context_id = ?
                        """,
                        (old_id, new_id),
                    ).rowcount
                    if inserted != 1:
                        raise SpaceConflictError(
                            "archived Context changed before identifier rollback",
                            details={"reason": "context_registry_changed", "context_id": new_id},
                        )
                    for table in (
                        "context_corpora",
                        "context_items",
                        "context_release_manifests",
                    ):
                        connection.execute(
                            f"UPDATE contexts_db.{table} SET context_id = ? WHERE context_id = ?",
                            (old_id, new_id),
                        )
                    if "workspaces_db" in attached_schemas:
                        connection.execute(
                            """
                            UPDATE workspaces_db.workspaces
                            SET context_id = ? WHERE context_id = ?
                            """,
                            (old_id, new_id),
                        )
                    deleted = connection.execute(
                        "DELETE FROM contexts_db.contexts WHERE context_id = ?",
                        (new_id,),
                    ).rowcount
                    if deleted != 1:
                        raise SpaceConflictError(
                            "archived Context could not be restored during identifier rollback",
                            details={"reason": "context_registry_changed", "context_id": new_id},
                        )

                for resource in plan["resource_locators"]:
                    updated = connection.execute(
                        """
                        UPDATE spaces_db.resources
                        SET locator_json = '{}', updated_at = ?
                        WHERE resource_uid = ? AND locator_json = ?
                        """,
                        (
                            utc_now(),
                            resource["resource_uid"],
                            encode_json(resource["locator"]),
                        ),
                    ).rowcount
                    if updated != 1:
                        raise SpaceConflictError(
                            "canonical resource binding changed before identifier rollback",
                            details={
                                "reason": "space_registry_drift",
                                "resource_uid": resource["resource_uid"],
                            },
                        )

                updated_receipt = connection.execute(
                    """
                    UPDATE spaces_db.source_identifier_cutovers
                    SET state = 'rolled_back', updated_at = ?
                    WHERE cutover_id = ? AND state = 'complete'
                    """,
                    (utc_now(), cutover_id),
                ).rowcount
                if updated_receipt != 1:
                    raise SpaceConflictError(
                        "identifier cutover receipt changed before rollback commit",
                        details={"reason": "cutover_receipt_changed"},
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                for schema_name in reversed(attached_schemas):
                    with suppress(sqlite3.DatabaseError):
                        connection.execute(f"DETACH DATABASE {schema_name}")

    def _verify_identifier_cutover(self, plan: dict[str, Any]) -> None:
        catalog_ids = {item["corpus_id"] for item in list_corpora(self.data_root)}
        with private_directory(self.data_root / "corpora") as parent_descriptor:
            for mapping in plan["source_mappings"]:
                if (
                    mapping["source_uid"] not in catalog_ids
                    or mapping["source_registry_id"] in catalog_ids
                ):
                    raise SpaceConflictError(
                        "source registry does not match the completed identifier cutover",
                        details={"reason": "source_registry_drift"},
                    )
                try:
                    observed = os.stat(
                        mapping["source_uid"],
                        dir_fd=parent_descriptor,
                        follow_symlinks=False,
                    )
                    os.stat(
                        mapping["source_registry_id"],
                        dir_fd=parent_descriptor,
                        follow_symlinks=False,
                    )
                except FileNotFoundError as exc:
                    if exc.filename == mapping["source_uid"]:
                        raise SpaceConflictError(
                            "source runtime is missing after identifier cutover",
                            details={"reason": "source_runtime_missing"},
                        ) from exc
                else:
                    raise SpaceConflictError(
                        "legacy source runtime still exists after identifier cutover",
                        details={"reason": "legacy_runtime_present"},
                    )
                if (
                    int(observed.st_dev) != mapping["runtime_device"]
                    or int(observed.st_ino) != mapping["runtime_inode"]
                ):
                    raise SpaceConflictError(
                        "source runtime changed after identifier cutover",
                        details={"reason": "source_runtime_changed"},
                    )

        for rewrite in plan["workspace_journal_rewrites"]:
            entries = self.workspaces._read_index_change_journal(
                WorkspaceRuntimePaths(self.data_root, rewrite["workspace_id"])
            )
            if hashlib.sha256(encode_json(entries).encode()).hexdigest() != rewrite["after_sha256"]:
                raise SpaceConflictError(
                    "work-folder source journal changed after identifier cutover",
                    details={"reason": "workspace_journal_drift"},
                )

        for rewrite in plan["golden_rewrites"]:
            old_directory = self.data_root / "goldens" / rewrite["source_registry_id"]
            new_directory = self.data_root / "goldens" / rewrite["source_uid"]
            if old_directory.exists() or not new_directory.is_dir():
                raise SpaceConflictError(
                    "golden directory changed after identifier cutover",
                    details={"reason": "golden_directory_drift"},
                )
            for item in rewrite["files"]:
                encoded = (new_directory / item["relative_path"]).read_bytes()
                if hashlib.sha256(encoded).hexdigest() != item["after_sha256"]:
                    raise SpaceConflictError(
                        "golden annotation changed after identifier cutover",
                        details={"reason": "golden_drift"},
                    )

        with space_read_connection(self.data_root) as connection:
            for resource in plan["resource_locators"]:
                row = connection.execute(
                    "SELECT locator_json FROM resources WHERE resource_uid = ?",
                    (resource["resource_uid"],),
                ).fetchone()
                if row is None or row["locator_json"] != encode_json(resource["locator"]):
                    raise SpaceConflictError(
                        "canonical resource binding changed after identifier cutover",
                        details={"reason": "space_registry_drift"},
                    )

    def _restore_cutover_files(
        self,
        plan: dict[str, Any],
        rollback: dict[str, Any],
    ) -> None:
        with private_directory(self.data_root / "corpora") as parent_descriptor:
            for mapping in reversed(plan["source_mappings"]):
                old_id = mapping["source_registry_id"]
                source_uid = mapping["source_uid"]
                old_exists = os.path.exists(self.data_root / "corpora" / old_id)
                new_exists = os.path.exists(self.data_root / "corpora" / source_uid)
                if new_exists and not old_exists:
                    os.rename(
                        source_uid,
                        old_id,
                        src_dir_fd=parent_descriptor,
                        dst_dir_fd=parent_descriptor,
                    )
                elif old_exists and not new_exists:
                    continue
                else:
                    raise SpaceConflictError(
                        "source runtime could not be restored after failed cutover",
                        details={"reason": "source_runtime_restore_conflict"},
                    )
            os.fsync(parent_descriptor)

        for rewrite in reversed(plan["golden_rewrites"]):
            old_id = rewrite["source_registry_id"]
            source_uid = rewrite["source_uid"]
            old_directory = self.data_root / "goldens" / old_id
            new_directory = self.data_root / "goldens" / source_uid
            if new_directory.exists() and not old_directory.exists():
                with private_directory(self.data_root / "goldens") as parent_descriptor:
                    os.rename(
                        source_uid,
                        old_id,
                        src_dir_fd=parent_descriptor,
                        dst_dir_fd=parent_descriptor,
                    )
                    os.fsync(parent_descriptor)
            elif not old_directory.exists() or new_directory.exists():
                raise SpaceConflictError(
                    "golden directory could not be restored after failed cutover",
                    details={"reason": "golden_restore_conflict"},
                )
            for relative_path, snapshot in rollback["golden_files"].get(old_id, {}).items():
                path = old_directory / relative_path
                self._atomic_private_bytes(
                    path,
                    base64.b64decode(snapshot["data_base64"], validate=True),
                    mode=int(snapshot["mode"]),
                )

        for workspace_id, entries in rollback["workspace_journals"].items():
            self.workspaces._write_index_change_journal(
                WorkspaceRuntimePaths(self.data_root, workspace_id),
                entries,
            )

    def identifier_cutover_apply(
        self,
        *,
        expected_input_sha256: str,
        confirm_apply: bool,
        policy: object = None,
    ) -> dict[str, Any]:
        expected = self._normalize_expected_sha256(expected_input_sha256)
        if confirm_apply is not True:
            raise SpaceValidationError("source identifier cutover requires explicit confirmation")

        with ExitStack() as stack:
            stack.enter_context(source_workspace_registry_lock(self.data_root))
            stack.enter_context(workspace_writer_lock(self.data_root))
            stack.enter_context(context_writer_lock(self.data_root))
            stack.enter_context(space_writer_lock(self.data_root))

            with space_connection(self.data_root) as connection:
                active = self._active_identifier_cutover(connection)
                if active is not None and active["input_sha256"] != expected:
                    raise SpaceConflictError(
                        "another source identifier cutover is active",
                        details={
                            "reason": "active_cutover_conflict",
                            "cutover_id": active["cutover_id"],
                        },
                    )
                if active is not None and active["state"] == "complete":
                    completed_plan = json.loads(active["plan_json"])
                    self._verify_identifier_cutover(completed_plan)
                    return {
                        "cutover_id": active["cutover_id"],
                        "input_sha256": expected,
                        "state": "complete",
                        "applied": False,
                        "resumed": False,
                        "keeps_legacy_aliases": False,
                        "changes_user_files": False,
                    }
                if active is None:
                    plan = self.identifier_cutover_plan(policy=policy)
                    if plan["input_sha256"] != expected:
                        raise SpaceConflictError(
                            "source identifier inputs changed after cutover planning",
                            details={
                                "reason": "cutover_input_changed",
                                "expected_input_sha256": expected,
                                "current_input_sha256": plan["input_sha256"],
                            },
                        )
                    if not plan["ready"]:
                        raise SpaceConflictError(
                            "source identifier cutover has unresolved blockers",
                            details={"reason": "cutover_blocked", "blockers": plan["blockers"]},
                        )
                    cutover_id = f"cut_{uuid.uuid4().hex}"
                    rollback = self._cutover_rollback_snapshot(plan)
                    now = utc_now()
                    connection.execute(
                        """
                        INSERT INTO source_identifier_cutovers(
                            cutover_id, input_sha256, state, plan_json,
                            rollback_json, created_at, updated_at
                        ) VALUES (?, ?, 'prepared', ?, ?, ?, ?)
                        """,
                        (
                            cutover_id,
                            expected,
                            encode_json(plan),
                            encode_json(rollback),
                            now,
                            now,
                        ),
                    )
                    resumed = False
                else:
                    cutover_id = active["cutover_id"]
                    plan = json.loads(active["plan_json"])
                    rollback = json.loads(active["rollback_json"])
                    resumed = True

            for mapping in plan["source_mappings"]:
                old_runtime = RuntimePaths(
                    self.data_root,
                    mapping["source_registry_id"],
                )
                new_runtime = RuntimePaths(self.data_root, mapping["source_uid"])
                lock_runtime = old_runtime if old_runtime.corpus_root.exists() else new_runtime
                stack.enter_context(writer_lock(lock_runtime.corpus_root / "writer.lock"))

            if not rollback.get("database_backups"):
                rollback["database_backups"] = self._backup_cutover_databases(cutover_id)
                with space_connection(self.data_root) as connection:
                    self._ensure_identifier_cutover_schema(connection)
                    connection.execute(
                        """
                        UPDATE source_identifier_cutovers
                        SET rollback_json = ?, updated_at = ?
                        WHERE cutover_id = ? AND state IN ('prepared', 'applying')
                        """,
                        (encode_json(rollback), utc_now(), cutover_id),
                    )

            with space_connection(self.data_root) as connection:
                self._ensure_identifier_cutover_schema(connection)
                updated = connection.execute(
                    """
                    UPDATE source_identifier_cutovers
                    SET state = 'applying', updated_at = ?
                    WHERE cutover_id = ? AND state IN ('prepared', 'applying')
                    """,
                    (utc_now(), cutover_id),
                ).rowcount
                if updated != 1:
                    raise SpaceConflictError(
                        "source identifier cutover receipt is not applicable",
                        details={"reason": "cutover_receipt_changed"},
                    )

            database_committed = False
            try:
                self._apply_workspace_journal_rewrites(plan)
                self._apply_golden_rewrites(plan)
                self._apply_corpus_runtime_renames(plan)
                self._apply_identifier_database_transaction(plan, cutover_id=cutover_id)
                database_committed = True
            except Exception:
                if not database_committed:
                    try:
                        self._restore_cutover_files(plan, rollback)
                    except Exception as rollback_exc:
                        raise SpaceConflictError(
                            "source identifier cutover failed and file rollback was incomplete",
                            details={
                                "reason": "cutover_file_rollback_failed",
                                "cutover_id": cutover_id,
                            },
                        ) from rollback_exc
                    with space_connection(self.data_root) as connection:
                        self._ensure_identifier_cutover_schema(connection)
                        connection.execute(
                            """
                            UPDATE source_identifier_cutovers
                            SET state = 'prepared', updated_at = ?
                            WHERE cutover_id = ? AND state = 'applying'
                            """,
                            (utc_now(), cutover_id),
                        )
                raise

        return {
            "cutover_id": cutover_id,
            "input_sha256": expected,
            "state": "complete",
            "applied": True,
            "resumed": resumed,
            "source_count": len(plan["source_mappings"]),
            "context_id_replacement_count": len(plan["context_id_replacements"]),
            "keeps_legacy_aliases": False,
            "changes_user_files": False,
        }

    @staticmethod
    def _normalize_cutover_id(value: str) -> str:
        if not isinstance(value, str) or SPACE_CUTOVER_ID_RE.fullmatch(value) is None:
            raise SpaceValidationError("source identifier cutover id is not valid")
        return value

    def identifier_cutover_rollback(
        self,
        *,
        cutover_id: str,
        expected_input_sha256: str,
        confirm_rollback: bool,
    ) -> dict[str, Any]:
        normalized_id = self._normalize_cutover_id(cutover_id)
        expected = self._normalize_expected_sha256(expected_input_sha256)
        if confirm_rollback is not True:
            raise SpaceValidationError("source identifier rollback requires explicit confirmation")

        with ExitStack() as stack:
            stack.enter_context(source_workspace_registry_lock(self.data_root))
            stack.enter_context(workspace_writer_lock(self.data_root))
            stack.enter_context(context_writer_lock(self.data_root))
            stack.enter_context(space_writer_lock(self.data_root))
            with space_connection(self.data_root) as connection:
                self._ensure_identifier_cutover_schema(connection)
                receipt = connection.execute(
                    "SELECT * FROM source_identifier_cutovers WHERE cutover_id = ?",
                    (normalized_id,),
                ).fetchone()
                if receipt is None:
                    raise SpaceNotFoundError(
                        "source identifier cutover does not exist",
                        details={"cutover_id": normalized_id},
                    )
                if receipt["input_sha256"] != expected:
                    raise SpaceConflictError(
                        "source identifier rollback hash does not match its receipt",
                        details={"reason": "cutover_input_changed"},
                    )
                if receipt["state"] == "rolled_back":
                    return {
                        "cutover_id": normalized_id,
                        "input_sha256": expected,
                        "state": "rolled_back",
                        "rolled_back": False,
                        "changes_user_files": False,
                    }
                if receipt["state"] != "complete":
                    raise SpaceConflictError(
                        "source identifier cutover is not complete",
                        details={
                            "reason": "cutover_state_conflict",
                            "state": receipt["state"],
                        },
                    )
                plan = json.loads(receipt["plan_json"])
                rollback = json.loads(receipt["rollback_json"])

            self._verify_identifier_cutover(plan)
            for mapping in plan["source_mappings"]:
                runtime = RuntimePaths(self.data_root, mapping["source_uid"])
                stack.enter_context(writer_lock(runtime.corpus_root / "writer.lock"))

            files_restored = False
            try:
                self._restore_cutover_files(plan, rollback)
                files_restored = True
                self._rollback_identifier_database_transaction(
                    plan,
                    cutover_id=normalized_id,
                )
            except Exception:
                if files_restored:
                    try:
                        self._apply_workspace_journal_rewrites(plan)
                        self._apply_golden_rewrites(plan)
                        self._apply_corpus_runtime_renames(plan)
                    except Exception as forward_exc:
                        raise SpaceConflictError(
                            "source identifier rollback failed and forward recovery was incomplete",
                            details={
                                "reason": "cutover_forward_recovery_failed",
                                "cutover_id": normalized_id,
                            },
                        ) from forward_exc
                raise

        return {
            "cutover_id": normalized_id,
            "input_sha256": expected,
            "state": "rolled_back",
            "rolled_back": True,
            "changes_user_files": False,
        }

    @staticmethod
    def _normalize_expected_sha256(value: str) -> str:
        if not isinstance(value, str) or not SHA256_RE.fullmatch(value.lower()):
            raise SpaceValidationError(
                "expected migration input hash must be a lowercase SHA-256 value"
            )
        return value.lower()

    @staticmethod
    def _normalize_migration_id(value: str) -> str:
        if not isinstance(value, str) or not SPACE_MIGRATION_ID_RE.fullmatch(value):
            raise SpaceValidationError("migration id is not valid")
        return value

    @staticmethod
    def _active_receipt(connection: Any) -> Any:
        return connection.execute(
            """
            SELECT * FROM space_migration_receipts
            WHERE state IN ('prepared', 'applying', 'complete')
            ORDER BY created_at, migration_id
            LIMIT 1
            """
        ).fetchone()

    @staticmethod
    def _registry_counts(connection: Any) -> dict[str, int]:
        return {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("resources", "spaces", "connections", "connection_recoveries")
        }

    @staticmethod
    def _registry_matches_plan(connection: Any, plan: dict[str, Any]) -> bool:
        expected_resources = {
            (
                item["resource_uid"],
                item["resource_kind"],
                item["provider_kind"],
                item["location"],
                item["location_nfc"],
                item["root_device"],
                item["root_inode"],
                encode_json(item["source_scope"]),
            )
            for item in plan["resources"]
        }
        actual_resources = {
            (
                row["resource_uid"],
                row["resource_kind"],
                row["provider_kind"],
                row["root_path"],
                row["root_path_nfc"],
                row["root_device"],
                row["root_inode"],
                row["source_scope_json"],
            )
            for row in connection.execute("SELECT * FROM resources").fetchall()
        }
        expected_spaces = {
            (
                item["space_uid"],
                item["space_id"],
                item["display_name"],
                item["state"],
                item["context_id"],
                item["context_access_scope"],
                item["context_status"],
                item["context_status_reason"],
                1,
            )
            for item in plan["spaces"]
        }
        actual_spaces = {
            (
                row["space_uid"],
                row["space_id"],
                row["display_name"],
                row["state"],
                row["context_id"],
                row["context_access_scope"],
                row["context_status"],
                row["context_status_reason"],
                row["generation"],
            )
            for row in connection.execute("SELECT * FROM spaces").fetchall()
        }
        expected_connections = {
            (
                connection_item["connection_uid"],
                space["space_uid"],
                connection_item["connection_id"],
                connection_item["display_name"],
                connection_item["resource_uid"],
                int("source" in connection_item["roles"]),
                int("work" in connection_item["roles"]),
                connection_item["access_scope"],
                connection_item["permission"],
                connection_item["index_mode"],
                int(connection_item["primary_work"]),
                connection_item["current_relative_path"],
                1,
            )
            for space in plan["spaces"]
            for connection_item in space["connections"]
        }
        actual_connections = {
            (
                row["connection_uid"],
                row["space_uid"],
                row["connection_id"],
                row["display_name"],
                row["resource_uid"],
                row["source_role"],
                row["work_role"],
                row["access_scope"],
                row["permission"],
                row["index_mode"],
                row["primary_work"],
                row["current_relative_path"],
                row["generation"],
            )
            for row in connection.execute("SELECT * FROM connections").fetchall()
        }
        recovery_count = int(
            connection.execute("SELECT COUNT(*) FROM connection_recoveries").fetchone()[0]
        )
        return (
            actual_resources == expected_resources
            and actual_spaces == expected_spaces
            and actual_connections == expected_connections
            and recovery_count == 0
        )

    @staticmethod
    def _materialize_registry(connection: Any, plan: dict[str, Any], *, now: str) -> None:
        connection.executemany(
            """
            INSERT INTO resources(
                resource_uid, resource_kind, provider_kind,
                root_path, root_path_nfc, root_device, root_inode,
                locator_json, source_scope_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '{}', ?, ?, ?)
            """,
            [
                (
                    item["resource_uid"],
                    item["resource_kind"],
                    item["provider_kind"],
                    item["location"],
                    item["location_nfc"],
                    item["root_device"],
                    item["root_inode"],
                    encode_json(item["source_scope"]),
                    now,
                    now,
                )
                for item in plan["resources"]
            ],
        )
        connection.executemany(
            """
            INSERT INTO spaces(
                space_uid, space_id, display_name, state, context_id,
                context_access_scope, context_status, context_status_reason,
                generation, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            [
                (
                    item["space_uid"],
                    item["space_id"],
                    item["display_name"],
                    item["state"],
                    item["context_id"],
                    item["context_access_scope"],
                    item["context_status"],
                    item["context_status_reason"],
                    now,
                    now,
                )
                for item in plan["spaces"]
            ],
        )
        connection.executemany(
            """
            INSERT INTO connections(
                connection_uid, space_uid, connection_id, display_name,
                resource_uid, source_role, work_role, access_scope,
                permission, index_mode, primary_work, current_relative_path,
                generation, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            [
                (
                    connection_item["connection_uid"],
                    space["space_uid"],
                    connection_item["connection_id"],
                    connection_item["display_name"],
                    connection_item["resource_uid"],
                    int("source" in connection_item["roles"]),
                    int("work" in connection_item["roles"]),
                    connection_item["access_scope"],
                    connection_item["permission"],
                    connection_item["index_mode"],
                    int(connection_item["primary_work"]),
                    connection_item["current_relative_path"],
                    now,
                    now,
                )
                for space in plan["spaces"]
                for connection_item in space["connections"]
            ],
        )

    def migration_apply(
        self,
        *,
        expected_input_sha256: str,
        confirm_apply: bool,
        policy: object = None,
    ) -> dict[str, Any]:
        """Materialize the active canonical registry with a resumable receipt."""

        expected = self._normalize_expected_sha256(expected_input_sha256)
        if confirm_apply is not True:
            raise SpaceValidationError("Space registry migration requires explicit confirmation")

        with (
            source_workspace_registry_lock(self.data_root),
            workspace_writer_lock(self.data_root),
            context_writer_lock(self.data_root),
            space_writer_lock(self.data_root),
        ):
            plan = self.migration_plan(policy=policy)
            if plan["input_sha256"] != expected:
                raise SpaceConflictError(
                    "Space registry inputs changed after migration planning",
                    details={
                        "reason": "migration_input_changed",
                        "expected_input_sha256": expected,
                        "current_input_sha256": plan["input_sha256"],
                    },
                )
            if not plan["ready"]:
                raise SpaceConflictError(
                    "Space registry migration has unresolved blockers",
                    details={"reason": "migration_blocked", "blockers": plan["blockers"]},
                )
            plan_json = encode_json(plan)
            if len(plan_json.encode()) > SPACE_MIGRATION_MAX_PLAN_BYTES:
                raise BudgetExceededError(
                    "Space registry migration plan exceeds the private receipt budget",
                    details={"maximum_bytes": SPACE_MIGRATION_MAX_PLAN_BYTES},
                )

            migration_id: str
            with space_connection(self.data_root) as connection:
                active = self._active_receipt(connection)
                if active is not None:
                    if active["input_sha256"] != expected:
                        raise SpaceConflictError(
                            "another Space registry migration is active",
                            details={
                                "reason": "active_migration_conflict",
                                "migration_id": active["migration_id"],
                            },
                        )
                    migration_id = active["migration_id"]
                    if active["state"] == "complete":
                        stored_plan = json.loads(active["plan_json"])
                        if not self._registry_matches_plan(connection, stored_plan):
                            raise SpaceConflictError(
                                "canonical Space registry changed after migration",
                                details={
                                    "reason": "space_registry_drift",
                                    "migration_id": migration_id,
                                },
                            )
                        return {
                            "migration_id": migration_id,
                            "input_sha256": expected,
                            "state": "complete",
                            "applied": False,
                            "resumed": False,
                            "changes_user_files": False,
                            "legacy_identifier_cutover": False,
                        }
                    if active["state"] not in {"prepared", "applying"}:
                        raise SpaceConflictError(
                            "Space registry migration is not resumable",
                            details={
                                "reason": "migration_state_conflict",
                                "migration_id": migration_id,
                                "state": active["state"],
                            },
                        )
                    if any(self._registry_counts(connection).values()):
                        raise SpaceConflictError(
                            "incomplete Space registry migration left unexpected rows",
                            details={
                                "reason": "partial_registry_state",
                                "migration_id": migration_id,
                            },
                        )
                    resumed = True
                else:
                    counts = self._registry_counts(connection)
                    if any(counts.values()):
                        raise SpaceConflictError(
                            "canonical Space registry already contains unmanaged rows",
                            details={"reason": "unmanaged_registry_state", "counts": counts},
                        )
                    migration_id = f"mig_{uuid.uuid4().hex}"
                    now = utc_now()
                    connection.execute(
                        """
                        INSERT INTO space_migration_receipts(
                            migration_id, input_sha256, state, plan_json,
                            rollback_json, created_at, updated_at
                        ) VALUES (?, ?, 'prepared', ?, '{}', ?, ?)
                        """,
                        (migration_id, expected, plan_json, now, now),
                    )
                    resumed = False

            now = utc_now()
            with space_connection(self.data_root) as connection:
                receipt = connection.execute(
                    "SELECT * FROM space_migration_receipts WHERE migration_id = ?",
                    (migration_id,),
                ).fetchone()
                if receipt is None or receipt["input_sha256"] != expected:
                    raise SpaceConflictError(
                        "Space registry migration receipt changed before apply",
                        details={"reason": "migration_receipt_changed"},
                    )
                if receipt["state"] == "complete":
                    if not self._registry_matches_plan(connection, plan):
                        raise SpaceConflictError(
                            "canonical Space registry changed after migration",
                            details={"reason": "space_registry_drift"},
                        )
                    return {
                        "migration_id": migration_id,
                        "input_sha256": expected,
                        "state": "complete",
                        "applied": False,
                        "resumed": True,
                        "changes_user_files": False,
                        "legacy_identifier_cutover": False,
                    }
                if receipt["state"] not in {"prepared", "applying"}:
                    raise SpaceConflictError(
                        "Space registry migration receipt is not applicable",
                        details={
                            "reason": "migration_state_conflict",
                            "state": receipt["state"],
                        },
                    )
                if any(self._registry_counts(connection).values()):
                    raise SpaceConflictError(
                        "Space registry is not empty before migration apply",
                        details={"reason": "partial_registry_state"},
                    )
                connection.execute(
                    """
                    UPDATE space_migration_receipts
                    SET state = 'applying', updated_at = ?
                    WHERE migration_id = ?
                    """,
                    (now, migration_id),
                )
                self._materialize_registry(connection, plan, now=now)
                if not self._registry_matches_plan(connection, plan):
                    raise SpaceConflictError(
                        "materialized Space registry does not match its migration plan",
                        details={"reason": "registry_verification_failed"},
                    )
                rollback = {
                    "resource_uids": [item["resource_uid"] for item in plan["resources"]],
                    "space_uids": [item["space_uid"] for item in plan["spaces"]],
                    "connection_uids": [
                        item["connection_uid"]
                        for space in plan["spaces"]
                        for item in space["connections"]
                    ],
                }
                connection.execute(
                    """
                    UPDATE space_migration_receipts
                    SET state = 'complete', rollback_json = ?, updated_at = ?
                    WHERE migration_id = ?
                    """,
                    (encode_json(rollback), utc_now(), migration_id),
                )

            return {
                "migration_id": migration_id,
                "input_sha256": expected,
                "state": "complete",
                "applied": True,
                "resumed": resumed,
                "space_count": len(plan["spaces"]),
                "resource_count": len(plan["resources"]),
                "connection_count": sum(len(space["connections"]) for space in plan["spaces"]),
                "changes_user_files": False,
                "legacy_identifier_cutover": False,
            }

    def migration_rollback(
        self,
        *,
        migration_id: str,
        expected_input_sha256: str,
        confirm_rollback: bool,
    ) -> dict[str, Any]:
        """Roll back a prepared or completed registry-only migration."""

        normalized_id = self._normalize_migration_id(migration_id)
        expected = self._normalize_expected_sha256(expected_input_sha256)
        if confirm_rollback is not True:
            raise SpaceValidationError("Space registry rollback requires explicit confirmation")
        if not (self.data_root / "spaces.sqlite3").exists():
            raise SpaceNotFoundError("Space registry migration does not exist")

        with (
            source_workspace_registry_lock(self.data_root),
            workspace_writer_lock(self.data_root),
            context_writer_lock(self.data_root),
            space_writer_lock(self.data_root),
            space_connection(self.data_root) as connection,
        ):
            receipt = connection.execute(
                "SELECT * FROM space_migration_receipts WHERE migration_id = ?",
                (normalized_id,),
            ).fetchone()
            if receipt is None:
                raise SpaceNotFoundError(
                    "Space registry migration does not exist",
                    details={"migration_id": normalized_id},
                )
            if receipt["input_sha256"] != expected:
                raise SpaceConflictError(
                    "Space registry rollback hash does not match its receipt",
                    details={
                        "reason": "migration_input_changed",
                        "migration_id": normalized_id,
                    },
                )
            if receipt["state"] == "rolled_back":
                return {
                    "migration_id": normalized_id,
                    "input_sha256": expected,
                    "state": "rolled_back",
                    "rolled_back": False,
                    "changes_user_files": False,
                }
            if receipt["state"] not in {"prepared", "applying", "complete"}:
                raise SpaceConflictError(
                    "Space registry migration cannot be rolled back from its current state",
                    details={"reason": "migration_state_conflict", "state": receipt["state"]},
                )

            cutover_locator_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM resources WHERE locator_json != '{}'"
                ).fetchone()[0]
            )
            if cutover_locator_count:
                raise SpaceConflictError(
                    "Space registry migration cannot be rolled back after identifier cutover",
                    details={
                        "reason": "identifier_cutover_complete",
                        "resource_count": cutover_locator_count,
                    },
                )

            stored_plan = json.loads(receipt["plan_json"])
            counts = self._registry_counts(connection)
            if receipt["state"] in {"prepared", "applying"}:
                if any(counts.values()):
                    raise SpaceConflictError(
                        "incomplete Space registry migration contains unexpected rows",
                        details={"reason": "partial_registry_state", "counts": counts},
                    )
            elif not self._registry_matches_plan(connection, stored_plan):
                raise SpaceConflictError(
                    "canonical Space registry changed after migration",
                    details={
                        "reason": "space_registry_drift",
                        "migration_id": normalized_id,
                    },
                )

            connection.execute("DELETE FROM connection_recoveries")
            connection.execute("DELETE FROM connections")
            connection.execute("DELETE FROM spaces")
            connection.execute("DELETE FROM resources")
            if any(self._registry_counts(connection).values()):
                raise SpaceConflictError(
                    "Space registry rollback did not remove all materialized rows",
                    details={"reason": "rollback_verification_failed"},
                )
            connection.execute(
                """
                UPDATE space_migration_receipts
                SET state = 'rolled_back', updated_at = ?
                WHERE migration_id = ?
                """,
                (utc_now(), normalized_id),
            )

        return {
            "migration_id": normalized_id,
            "input_sha256": expected,
            "state": "rolled_back",
            "rolled_back": True,
            "changes_user_files": False,
        }
