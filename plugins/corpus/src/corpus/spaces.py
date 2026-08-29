"""Canonical read projection over contexts, sources, and editable folders."""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .contexts import CONTEXT_MAX_LIMIT
from .database import encode_json, list_corpora
from .errors import (
    BudgetExceededError,
    SpaceConflictError,
    SpaceNotFoundError,
    SpaceValidationError,
)

SPACE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
SPACE_AUDIENCES = {"local_cli", "external_mcp"}
SPACE_DEFAULT_LIMIT = 100
SPACE_MAX_LIMIT = 100
SPACE_MAX_OFFSET = 10_000
SPACE_MAX_SERIALIZED_BYTES = 1024 * 1024
SPACE_REFERENCE_MAX_CHARS = 8_192


def encode_space_reference(kind: str, payload: dict[str, Any]) -> str:
    if kind not in {"read", "cursor"}:
        raise SpaceValidationError("unsupported Space reference kind")
    canonical = {"version": 1, **payload}
    encoded = (
        base64.urlsafe_b64encode(encode_json(canonical).encode()).decode().rstrip("=")
    )
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
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= SPACE_MAX_LIMIT
    ):
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
    return (
        "remote_allowed"
        if execution_policy == "external_host_allowed"
        else "local_only"
    )


def _canonical_root(value: str) -> Path:
    return Path(value).expanduser().resolve(strict=False)


def _serialized_size(value: object) -> int:
    return len(encode_json(value).encode())


class SpaceService:
    """Project current Context, Source and Work registrations as Spaces."""

    def __init__(
        self,
        data_root: Path,
        *,
        contexts: Any,
        context_skills: Any,
        workspaces: Any,
        source_state: Callable[[str], str],
    ) -> None:
        self.data_root = data_root
        self.contexts = contexts
        self.context_skills = context_skills
        self.workspaces = workspaces
        self.source_state = source_state

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

    def _project_connection(
        self,
        group: dict[str, Any],
        *,
        audience: str,
    ) -> dict[str, Any] | None:
        scopes = set(group["access_scopes"])
        access_scope = (
            "remote_allowed" if scopes == {"remote_allowed"} else "local_only"
        )
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
                "unknown"
                if work_folder is not None and connection_state == "connected"
                else None
            ),
            "configuration_state": (
                "ready" if len(scopes) == 1 else "access_scope_conflict"
            ),
            "_workspace_id": (
                work_folder["workspace_id"] if work_folder is not None else None
            ),
            "_source_ids": [source["corpus_id"] for source in sources],
        }
        if sources:
            result["source_state"] = self._connection_source_state(sources)
        if audience == "local_cli":
            result["location"] = str(group["root"])
        return result

    @staticmethod
    def _connection_source_state(sources: list[dict[str, Any]]) -> str | None:
        if not sources:
            return None
        states = {str(source["_source_state"]) for source in sources}
        if len(states) == 1:
            return next(iter(states))
        if "unavailable" in states or "partial" in states:
            return "partial"
        if "needs_refresh" in states:
            return "needs_refresh"
        return "ready"

    @staticmethod
    def _context_access_scope(connections: list[dict[str, Any]]) -> str:
        remote_work = any(
            "work" in connection["roles"]
            and connection["access_scope"] == "remote_allowed"
            for connection in connections
        )
        if remote_work:
            return "remote_allowed"
        source_connections = [
            connection for connection in connections if "source" in connection["roles"]
        ]
        if source_connections and all(
            connection["access_scope"] == "remote_allowed"
            for connection in source_connections
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
            if (projected := self._project_connection(group, audience="local_cli"))
            is not None
        ]
        context_access_scope = (
            self._context_access_scope(all_connections) if context is not None else None
        )
        visible_connections = [
            projected
            for group in groups
            if (projected := self._project_connection(group, audience=audience))
            is not None
        ]
        remote_visible = (
            bool(visible_connections) or context_access_scope == "remote_allowed"
        )
        if audience == "external_mcp" and not remote_visible:
            return None

        primary_work = next(
            (
                connection
                for connection in visible_connections
                if "work" in connection["roles"]
            ),
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
            "current_file": (
                primary_work["current_file"] if primary_work is not None else None
            ),
        }
        return result

    def _spaces(self, *, audience: str) -> list[dict[str, Any]]:
        _validate_audience(audience)
        corpora = list_corpora(self.data_root)
        for corpus in corpora:
            corpus["_source_state"] = self.source_state(corpus["corpus_id"])
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
    def _public_connection(connection: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value for key, value in connection.items() if not key.startswith("_")
        }

    @classmethod
    def _public_space(cls, space: dict[str, Any]) -> dict[str, Any]:
        public = {key: value for key, value in space.items() if not key.startswith("_")}
        public["connections"] = [
            cls._public_connection(connection) for connection in space["connections"]
        ]
        return public

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
                    "available_connection_ids": [
                        item["connection_id"] for item in visible
                    ],
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

        workspace_id = selected.get("_workspace_id")
        source_ids = list(selected.get("_source_ids", []))
        if "work" in roles and not isinstance(workspace_id, str):
            raise SpaceConflictError(
                "Work Connection backing is unavailable",
                details={
                    "space_id": normalized_space_id,
                    "connection_id": selected_id,
                    "reason": "workspace_binding_missing",
                },
            )
        if "source" in roles and not source_ids:
            raise SpaceConflictError(
                "Source Connection backing is unavailable",
                details={
                    "space_id": normalized_space_id,
                    "connection_id": selected_id,
                    "reason": "source_binding_missing",
                },
            )
        return {
            "space": self._public_space(space),
            "connection": self._public_connection(selected),
            "_workspace_id": workspace_id,
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
            limit=limit,
            offset=offset,
            audience="local_cli",
        )
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
