"""Read and rewrite the agent's persistent relationship model of the user."""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter, ValidationError

from .errors import HypesError
from .model import (
    DeleteOperation,
    PutEdgeOperation,
    PutNodeOperation,
    PutPredicateOperation,
    RewriteOperation,
)
from .store import HypesStore, _canonical

_OPERATIONS_ADAPTER = TypeAdapter(list[RewriteOperation])
_FOCUS_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
_PERSISTENT_REF = re.compile(r"^(node|pred|edge)_[0-9a-f]{32}$")
_OUTLINE_CONTINUATION = re.compile(r"^outline-v1:([1-9][0-9]{0,9})$")
_MAX_OPERATIONS = 100
_MAX_READ_LIMIT = 200
_MAX_FOCUS_SEEDS = 12
_EDGE_EXPANSION_RESERVE = 3
_OPERATION_INPUT_TYPES = (
    Mapping,
    PutNodeOperation,
    PutPredicateOperation,
    PutEdgeOperation,
    DeleteOperation,
)


def _new_ref(kind: str) -> str:
    return f"{kind}_{uuid.uuid4().hex}"


def _ref_kind(ref: str) -> str | None:
    match = _PERSISTENT_REF.fullmatch(ref)
    return match.group(1) if match else None


def _loads_object(value: str) -> dict[str, Any]:
    loaded = json.loads(value)
    if not isinstance(loaded, dict):
        raise TypeError("stored JSON object is invalid")
    return loaded


def _loads_list(value: str) -> list[str]:
    loaded = json.loads(value)
    if not isinstance(loaded, list) or not all(
        isinstance(item, str) for item in loaded
    ):
        raise TypeError("stored JSON list is invalid")
    return loaded


def _node_value(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "node_id": row["node_id"],
        "labels": _loads_list(row["labels_json"]),
        "name": row["name"],
        "description": row["description"] or None,
        "aliases": _loads_list(row["aliases_json"]),
        "attributes": _loads_object(row["attributes_json"]),
    }


def _predicate_value(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "predicate_id": row["predicate_id"],
        "name": row["name"],
        "description": row["description"] or None,
        "aliases": _loads_list(row["aliases_json"]),
    }


def _edge_value(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "edge_id": row["edge_id"],
        "source_id": row["source_id"],
        "predicate_id": row["predicate_id"],
        "target_id": row["target_id"],
        "qualifiers": _loads_object(row["qualifiers_json"]),
    }


class HypesService:
    """Small application service over one local ontology graph."""

    def __init__(self, data_root: Path | None = None) -> None:
        self.store = HypesStore(data_root)

    @staticmethod
    def _validated_operations(
        operations: Any,
    ) -> list[RewriteOperation]:
        if not isinstance(operations, list) or not all(
            isinstance(operation, _OPERATION_INPUT_TYPES) for operation in operations
        ):
            raise HypesError("invalid_patch", "the ontology patch is invalid")
        try:
            validated = _OPERATIONS_ADAPTER.validate_python(operations)
        except (TypeError, ValidationError) as exc:
            raise HypesError("invalid_patch", "the ontology patch is invalid") from exc
        if not validated:
            raise HypesError(
                "invalid_patch", "the ontology patch must contain an operation"
            )
        if len(validated) > _MAX_OPERATIONS:
            raise HypesError(
                "patch_limit_exceeded",
                f"the ontology patch may contain at most {_MAX_OPERATIONS} operations",
            )
        return validated

    @staticmethod
    def _resolved_ref(
        ref: str,
        *,
        expected_kind: str,
        ref_map: Mapping[str, str],
        temporary_kinds: Mapping[str, str],
    ) -> str:
        if ref.startswith("$"):
            actual_kind = temporary_kinds.get(ref)
            if actual_kind is None:
                raise HypesError(
                    "object_not_found",
                    "an edge refers to an undefined temporary object",
                    details={"ref": ref},
                )
            if actual_kind != expected_kind:
                raise HypesError(
                    "reference_type_mismatch",
                    "an edge reference has the wrong ontology object type",
                    details={"ref": ref, "expected_type": expected_kind},
                )
            return ref_map[ref]
        if _ref_kind(ref) != expected_kind:
            raise HypesError(
                "reference_type_mismatch",
                "an edge reference has the wrong ontology object type",
                details={"ref": ref, "expected_type": expected_kind},
            )
        return ref

    @staticmethod
    def _require_existing(
        connection: sqlite3.Connection,
        *,
        kind: str,
        ref: str,
    ) -> None:
        table, column = {
            "node": ("nodes", "node_id"),
            "pred": ("predicates", "predicate_id"),
            "edge": ("edges", "edge_id"),
        }[kind]
        row = connection.execute(
            f"SELECT 1 FROM {table} WHERE {column} = ?", (ref,)
        ).fetchone()
        if row is None:
            raise HypesError(
                "object_not_found",
                "the ontology object does not exist",
                details={"ref": ref},
            )

    def rewrite(
        self,
        *,
        operations: list[RewriteOperation | Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Apply one complete graph patch or leave the graph unchanged."""

        validated = self._validated_operations(operations)
        ref_map: dict[str, str] = {}
        temporary_kinds: dict[str, str] = {}
        target_refs: set[str] = set()
        normalized: list[tuple[str, str, Any]] = []

        for operation in validated:
            if isinstance(operation, PutNodeOperation):
                kind = "node"
            elif isinstance(operation, PutPredicateOperation):
                kind = "pred"
            elif isinstance(operation, PutEdgeOperation):
                kind = "edge"
            elif isinstance(operation, DeleteOperation):
                kind = _ref_kind(operation.ref)
                if kind is None:  # Pydantic normally catches this first.
                    raise HypesError(
                        "invalid_patch", "delete requires a persistent ref"
                    )
                if operation.ref in target_refs:
                    raise HypesError(
                        "duplicate_ref",
                        "an ontology patch may target each object only once",
                        details={"ref": operation.ref},
                    )
                target_refs.add(operation.ref)
                normalized.append(("delete", operation.ref, kind))
                continue
            else:  # pragma: no cover - the discriminated union is exhaustive.
                raise HypesError("invalid_patch", "unsupported ontology operation")

            ref = operation.ref
            if ref.startswith("$"):
                if ref in temporary_kinds:
                    raise HypesError(
                        "duplicate_ref",
                        "a temporary ref may be defined only once",
                        details={"ref": ref},
                    )
                temporary_kinds[ref] = kind
                persistent_ref = _new_ref(kind)
                ref_map[ref] = persistent_ref
            else:
                persistent_ref = ref
            if persistent_ref in target_refs:
                raise HypesError(
                    "duplicate_ref",
                    "an ontology patch may target each object only once",
                    details={"ref": persistent_ref},
                )
            target_refs.add(persistent_ref)
            normalized.append((operation.op, persistent_ref, operation.value))

        resolved: list[tuple[str, str, Any]] = []
        for op, ref, value in normalized:
            if op != "put_edge":
                resolved.append((op, ref, value))
                continue
            edge = value.model_copy(
                update={
                    "source_ref": self._resolved_ref(
                        value.source_ref,
                        expected_kind="node",
                        ref_map=ref_map,
                        temporary_kinds=temporary_kinds,
                    ),
                    "predicate_ref": self._resolved_ref(
                        value.predicate_ref,
                        expected_kind="pred",
                        ref_map=ref_map,
                        temporary_kinds=temporary_kinds,
                    ),
                    "target_ref": self._resolved_ref(
                        value.target_ref,
                        expected_kind="node",
                        ref_map=ref_map,
                        temporary_kinds=temporary_kinds,
                    ),
                }
            )
            resolved.append((op, ref, edge))

        upserted_refs = [ref for op, ref, _ in resolved if op.startswith("put_")]
        removed_refs = [ref for op, ref, _ in resolved if op == "delete"]

        try:
            with self.store.connect() as connection:
                self.store.begin_write(connection)

                # Persistent puts are replacements, never caller-chosen creations.
                for op, ref, _ in resolved:
                    if op.startswith("put_") and ref not in ref_map.values():
                        self._require_existing(
                            connection,
                            kind={
                                "put_node": "node",
                                "put_predicate": "pred",
                                "put_edge": "edge",
                            }[op],
                            ref=ref,
                        )
                    elif op == "delete":
                        self._require_existing(connection, kind=str(_), ref=ref)

                # Entities must exist before new or replaced edges refer to them.
                for op, ref, value in resolved:
                    if op == "put_node":
                        connection.execute(
                            "INSERT INTO nodes(node_id, labels_json, name, description, "
                            "aliases_json, attributes_json) VALUES (?, ?, ?, ?, ?, ?) "
                            "ON CONFLICT(node_id) DO UPDATE SET labels_json=excluded.labels_json, "
                            "name=excluded.name, description=excluded.description, "
                            "aliases_json=excluded.aliases_json, "
                            "attributes_json=excluded.attributes_json",
                            (
                                ref,
                                _canonical(value.labels),
                                value.name,
                                value.description or "",
                                _canonical(value.aliases),
                                _canonical(value.attributes),
                            ),
                        )
                    elif op == "put_predicate":
                        connection.execute(
                            "INSERT INTO predicates(predicate_id, name, description, aliases_json) "
                            "VALUES (?, ?, ?, ?) ON CONFLICT(predicate_id) DO UPDATE SET "
                            "name=excluded.name, description=excluded.description, "
                            "aliases_json=excluded.aliases_json",
                            (
                                ref,
                                value.name,
                                value.description or "",
                                _canonical(value.aliases),
                            ),
                        )

                # Explicit edge deletion may release an entity that is deleted later in the patch.
                for op, ref, kind in resolved:
                    if op == "delete" and kind == "edge":
                        connection.execute(
                            "DELETE FROM edges WHERE edge_id = ?", (ref,)
                        )

                for op, ref, value in resolved:
                    if op == "put_edge":
                        connection.execute(
                            "INSERT INTO edges(edge_id, source_id, predicate_id, target_id, "
                            "qualifiers_json) VALUES (?, ?, ?, ?, ?) "
                            "ON CONFLICT(edge_id) DO UPDATE SET source_id=excluded.source_id, "
                            "predicate_id=excluded.predicate_id, target_id=excluded.target_id, "
                            "qualifiers_json=excluded.qualifiers_json",
                            (
                                ref,
                                value.source_ref,
                                value.predicate_ref,
                                value.target_ref,
                                _canonical(value.qualifiers),
                            ),
                        )

                for op, ref, kind in resolved:
                    if op != "delete" or kind == "edge":
                        continue
                    table, column = (
                        ("nodes", "node_id")
                        if kind == "node"
                        else ("predicates", "predicate_id")
                    )
                    connection.execute(
                        f"DELETE FROM {table} WHERE {column} = ?", (ref,)
                    )

                violations = connection.execute("PRAGMA foreign_key_check").fetchall()
                if violations:
                    raise HypesError(
                        "dangling_edge",
                        "the ontology patch would leave a dangling edge",
                    )
        except sqlite3.IntegrityError as exc:
            raise HypesError(
                "dangling_edge",
                "delete incident edges in the same patch and keep every edge endpoint valid",
            ) from exc

        return {
            "ref_map": ref_map,
            "upserted_refs": upserted_refs,
            "removed_refs": removed_refs,
        }

    @staticmethod
    def _fts_query(focus: str) -> str | None:
        tokens = _FOCUS_TOKEN.findall(focus.casefold())
        if not tokens:
            return None
        # Tokens come from a conservative Unicode word matcher; quoting makes the
        # generated expression data rather than caller-controlled FTS syntax.
        return " AND ".join(f'"{token}"*' for token in tokens[:16])

    @staticmethod
    def _focus_seeds(
        connection: sqlite3.Connection, focus: str, *, limit: int
    ) -> list[tuple[str, str]]:
        query = HypesService._fts_query(focus)
        if query is None:
            return []
        seeds: list[tuple[float, str, str]] = []
        for kind, table in (("node", "nodes_fts"), ("pred", "predicates_fts")):
            rows = connection.execute(
                f"SELECT ref, bm25({table}) AS rank FROM {table} "
                f"WHERE {table} MATCH ? ORDER BY rank, ref LIMIT ?",
                (query, limit),
            ).fetchall()
            seeds.extend((float(row["rank"]), kind, row["ref"]) for row in rows)
        seeds.sort(key=lambda item: (item[0], item[1], item[2]))
        return [(kind, ref) for _, kind, ref in seeds[:limit]]

    @staticmethod
    def _outline_seeds(
        connection: sqlite3.Connection, *, limit: int, offset: int
    ) -> tuple[list[tuple[str, str]], bool]:
        rows: list[tuple[str, str, str]] = []
        rows.extend(
            (row["name"].casefold(), "node", row["node_id"])
            for row in connection.execute("SELECT node_id, name FROM nodes")
        )
        rows.extend(
            (row["name"].casefold(), "pred", row["predicate_id"])
            for row in connection.execute("SELECT predicate_id, name FROM predicates")
        )
        rows.sort()
        page = rows[offset : offset + limit + 1]
        return (
            [(kind, ref) for _, kind, ref in page[:limit]],
            len(page) > limit,
        )

    @staticmethod
    def _outline_offset(continuation: str | None) -> int:
        if continuation is None:
            return 0
        if not isinstance(continuation, str):
            raise HypesError(
                "invalid_read",
                "continuation must be an outline cursor returned by hypes_read",
            )
        match = _OUTLINE_CONTINUATION.fullmatch(continuation)
        if match is None:
            raise HypesError(
                "invalid_read",
                "continuation must be an outline cursor returned by hypes_read",
            )
        return int(match.group(1))

    @staticmethod
    def _rows_by_ref(
        connection: sqlite3.Connection, table: str, column: str
    ) -> dict[str, sqlite3.Row]:
        return {
            row[column]: row
            for row in connection.execute(f"SELECT * FROM {table} ORDER BY {column}")
        }

    def read(
        self,
        *,
        focus: str | None = None,
        seed_refs: list[str] | None = None,
        max_hops: int = 1,
        limit: int = 50,
        continuation: str | None = None,
    ) -> dict[str, Any]:
        """Return a bounded, reference-closed slice of the current ontology."""

        if focus is not None and (
            not isinstance(focus, str) or not focus.strip() or len(focus) > 1000
        ):
            raise HypesError(
                "invalid_read",
                "focus must be a non-empty string of at most 1000 characters",
            )
        if (
            not isinstance(max_hops, int)
            or isinstance(max_hops, bool)
            or not 0 <= max_hops <= 2
        ):
            raise HypesError("invalid_read", "max_hops must be between 0 and 2")
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= _MAX_READ_LIMIT
        ):
            raise HypesError(
                "invalid_read", f"limit must be between 1 and {_MAX_READ_LIMIT}"
            )
        if seed_refs is not None and (
            not isinstance(seed_refs, list)
            or len(seed_refs) > 50
            or not all(isinstance(ref, str) for ref in seed_refs)
        ):
            raise HypesError("invalid_read", "seed_refs must contain at most 50 refs")
        requested_refs = list(seed_refs or [])
        outline_offset = self._outline_offset(continuation)
        if continuation is not None and (focus is not None or requested_refs):
            raise HypesError(
                "invalid_read",
                "continuation can be used only for an outline read without focus or seed_refs",
            )

        with self.store.connect() as connection:
            # Pin every table read to one WAL snapshot so a concurrent rewrite cannot
            # produce a graph slice assembled from different committed versions.
            connection.execute("BEGIN")
            node_rows = self._rows_by_ref(connection, "nodes", "node_id")
            predicate_rows = self._rows_by_ref(connection, "predicates", "predicate_id")
            edge_rows = self._rows_by_ref(connection, "edges", "edge_id")

            seeds: list[tuple[str, str]] = []
            next_continuation: str | None = None
            seen_seed_refs: set[str] = set()
            for ref in requested_refs:
                kind = _ref_kind(ref)
                if kind not in {"node", "pred"}:
                    raise HypesError(
                        "invalid_read", "seed_refs accept only node and predicate refs"
                    )
                rows = node_rows if kind == "node" else predicate_rows
                if ref not in rows:
                    raise HypesError(
                        "object_not_found",
                        "a requested ontology seed does not exist",
                        details={"ref": ref},
                    )
                if ref not in seen_seed_refs:
                    seeds.append((kind, ref))
                    seen_seed_refs.add(ref)

            if focus is not None:
                remaining = max(0, limit - len(seeds))
                focus_capacity = min(_MAX_FOCUS_SEEDS, remaining)
                if max_hops > 0 and focus_capacity:
                    # A regular edge reached from an included node or predicate costs
                    # at most three additional objects: the edge and the two missing
                    # members of its reference closure. Explicit seeds keep priority;
                    # only focus-derived seeds yield this room.
                    focus_capacity = min(
                        focus_capacity,
                        max(0, remaining - _EDGE_EXPANSION_RESERVE),
                    )
                    if not seeds:
                        focus_capacity = max(1, focus_capacity)

                if focus_capacity:
                    # Query past explicit refs that may also match, then add only
                    # the bounded number of distinct focus seeds.
                    focus_candidates = self._focus_seeds(
                        connection,
                        focus,
                        limit=focus_capacity + len(seen_seed_refs),
                    )
                    added_focus_seeds = 0
                    for kind, ref in focus_candidates:
                        if ref not in seen_seed_refs:
                            seeds.append((kind, ref))
                            seen_seed_refs.add(ref)
                            added_focus_seeds += 1
                            if added_focus_seeds >= focus_capacity:
                                break
            elif not seeds:
                seeds, has_more_outline = self._outline_seeds(
                    connection,
                    limit=limit,
                    offset=outline_offset,
                )
                if has_more_outline:
                    next_continuation = f"outline-v1:{outline_offset + len(seeds)}"

            included_nodes: set[str] = set()
            included_predicates: set[str] = set()
            included_edges: set[str] = set()
            frontier_nodes: set[str] = set()
            frontier_predicates: set[str] = set()

            for kind, ref in seeds:
                if len(included_nodes) + len(included_predicates) >= limit:
                    break
                if kind == "node":
                    included_nodes.add(ref)
                    frontier_nodes.add(ref)
                else:
                    included_predicates.add(ref)
                    frontier_predicates.add(ref)

            if focus is None and not requested_refs:
                max_hops = 0

            expanded_nodes: set[str] = set()
            for _ in range(max_hops):
                candidate_edges = [
                    row
                    for row in edge_rows.values()
                    if row["edge_id"] not in included_edges
                    and (
                        row["source_id"] in frontier_nodes
                        or row["target_id"] in frontier_nodes
                        or row["predicate_id"] in frontier_predicates
                    )
                ]
                candidate_edges.sort(key=lambda row: row["edge_id"])
                next_nodes: set[str] = set()
                for row in candidate_edges:
                    missing_nodes = {
                        ref
                        for ref in (row["source_id"], row["target_id"])
                        if ref not in included_nodes
                    }
                    missing_predicates = (
                        {row["predicate_id"]}
                        if row["predicate_id"] not in included_predicates
                        else set()
                    )
                    cost = 1 + len(missing_nodes) + len(missing_predicates)
                    used = (
                        len(included_nodes)
                        + len(included_predicates)
                        + len(included_edges)
                    )
                    if used + cost > limit:
                        continue
                    included_edges.add(row["edge_id"])
                    included_nodes.update(missing_nodes)
                    included_predicates.update(missing_predicates)
                    next_nodes.update({row["source_id"], row["target_id"]})
                expanded_nodes.update(frontier_nodes)
                frontier_nodes = next_nodes - expanded_nodes
                frontier_predicates = set()
                if not frontier_nodes:
                    break

            return {
                "nodes": [
                    _node_value(node_rows[ref]) for ref in sorted(included_nodes)
                ],
                "predicates": [
                    _predicate_value(predicate_rows[ref])
                    for ref in sorted(included_predicates)
                ],
                "edges": [
                    _edge_value(edge_rows[ref]) for ref in sorted(included_edges)
                ],
                "continuation": next_continuation,
            }


__all__ = ["HypesService"]
