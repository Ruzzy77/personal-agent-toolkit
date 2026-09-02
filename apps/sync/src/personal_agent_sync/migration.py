"""One-time, resumable migration from the installed local products."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from collections import defaultdict
from collections.abc import Mapping
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import SyncConfig
from .errors import SyncError
from .remote import RemoteClient
from .state import SyncState, canonical


def _json(value: str | None, fallback: object) -> Any:
    if value is None:
        return fallback
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise SyncError(
            "invalid_local_state", "a local JSON record is invalid"
        ) from exc
    return parsed


def _iso(value: object | None) -> str | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise SyncError("invalid_local_state", "a local timestamp is invalid") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def export_sense() -> dict[str, Any]:
    data_root = Path.home() / "Library" / "Application Support" / "Sense"
    path = data_root / "sense.sqlite3"
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            row = connection.execute(
                "SELECT profile_json FROM current_profile WHERE singleton = 1"
            ).fetchone()
    except sqlite3.Error as exc:
        raise SyncError(
            "local_sense_unavailable", "the local Sense store is unavailable"
        ) from exc
    profile = _json(row[0], {}) if row else None
    if not isinstance(profile, dict) or not isinstance(profile.get("sections"), list):
        raise SyncError("invalid_local_state", "the local Sense profile is invalid")
    clean_sections: list[dict[str, Any]] = []
    skills: list[dict[str, Any]] = []
    store_updated_at = (
        datetime.fromtimestamp(path.stat().st_mtime, UTC)
        .isoformat()
        .replace("+00:00", "Z")
    )
    for raw in profile["sections"]:
        if not isinstance(raw, dict):
            raise SyncError("invalid_local_state", "a local Sense section is invalid")
        section = dict(raw)
        skill = section.pop("skill", None)
        skill_path = data_root / "sections" / str(section["id"]) / "skill" / "SKILL.md"
        if skill is None and skill_path.is_file():
            skill = _read_skill_file(skill_path)
        clean_sections.append(section)
        if isinstance(skill, dict):
            skills.append(
                {
                    "section_id": section["id"],
                    "name": skill["name"],
                    "description": skill["description"],
                    "instructions": skill["instructions"],
                    "updated_at": _iso(skill.get("updated_at")) or store_updated_at,
                }
            )
    return {
        "profile": {"schema_version": 2, "sections": clean_sections},
        "skills": skills,
    }


def _read_skill_file(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SyncError(
            "local_sense_unavailable", "a Sense Skill is unavailable"
        ) from exc
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        raise SyncError("invalid_local_state", "a Sense Skill file is invalid")
    front, instructions = text[4:].split("\n---\n", 1)
    fields: dict[str, str] = {}
    for line in front.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        value = value.strip()
        if value.startswith('"'):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as exc:
                raise SyncError(
                    "invalid_local_state", "a Sense Skill header is invalid"
                ) from exc
        fields[key.strip()] = value
    if (
        not fields.get("name")
        or not fields.get("description")
        or not instructions.strip()
    ):
        raise SyncError("invalid_local_state", "a Sense Skill file is incomplete")
    return {
        "name": fields["name"],
        "description": fields["description"],
        "instructions": instructions.strip(),
        "updated_at": datetime.fromtimestamp(path.stat().st_mtime, UTC)
        .isoformat()
        .replace("+00:00", "Z"),
    }


def export_hypes() -> dict[str, Any]:
    path = (
        Path.home()
        / "Library"
        / "Application Support"
        / "Hypes"
        / "hypes-ontology.sqlite3"
    )
    nodes: list[dict[str, Any]] = []
    predicates: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    try:
        connection_manager = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        connection_manager.row_factory = sqlite3.Row
    except sqlite3.Error as exc:
        raise SyncError(
            "local_hypes_unavailable", "the local Hypes store is unavailable"
        ) from exc
    with closing(connection_manager) as connection:
        for row in connection.execute("SELECT * FROM nodes ORDER BY node_id"):
            nodes.append(
                {
                    "node_id": row["node_id"],
                    "labels": _json(row["labels_json"], []),
                    "name": row["name"],
                    "description": row["description"] or None,
                    "aliases": _json(row["aliases_json"], []),
                    "attributes": _json(row["attributes_json"], {}),
                }
            )
        for row in connection.execute("SELECT * FROM predicates ORDER BY predicate_id"):
            predicates.append(
                {
                    "predicate_id": row["predicate_id"],
                    "name": row["name"],
                    "description": row["description"] or None,
                    "aliases": _json(row["aliases_json"], []),
                }
            )
        for row in connection.execute("SELECT * FROM edges ORDER BY edge_id"):
            edges.append(
                {
                    "edge_id": row["edge_id"],
                    "source_id": row["source_id"],
                    "predicate_id": row["predicate_id"],
                    "target_id": row["target_id"],
                    "qualifiers": _json(row["qualifiers_json"], {}),
                }
            )
    return {"nodes": nodes, "predicates": predicates, "edges": edges}


def _corpus_projection(corpus_python: Path, data_root: Path) -> dict[str, Any]:
    script = r"""
import json
import sys
from pathlib import Path
from corpus.service import CorpusService

service = CorpusService(Path(sys.argv[1]))
spaces = service.spaces._spaces(audience="external_mcp")
skills = {}
for space in spaces:
    context_id = space.get("_context_id")
    if isinstance(context_id, str):
        skill = service.context_skills.read(
            context_id=context_id,
            audience="external_mcp",
            include_instructions=True,
            require_context=False,
        )
        if skill is not None:
            skills[context_id] = skill
print(json.dumps({"spaces": spaces, "skills": skills}, ensure_ascii=False))
"""
    try:
        completed = subprocess.run(
            [str(corpus_python), "-c", script, str(data_root)],
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SyncError(
            "local_corpus_unavailable", "the Corpus runtime could not be started"
        ) from exc
    if completed.returncode != 0:
        raise SyncError(
            "local_corpus_unavailable",
            "the Corpus runtime could not project current Spaces",
        )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise SyncError(
            "local_corpus_unavailable", "the Corpus runtime returned invalid state"
        ) from exc
    if not isinstance(value, dict) or not isinstance(value.get("spaces"), list):
        raise SyncError("invalid_local_state", "the Corpus Space projection is invalid")
    return value


def write_discovered_config(
    *,
    output: Path,
    service_url: str,
    device_id: str,
    display_name: str,
    corpus_python: Path,
    document_files_python: Path,
    corpus_data_root: Path | None = None,
    replace: bool = False,
) -> dict[str, Any]:
    """Create the private Sync configuration from current remote-visible Spaces."""

    if output.exists() and not replace:
        raise SyncError("configuration_exists", "the Sync configuration already exists")
    data_root = corpus_data_root or (
        Path.home() / "Library" / "Application Support" / "Corpus"
    )
    projected = _corpus_projection(corpus_python, data_root)
    catalog = {
        row["corpus_id"]: dict(row)
        for row in LocalCorpusMigration._read_all(
            data_root / "catalog.sqlite", "SELECT * FROM corpora"
        )
    }
    workspaces = {
        row["workspace_id"]: dict(row)
        for row in LocalCorpusMigration._read_all(
            data_root / "workspaces.sqlite3", "SELECT * FROM workspaces"
        )
    }
    values: list[dict[str, Any]] = []
    for space in projected["spaces"]:
        for connection in space["connections"]:
            source_ids = list(connection.get("_source_ids", []))
            if len(source_ids) > 1:
                raise SyncError(
                    "unsupported_connection",
                    "one Sync Connection cannot currently bind multiple Source roots",
                )
            corpus_id = source_ids[0] if source_ids else None
            source = catalog.get(corpus_id) if corpus_id else None
            workspace_id = connection.get("_workspace_id")
            workspace = (
                workspaces.get(workspace_id) if isinstance(workspace_id, str) else None
            )
            roots = [
                Path(value)
                for value in (
                    source.get("source_root") if source else None,
                    workspace.get("root_path") if workspace else None,
                )
                if isinstance(value, str)
            ]
            if not roots:
                raise SyncError(
                    "invalid_local_state",
                    "a remote-visible Connection has no local root",
                )
            root = roots[0]
            identities = {(item.stat().st_dev, item.stat().st_ino) for item in roots}
            if len(identities) != 1:
                raise SyncError(
                    "connection_root_mismatch",
                    "a combined Source and Work Connection resolves to different roots",
                )
            scope = _json(source.get("source_scope_json"), {}) if source else {}
            values.append(
                {
                    "space_id": space["space_id"],
                    "connection_id": connection["connection_id"],
                    "root": str(root),
                    "roles": connection["roles"],
                    "access_scope": connection["access_scope"],
                    "permission": connection["permission"],
                    "corpus_id": corpus_id,
                    "generation": connection["generation"],
                    "exclude_directory_names": scope.get("exclude_directory_names", []),
                    "exclude_path_prefixes": scope.get("exclude_path_prefixes", []),
                }
            )
    sync_root = Path.home() / "Library" / "Application Support" / "Personal Agent Sync"
    lines = [
        f"service_url = {json.dumps(service_url, ensure_ascii=False)}",
        f"device_id = {json.dumps(device_id, ensure_ascii=False)}",
        f"display_name = {json.dumps(display_name, ensure_ascii=False)}",
        f"data_root = {json.dumps(str(sync_root), ensure_ascii=False)}",
        f"corpus_data_root = {json.dumps(str(data_root), ensure_ascii=False)}",
        f"corpus_python = {json.dumps(str(corpus_python), ensure_ascii=False)}",
        "document_files_python = "
        + json.dumps(str(document_files_python), ensure_ascii=False),
        "reconcile_seconds = 15",
        "",
    ]
    for value in values:
        lines.extend(
            [
                "[[connections]]",
                f"space_id = {json.dumps(value['space_id'], ensure_ascii=False)}",
                f"connection_id = {json.dumps(value['connection_id'], ensure_ascii=False)}",
                f"root = {json.dumps(value['root'], ensure_ascii=False)}",
                "roles = " + json.dumps(value["roles"], ensure_ascii=False),
                f"access_scope = {json.dumps(value['access_scope'])}",
                f"permission = {json.dumps(value['permission'])}",
                *(
                    [f"corpus_id = {json.dumps(value['corpus_id'])}"]
                    if value["corpus_id"] is not None
                    else []
                ),
                'analyzer_route = "local"',
                "max_transfer_bytes = 1073741824",
                f"generation = {value['generation']}",
                "include_hidden = true",
                "exclude_directory_names = "
                + json.dumps(value["exclude_directory_names"], ensure_ascii=False),
                "exclude_path_prefixes = "
                + json.dumps(value["exclude_path_prefixes"], ensure_ascii=False),
                "",
            ]
        )
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = output.with_suffix(f"{output.suffix}.tmp")
    temporary.write_text("\n".join(lines), encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(output)
    return {"created": True, "connection_count": len(values), "path": str(output)}


class LocalCorpusMigration:
    """Read immutable durable records without copying original document bytes."""

    def __init__(self, config: SyncConfig) -> None:
        if config.corpus_data_root is None:
            raise SyncError(
                "local_corpus_unavailable", "Corpus local data root is not configured"
            )
        self.config = config
        self.data_root = Path(config.corpus_data_root)
        if config.corpus_python is None:
            raise SyncError(
                "local_corpus_unavailable", "Corpus runtime is not configured"
            )
        projected = _corpus_projection(config.corpus_python, self.data_root)
        self._spaces = projected["spaces"]
        self._skills = projected.get("skills", {})
        self._connections = {
            connection.key: connection for connection in config.connections
        }
        self._catalog = {
            row["corpus_id"]: dict(row)
            for row in self._read_all(
                self.data_root / "catalog.sqlite", "SELECT * FROM corpora"
            )
        }

    @staticmethod
    def _connect(path: Path) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
        except sqlite3.Error as exc:
            raise SyncError(
                "local_corpus_unavailable", "a Corpus database is unavailable"
            ) from exc
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @classmethod
    def _read_all(cls, path: Path, query: str, values: tuple[object, ...] = ()):
        with closing(cls._connect(path)) as connection:
            return connection.execute(query, values).fetchall()

    def corpus_path(self, corpus_id: str) -> Path:
        path = self.data_root / "corpora" / corpus_id / "corpus.sqlite"
        if corpus_id not in self._catalog or not path.is_file():
            raise SyncError(
                "local_corpus_unavailable", "a configured Corpus Source is unavailable"
            )
        return path

    def corpus_ids(self) -> list[str]:
        return sorted(
            {
                connection.corpus_id
                for connection in self.config.connections
                if "source" in connection.roles
                and connection.access_scope == "remote_allowed"
                and connection.corpus_id is not None
            }
        )

    def _context_payload(self, space: Mapping[str, Any]) -> dict[str, Any] | None:
        context_id = space.get("_context_id")
        if not isinstance(context_id, str):
            return None
        path = self.data_root / "contexts.sqlite3"
        with closing(self._connect(path)) as connection:
            context = connection.execute(
                "SELECT * FROM contexts WHERE context_id = ?", (context_id,)
            ).fetchone()
            if context is None:
                raise SyncError(
                    "invalid_local_state", "a Space Context binding is missing"
                )
            items = connection.execute(
                "SELECT * FROM context_items WHERE context_id = ? ORDER BY created_at, item_id",
                (context_id,),
            ).fetchall()
            item_ids = {row["item_id"] for row in items}
            file_sources = connection.execute(
                "SELECT * FROM context_sources WHERE item_id IN "
                "(SELECT item_id FROM context_items WHERE context_id = ?) ORDER BY source_ref_id",
                (context_id,),
            ).fetchall()
            external_sources = connection.execute(
                """
                SELECT x.*, b.provider_kind, r.external_id, r.last_seen_run_id
                FROM context_external_sources x
                JOIN corpus_source_bindings b ON b.binding_id = x.binding_id
                JOIN external_source_records r ON r.source_record_id = x.source_record_id
                WHERE x.item_id IN (
                    SELECT item_id FROM context_items WHERE context_id = ?
                ) ORDER BY x.source_ref_id
                """,
                (context_id,),
            ).fetchall()
        sources = [
            {
                "sourceRefId": row["source_ref_id"],
                "itemId": row["item_id"],
                "corpusId": row["corpus_id"],
                "snapshotId": None,
                "documentId": row["document_id"],
                "revisionId": row["revision_id"],
                "projectionId": row["projection_id"],
                "sourceUnitId": row["source_unit_id"],
                "providerKind": None,
                "providerRecordId": None,
                "linkRole": row["link_role"],
                "sourceSpan": _json(row["source_span_json"], {}),
            }
            for row in file_sources
            if row["item_id"] in item_ids
        ]
        sources.extend(
            {
                "sourceRefId": row["source_ref_id"],
                "itemId": row["item_id"],
                "corpusId": row["corpus_id"],
                "snapshotId": row["last_seen_run_id"],
                "documentId": None,
                "revisionId": None,
                "projectionId": None,
                "sourceUnitId": None,
                "providerKind": row["provider_kind"],
                "providerRecordId": row["source_record_id"],
                "linkRole": row["link_role"],
                "sourceSpan": {
                    "external_id": row["external_id"],
                    "observed_metadata_sha256": row["observed_metadata_sha256"],
                },
            }
            for row in external_sources
            if row["item_id"] in item_ids
        )
        skill = self._skills.get(context_id)
        return {
            "spaceId": space["space_id"],
            "title": context["title"],
            "purpose": context["purpose"],
            "scope": _json(context["scope_json"], {}),
            "version": context["version"],
            "updatedAt": _iso(context["updated_at"]),
            "items": [
                {
                    "itemId": row["item_id"],
                    "kind": row["kind"],
                    "bodyText": row["body_text"],
                    "attributes": _json(row["attributes_json"], {}),
                    "disclosureState": row["disclosure_state"],
                    "lifecycleState": row["lifecycle_state"],
                    "supersedesItemId": row["supersedes_item_id"],
                    "createdAt": _iso(row["created_at"]),
                }
                for row in items
            ],
            "sources": sources,
            "skill": (
                {
                    "name": skill["name"],
                    "description": skill["description"],
                    "instructions": skill["instructions"],
                    "version": skill["version"],
                    "updatedAt": _iso(skill["updated_at"]),
                }
                if isinstance(skill, dict)
                else None
            ),
        }

    def metadata_payload(self) -> dict[str, Any]:
        spaces: list[dict[str, Any]] = []
        contexts: list[dict[str, Any]] = []
        connections: list[dict[str, Any]] = []
        current_files: list[dict[str, Any]] = []
        for space in self._spaces:
            if space.get("access_scope") != "remote_allowed":
                continue
            context = self._context_payload(space)
            if context is not None:
                contexts.append(context)
            scan_times = [
                timestamp
                for raw_connection in space.get("connections", [])
                for corpus_id in raw_connection.get("_source_ids", [])
                if (timestamp := self.latest_scan_at(corpus_id)) is not None
            ]
            updated_at = (
                context["updatedAt"]
                if context is not None
                else max(scan_times, default="1970-01-01T00:00:00Z")
            )
            spaces.append(
                {
                    "spaceId": space["space_id"],
                    "displayName": space["display_name"],
                    "state": space["state"],
                    "accessScope": space["access_scope"],
                    "primaryWorkConnectionId": space.get("primary_work_connection_id"),
                    "updatedAt": updated_at,
                }
            )
            for raw_connection in space["connections"]:
                key = f"{space['space_id']}:{raw_connection['connection_id']}"
                local = self._connections.get(key)
                if local is None:
                    raise SyncError(
                        "invalid_configuration",
                        "Sync configuration is missing a remote-visible Corpus Connection",
                    )
                connections.append(
                    {
                        "spaceId": space["space_id"],
                        "connectionId": raw_connection["connection_id"],
                        "displayName": raw_connection["display_name"],
                        "roles": raw_connection["roles"],
                        "accessScope": raw_connection["access_scope"],
                        "permission": raw_connection["permission"],
                        "indexMode": raw_connection["index_mode"],
                        "corpusId": local.corpus_id,
                        "deviceId": self.config.device_id,
                        "localConnectionKey": key,
                        "generation": raw_connection["generation"],
                        "configurationState": raw_connection["configuration_state"],
                        "sourceState": raw_connection.get("source_state"),
                        "recordState": raw_connection.get("record_state"),
                        "capturedAt": self.latest_scan_at(local.corpus_id),
                        "updatedAt": updated_at,
                    }
                )
                current = raw_connection.get("current_file")
                if isinstance(current, dict):
                    current_files.append(
                        {
                            "spaceId": space["space_id"],
                            "connectionId": raw_connection["connection_id"],
                            "relativePath": current["relative_path"],
                            "versionToken": None,
                            "state": current["state"],
                            "reason": current.get("reason"),
                            "residencyState": current.get("residency_state"),
                            "size": current.get("size"),
                            "modifiedNs": str(current["modified_ns"])
                            if current.get("modified_ns") is not None
                            else None,
                            "updatedAt": updated_at,
                        }
                    )
        stable_times = [
            value
            for value in [
                *(space["updatedAt"] for space in spaces),
                *(connection["updatedAt"] for connection in connections),
                *(current["updatedAt"] for current in current_files),
            ]
            if isinstance(value, str)
        ]
        device_updated_at = max(stable_times, default="1970-01-01T00:00:00Z")
        payload: dict[str, Any] = {
            "schemaVersion": 1,
            "sourceSchemaVersion": 6,
            "spaces": spaces,
            "contexts": contexts,
            "connections": connections,
            "currentFiles": current_files,
            "devices": [
                {
                    "deviceId": self.config.device_id,
                    "displayName": self.config.display_name,
                    "status": "active",
                    "capabilities": [
                        "source.reconcile",
                        "work.file.list",
                        "work.file.read",
                        "work.file.write",
                        "work.file.delete",
                        "work.file.select_current",
                        "work.file.restore",
                        "document.analyze.local",
                        "document.analyze.remote",
                    ],
                    "createdAt": device_updated_at,
                    "updatedAt": device_updated_at,
                }
            ],
        }
        payload["sourceDigest"] = _digest(payload)
        return payload

    def latest_scan_at(self, corpus_id: str | None) -> str | None:
        if corpus_id is None:
            return None
        with closing(self._connect(self.corpus_path(corpus_id))) as connection:
            row = connection.execute(
                "SELECT completed_at FROM scan_runs WHERE status = 'complete' "
                "ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
        return _iso(row["completed_at"]) if row and row["completed_at"] else None

    @staticmethod
    def _source_state(row: Mapping[str, Any]) -> str:
        if row["deleted_at"] is not None:
            return "unavailable"
        if row["residency_state"] != "resident":
            return "partially_available"
        if row["current_revision_id"] is not None and (
            row["revision_id"] is None
            or row["source_size"] != row["logical_size"]
            or row["source_modified_ns"] != row["modified_ns"]
            or row["source_changed_ns"] != row["changed_ns"]
            or row["source_inode"] != row["inode"]
        ):
            return "changed"
        return "available"

    def documents(self, corpus_id: str) -> list[dict[str, Any]]:
        with closing(self._connect(self.corpus_path(corpus_id))) as connection:
            rows = connection.execute(
                """
                SELECT d.*, r.revision_id, r.sha256, r.source_size,
                       r.source_modified_ns, r.source_changed_ns, r.source_inode,
                       p.projection_id AS active_projection_id
                FROM documents d
                LEFT JOIN revisions r ON r.revision_id = d.current_revision_id
                LEFT JOIN extraction_projections p
                  ON p.revision_id = d.current_revision_id AND p.is_active = 1
                ORDER BY d.relative_path_nfc, d.document_id
                """
            ).fetchall()
        return [
            {
                "documentId": row["document_id"],
                "relativePath": row["relative_path_nfc"],
                "extension": row["extension"],
                "sourceState": self._source_state(row),
                "mediaType": row["media_type"],
                "logicalSize": row["logical_size"],
                "modifiedNs": str(row["modified_ns"]),
                "residencyState": row["residency_state"],
                "eligibilityState": row["eligibility_state"],
                "currentRevisionId": row["current_revision_id"],
                "lifecycleState": row["lifecycle_state"],
                "retentionClass": row["retention_class"],
                "lastUserAccessAt": _iso(row["last_user_access_at"]),
                "firstSeenAt": _iso(row["first_seen_at"]),
                "lastSeenAt": _iso(row["last_seen_at"]),
                "deletedAt": _iso(row["deleted_at"]),
            }
            for row in rows
        ]

    def external_state(self, corpus_id: str) -> dict[str, Any]:
        path = self.data_root / "contexts.sqlite3"
        with closing(self._connect(path)) as connection:
            bindings = connection.execute(
                "SELECT * FROM corpus_source_bindings WHERE corpus_id = ? "
                "ORDER BY binding_id",
                (corpus_id,),
            ).fetchall()
            binding_ids = [row["binding_id"] for row in bindings]
            if not binding_ids:
                return {
                    "corpusId": corpus_id,
                    "bindings": [],
                    "runs": [],
                    "records": [],
                }
            placeholders = ",".join("?" for _ in binding_ids)
            runs = connection.execute(
                f"SELECT * FROM external_source_runs WHERE binding_id IN ({placeholders}) "
                "ORDER BY started_at, run_id",
                binding_ids,
            ).fetchall()
            records = connection.execute(
                f"SELECT * FROM external_source_records WHERE binding_id IN ({placeholders}) "
                "ORDER BY binding_id, external_id, source_record_id",
                binding_ids,
            ).fetchall()
        return {
            "corpusId": corpus_id,
            "bindings": [
                {
                    "bindingId": row["binding_id"],
                    "providerKind": row["provider_kind"],
                    "selector": _json(row["selector_json"], {}),
                    "state": row["state"],
                    "lastCompleteRunId": row["last_complete_run_id"],
                    "lastCompleteAt": _iso(row["last_complete_at"]),
                    "createdAt": _iso(row["created_at"]),
                    "updatedAt": _iso(row["updated_at"]),
                }
                for row in bindings
            ],
            "runs": [
                {
                    "runId": row["run_id"],
                    "bindingId": row["binding_id"],
                    "baseCompleteRunId": row["base_complete_run_id"],
                    "status": row["status"],
                    "startedAt": _iso(row["started_at"]),
                    "completedAt": _iso(row["completed_at"]),
                    "supersededAt": _iso(row["superseded_at"]),
                }
                for row in runs
            ],
            "records": [
                {
                    "sourceRecordId": row["source_record_id"],
                    "bindingId": row["binding_id"],
                    "externalId": row["external_id"],
                    "parentExternalId": row["parent_external_id"],
                    "occurredAt": _iso(row["occurred_at"]),
                    "title": row["title"],
                    "participants": _json(row["participants_json"], []),
                    "labelIds": _json(row["label_ids_json"], []),
                    "attachments": _json(row["attachments_json"], []),
                    "providerMetadata": _json(row["provider_metadata_json"], {}),
                    "locator": _json(row["locator_json"], {}),
                    "freshnessIdentity": row["freshness_identity"],
                    "metadataSha256": row["metadata_sha256"],
                    "membershipState": row["membership_state"],
                    "lastSeenRunId": row["last_seen_run_id"],
                    "firstSeenAt": _iso(row["first_seen_at"]),
                    "lastSeenAt": _iso(row["last_seen_at"]),
                }
                for row in records
            ],
        }

    def seed_documents(self, state: SyncState, corpus_id: str) -> dict[str, int]:
        with closing(self._connect(self.corpus_path(corpus_id))) as connection:
            rows = connection.execute(
                """
                SELECT d.*, r.sha256, r.source_size, r.source_modified_ns,
                       r.source_changed_ns, r.source_inode,
                       p.projection_id AS active_projection_id
                FROM documents d
                LEFT JOIN revisions r ON r.revision_id = d.current_revision_id
                LEFT JOIN extraction_projections p
                  ON p.revision_id = d.current_revision_id AND p.is_active = 1
                WHERE d.deleted_at IS NULL AND d.lifecycle_state = 'active'
                ORDER BY d.relative_path_nfc, d.document_id
                """
            ).fetchall()
        values = []
        for row in rows:
            changed = row["current_revision_id"] is not None and (
                row["sha256"] is None
                or row["source_size"] != row["logical_size"]
                or row["source_modified_ns"] != row["modified_ns"]
                or row["source_changed_ns"] != row["changed_ns"]
                or row["source_inode"] != row["inode"]
            )
            values.append(
                {
                    "document_id": row["document_id"],
                    "relative_path": row["relative_path"],
                    "relative_path_nfc": row["relative_path_nfc"],
                    "device": row["device"],
                    "inode": row["inode"],
                    "size": row["logical_size"],
                    "modified_ns": row["modified_ns"],
                    "changed_ns": row["changed_ns"],
                    "last_revision_sha256": row["sha256"],
                    "last_projection_id": row["active_projection_id"],
                    "needs_refresh": row["eligibility_state"] == "supported"
                    and (
                        row["sha256"] is None
                        or row["active_projection_id"] is None
                        or changed
                    ),
                }
            )
        totals = {"seeded": 0, "queued": 0}
        for connection in self.config.source_watchers:
            if connection.corpus_id != corpus_id or "source" not in connection.roles:
                continue
            result = state.seed_documents(connection.key, values)
            totals["seeded"] += result["seeded"]
            totals["queued"] += result["queued"]
        return totals

    def projection_headers(self, corpus_id: str) -> list[dict[str, Any]]:
        with closing(self._connect(self.corpus_path(corpus_id))) as connection:
            rows = connection.execute(
                """
                SELECT p.*, r.document_id, r.sha256, r.source_size, r.captured_at,
                       r.predecessor_revision_id, d.relative_path_nfc, d.extension,
                       d.media_type, d.logical_size, d.modified_ns, d.residency_state,
                       d.eligibility_state, d.current_revision_id, d.deleted_at,
                       d.lifecycle_state, d.retention_class, d.last_user_access_at,
                       cr.revision_id AS observed_revision_id,
                       cr.source_size AS observed_source_size,
                       cr.source_modified_ns AS observed_source_modified_ns,
                       cr.source_changed_ns AS observed_source_changed_ns,
                       cr.source_inode AS observed_source_inode,
                       d.changed_ns, d.inode,
                       (SELECT COUNT(*) FROM source_units u
                        WHERE u.projection_id = p.projection_id) AS unit_count
                FROM extraction_projections p
                JOIN revisions r ON r.revision_id = p.revision_id
                JOIN documents d ON d.document_id = r.document_id
                LEFT JOIN revisions cr ON cr.revision_id = d.current_revision_id
                ORDER BY r.captured_at, p.is_active, p.created_at, p.projection_id
                """
            ).fetchall()
            results = []
            for row in rows:
                issues = [
                    {
                        "stage": issue["stage"],
                        "severity": issue["severity"],
                        "code": issue["code"],
                        "message": issue["message"],
                        "details": _json(issue["details_json"], {}),
                        "structural_locator": _json(
                            issue["structural_locator_json"], {}
                        ),
                        "lifecycle_state": issue["lifecycle_state"],
                        "created_at": _iso(issue["created_at"]),
                    }
                    for issue in connection.execute(
                        "SELECT * FROM extraction_issues WHERE projection_id = ? "
                        "ORDER BY created_at, issue_id",
                        (row["projection_id"],),
                    )
                ]
                header = {
                    "uploadId": "upload_"
                    + hashlib.sha256(
                        f"{corpus_id}:{row['projection_id']}".encode()
                    ).hexdigest()[:32],
                    "corpusId": corpus_id,
                    "document": {
                        "documentId": row["document_id"],
                        "relativePath": row["relative_path_nfc"],
                        "extension": row["extension"],
                        "sourceState": (
                            "unavailable"
                            if row["deleted_at"]
                            else "partially_available"
                            if row["residency_state"] != "resident"
                            else "changed"
                            if row["current_revision_id"] is not None
                            and (
                                row["observed_revision_id"] is None
                                or row["observed_source_size"] != row["logical_size"]
                                or row["observed_source_modified_ns"]
                                != row["modified_ns"]
                                or row["observed_source_changed_ns"]
                                != row["changed_ns"]
                                or row["observed_source_inode"] != row["inode"]
                            )
                            else "available"
                        ),
                        "mediaType": row["media_type"],
                        "logicalSize": row["logical_size"],
                        "modifiedNs": str(row["modified_ns"]),
                        "residencyState": row["residency_state"],
                        "eligibilityState": row["eligibility_state"],
                        "lifecycleState": row["lifecycle_state"],
                        "retentionClass": row["retention_class"],
                        "lastUserAccessAt": _iso(row["last_user_access_at"]),
                        "deletedAt": _iso(row["deleted_at"]),
                    },
                    "revision": {
                        "revisionId": row["revision_id"],
                        "sha256": row["sha256"],
                        "sourceSize": row["source_size"],
                        "capturedAt": _iso(row["captured_at"]),
                        "predecessorRevisionId": row["predecessor_revision_id"],
                        "makeCurrent": row["current_revision_id"] == row["revision_id"],
                    },
                    "projection": {
                        "projectionId": row["projection_id"],
                        "adapterId": row["adapter_id"],
                        "adapterVersion": row["adapter_version"],
                        "configHash": row["config_hash"],
                        "resultManifestHash": row["result_manifest_hash"],
                        "completenessState": row["completeness_state"],
                        "coverage": _json(row["coverage_json"], {}),
                        "capabilityManifest": _json(
                            row["capability_manifest_json"], {}
                        ),
                        "issues": issues,
                        "assuranceState": row["assurance_state"],
                        "declaredUnitCount": row["unit_count"],
                        "activate": bool(row["is_active"]),
                        "createdAt": _iso(row["created_at"]),
                    },
                }
                results.append(header)
        return results

    def projection_units(
        self, corpus_id: str, projection_id: str
    ) -> list[dict[str, Any]]:
        with closing(self._connect(self.corpus_path(corpus_id))) as connection:
            rows = connection.execute(
                "SELECT * FROM source_units WHERE projection_id = ? ORDER BY ordinal",
                (projection_id,),
            ).fetchall()
        return [
            {
                "unitId": row["unit_id"],
                "ordinal": row["ordinal"],
                "unitType": row["unit_type"],
                "structurePath": _json(row["structure_path_json"], {}),
                "sourceAnchor": _json(row["source_anchor_json"], {}),
                "content": row["normalized_content"],
                "contentSha256": row["content_sha256"],
                "previousUnitId": row["previous_unit_id"],
                "nextUnitId": row["next_unit_id"],
                "extractionIssues": _json(row["extraction_issues_json"], []),
                "derivationMethod": row["derivation_method"],
                "geometry": _json(row["geometry_json"], {}),
                "confidence": row["confidence"],
                "ocr": str(row["derivation_method"]).startswith("ocr"),
                "qualityFlags": _json(row["quality_flags_json"], []),
            }
            for row in rows
        ]

    def counts(self, corpus_id: str) -> dict[str, int]:
        with closing(self._connect(self.corpus_path(corpus_id))) as connection:
            return {
                name: int(
                    connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                )
                for name, table in (
                    ("documents", "documents"),
                    ("revisions", "revisions"),
                    ("projections", "extraction_projections"),
                    ("units", "source_units"),
                )
            }


async def migrate_local(config: SyncConfig, token: str) -> dict[str, Any]:
    """Migrate current stores and resume at projection boundaries after interruption."""

    state = SyncState(config)
    remote = RemoteClient(config, token)
    summary: dict[str, Any] = {}
    try:
        for product, payload in (("sense", export_sense()), ("hypes", export_hypes())):
            source_digest = _digest(payload)
            cached = state.migration_result(product, "complete", source_digest)
            if cached is None:
                cached = await remote.import_payload(product, payload)
                state.remember_migration(product, "complete", source_digest, cached)
            summary[product] = cached

        corpus = LocalCorpusMigration(config)
        metadata = corpus.metadata_payload()
        metadata_digest = str(metadata["sourceDigest"])
        metadata_result = state.migration_result(
            "corpus-metadata", "complete", metadata_digest
        )
        if metadata_result is None:
            metadata_result = await remote.import_payload("corpus-metadata", metadata)
            state.remember_migration(
                "corpus-metadata", "complete", metadata_digest, metadata_result
            )
        summary["corpus_metadata"] = metadata_result

        corpus_summaries: dict[str, Any] = {}
        for corpus_id in corpus.corpus_ids():
            documents = corpus.documents(corpus_id)
            document_digest = _digest(documents)
            document_result = state.migration_result(
                "corpus-documents", corpus_id, document_digest
            )
            if document_result is None:
                document_result = await remote.import_documents(corpus_id, documents)
                state.remember_migration(
                    "corpus-documents", corpus_id, document_digest, document_result
                )
            external = corpus.external_state(corpus_id)
            external_digest = _digest(external)
            external_result = state.migration_result(
                "corpus-external", corpus_id, external_digest
            )
            if external_result is None:
                external_result = await remote.import_external(corpus_id, external)
                state.remember_migration(
                    "corpus-external", corpus_id, external_digest, external_result
                )
            seeded = corpus.seed_documents(state, corpus_id)
            migrated = 0
            skipped = 0
            headers = corpus.projection_headers(corpus_id)
            for index, header in enumerate(headers, start=1):
                projection_id = str(header["projection"]["projectionId"])
                projection_digest = _digest(header)
                cached = state.migration_result(
                    "corpus-projection",
                    f"{corpus_id}:{projection_id}",
                    projection_digest,
                )
                if cached is not None:
                    skipped += 1
                    continue
                print(
                    f"migrating {corpus_id} projection {index}/{len(headers)}",
                    file=sys.stderr,
                    flush=True,
                )
                units = corpus.projection_units(corpus_id, projection_id)
                if len(units) != header["projection"]["declaredUnitCount"]:
                    raise SyncError(
                        "local_projection_changed",
                        "a Corpus projection changed while it was being migrated",
                    )
                result = await remote.upload_projection(corpus_id, header, units)
                state.remember_migration(
                    "corpus-projection",
                    f"{corpus_id}:{projection_id}",
                    projection_digest,
                    result,
                )
                migrated += 1
            # Projection commits intentionally do not own Finder observation metadata.
            # Reapplying the document inventory also repairs stores written by an
            # earlier service version that updated last_seen_at during projection import.
            document_result = await remote.import_documents(corpus_id, documents)
            corpus_summaries[corpus_id] = {
                "documents": document_result,
                "external": external_result,
                "seed": seeded,
                "projection_count": len(headers),
                "migrated_projections": migrated,
                "resumed_projections": skipped,
            }
        summary["corpora"] = corpus_summaries
        return summary
    finally:
        await remote.close()


def _verification_error(message: str) -> SyncError:
    return SyncError("migration_verification_failed", message)


async def verify_local(config: SyncConfig, token: str) -> dict[str, Any]:
    """Compare the local durable records with the deployed remote state."""

    remote = RemoteClient(config, token)
    try:
        sense = export_sense()
        hypes = export_hypes()
        corpus = LocalCorpusMigration(config)
        metadata = corpus.metadata_payload()
        summary = await remote.verification_summary()
        remote_sense = summary.get("sense")
        remote_hypes = summary.get("hypes")
        remote_metadata = summary.get("corpus_metadata")
        if not isinstance(remote_sense, dict) or remote_sense.get(
            "profile_sha256"
        ) != _digest(sense["profile"]):
            raise _verification_error("Sense profile digest does not match")
        if remote_sense.get("skill_count") != len(sense["skills"]):
            raise _verification_error("Sense Skill count does not match")
        if remote_sense.get("skills_sha256") != _digest(sense["skills"]):
            raise _verification_error("Sense Skill digest does not match")
        if not isinstance(remote_hypes, dict) or (
            remote_hypes.get("node_count"),
            remote_hypes.get("predicate_count"),
            remote_hypes.get("edge_count"),
        ) != (len(hypes["nodes"]), len(hypes["predicates"]), len(hypes["edges"])):
            raise _verification_error("Hypes graph counts do not match")
        if remote_hypes.get("graph_sha256") != _digest(hypes):
            raise _verification_error("Hypes graph digest does not match")
        if (
            not isinstance(remote_metadata, dict)
            or remote_metadata.get("source_digest") != metadata["sourceDigest"]
        ):
            raise _verification_error("Corpus metadata digest does not match")

        verified: dict[str, Any] = {}
        for corpus_id in corpus.corpus_ids():
            expected_documents = {
                document["documentId"]: {
                    "document_id": document["documentId"],
                    "relative_path": document["relativePath"],
                    "extension": document["extension"],
                    "source_state": document["sourceState"],
                    "media_type": document["mediaType"],
                    "logical_size": document["logicalSize"],
                    "modified_ns": document["modifiedNs"],
                    "residency_state": document["residencyState"],
                    "eligibility_state": document["eligibilityState"],
                    "current_revision_id": document["currentRevisionId"],
                    "first_seen_at": document["firstSeenAt"],
                    "last_seen_at": document["lastSeenAt"],
                    "deleted_at": document["deletedAt"],
                    "lifecycle_state": document["lifecycleState"],
                    "retention_class": document["retentionClass"],
                    "last_user_access_at": document["lastUserAccessAt"],
                }
                for document in corpus.documents(corpus_id)
            }
            headers = corpus.projection_headers(corpus_id)
            expected_projections = {
                header["projection"]["projectionId"]: {
                    "projection_id": header["projection"]["projectionId"],
                    "revision_id": header["revision"]["revisionId"],
                    "document_id": header["document"]["documentId"],
                    "sha256": header["revision"]["sha256"],
                    "source_size": header["revision"]["sourceSize"],
                    "captured_at": header["revision"]["capturedAt"],
                    "predecessor_revision_id": header["revision"][
                        "predecessorRevisionId"
                    ],
                    "adapter_id": header["projection"]["adapterId"],
                    "adapter_version": header["projection"]["adapterVersion"],
                    "config_hash": header["projection"]["configHash"],
                    "result_manifest_hash": header["projection"]["resultManifestHash"],
                    "completeness_state": header["projection"]["completenessState"],
                    "assurance_state": header["projection"]["assuranceState"],
                    "coverage_json": canonical(header["projection"]["coverage"]),
                    "capability_manifest_json": canonical(
                        header["projection"]["capabilityManifest"]
                    ),
                    "issues_json": canonical(header["projection"]["issues"]),
                    "is_active": int(header["projection"]["activate"]),
                    "created_at": header["projection"]["createdAt"],
                    "is_current_revision": int(header["revision"]["makeCurrent"]),
                    "unit_count": header["projection"]["declaredUnitCount"],
                }
                for header in headers
            }
            expected_counts = corpus.counts(corpus_id)
            external = corpus.external_state(corpus_id)
            expected_external = {
                "binding_count": len(external["bindings"]),
                "run_count": len(external["runs"]),
                "record_count": len(external["records"]),
            }
            document_offset = 0
            projection_offset = 0
            document_done = False
            projection_done = False
            observed_counts: dict[str, Any] | None = None
            while not (document_done and projection_done):
                inventory = await remote.inventory(
                    corpus_id,
                    document_offset=document_offset,
                    projection_offset=projection_offset,
                )
                counts = inventory.get("counts")
                if counts != expected_counts:
                    raise _verification_error(
                        f"{corpus_id} durable counts do not match"
                    )
                observed_counts = counts
                if inventory.get("external") != expected_external:
                    raise _verification_error(
                        f"{corpus_id} external Source counts do not match"
                    )
                if inventory.get("staged_upload_count") != 0:
                    raise _verification_error(f"{corpus_id} has an abandoned upload")
                remote_documents = inventory.get("documents")
                remote_projections = inventory.get("projections")
                if not isinstance(remote_documents, list) or not isinstance(
                    remote_projections, list
                ):
                    raise _verification_error(f"{corpus_id} inventory is invalid")
                for actual in remote_documents:
                    if not isinstance(actual, dict):
                        raise _verification_error(
                            f"{corpus_id} document row is invalid"
                        )
                    document_id = actual.get("document_id")
                    expected = expected_documents.pop(document_id, None)
                    if expected is None or any(
                        actual.get(key) != value for key, value in expected.items()
                    ):
                        raise _verification_error(
                            f"{corpus_id} document {document_id} does not match"
                        )
                for actual in remote_projections:
                    if not isinstance(actual, dict):
                        raise _verification_error(
                            f"{corpus_id} projection row is invalid"
                        )
                    projection_id = actual.get("projection_id")
                    expected = expected_projections.pop(projection_id, None)
                    if expected is None or any(
                        actual.get(key) != value for key, value in expected.items()
                    ):
                        raise _verification_error(
                            f"{corpus_id} projection {projection_id} does not match"
                        )
                document_offset += len(remote_documents)
                projection_offset += len(remote_projections)
                document_done = inventory.get("document_has_more") is False
                projection_done = inventory.get("projection_has_more") is False
            if expected_documents or expected_projections or observed_counts is None:
                raise _verification_error(f"{corpus_id} inventory is incomplete")
            verified[corpus_id] = {
                "counts": observed_counts,
                "external": expected_external,
            }
        return {
            "verified": True,
            "sense_sections": len(sense["profile"]["sections"]),
            "sense_skills": len(sense["skills"]),
            "hypes": {
                "nodes": len(hypes["nodes"]),
                "predicates": len(hypes["predicates"]),
                "edges": len(hypes["edges"]),
            },
            "corpora": verified,
        }
    finally:
        await remote.close()


def configured_corpus_roots(config: SyncConfig) -> dict[str, list[str]]:
    result: defaultdict[str, list[str]] = defaultdict(list)
    for connection in config.connections:
        if connection.corpus_id is not None:
            result[connection.corpus_id].append(connection.key)
    return dict(result)


__all__ = [
    "LocalCorpusMigration",
    "configured_corpus_roots",
    "export_hypes",
    "export_sense",
    "migrate_local",
    "verify_local",
    "write_discovered_config",
]
