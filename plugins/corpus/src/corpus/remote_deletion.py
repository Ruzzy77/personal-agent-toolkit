"""Exact, ticket-bound deletion of Corpus-managed remote state.

The source root registered with a corpus is deliberately outside ``data_root``.
This module removes only private state owned by Corpus: named contexts, linked
source bindings, registrations, indexes, staging data, and retained capture
copies.  It never removes or edits the registered source root.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import time
import uuid
from collections.abc import Callable
from contextlib import closing, nullcontext
from pathlib import Path
from typing import Any, Literal

from mcp.server.request_state import RequestStateCodec

from .config import (
    RuntimePaths,
    normalize_corpus_id,
    open_private_file_at,
    private_directory,
)
from .contexts import normalize_context_id, normalize_source_binding_id
from .database import (
    connect,
    context_connection,
    context_read_connection,
    encode_json,
    get_corpus,
    utc_now,
)
from .errors import (
    ContextConflictError,
    ContextNotFoundError,
    CorpusError,
    CorpusNotFoundError,
)
from .locking import context_writer_lock, writer_lock
from .remote_deletion_state import (
    REMOTE_DELETE_INTENT_FORMAT,
    RemoteDeletionRecoveryRequiredError,
    read_remote_delete_intent,
    remove_remote_delete_intent,
    require_no_remote_delete_intent,
    write_remote_delete_intent,
)
from .remote_source_sync import (
    next_source_sync_deletion_epoch,
    persist_source_sync_deletion_epoch,
    remote_source_sync_deletion_lock,
    source_sync_delete_blocker,
)
from .service import CorpusService

DeleteTargetKind = Literal["context", "source_binding", "corpus"]


class RemoteDeleteBlockedError(CorpusError):
    code = "remote_delete_blocked"


class InvalidRemoteDeleteTicketError(CorpusError):
    code = "invalid_remote_delete_ticket"

    def __init__(self, message: str = "the Corpus deletion ticket is invalid or expired") -> None:
        super().__init__(message)


def _digest(value: object) -> str:
    return hashlib.sha256(encode_json(value).encode()).hexdigest()


def _rows(connection, query: str, parameters: tuple[Any, ...]) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(query, parameters).fetchall()]


class RemoteDeletionService:
    """Preview and delete exact tenant-local managed-state targets."""

    def __init__(
        self,
        service: CorpusService,
        *,
        codec: RequestStateCodec,
        resource: str,
        ttl_seconds: float,
        source_root_guard: Callable[[CorpusService, dict[str, Any]], bool],
    ) -> None:
        self.service = service
        self.codec = codec
        self.resource = resource
        self.resource_sha256 = hashlib.sha256(resource.encode()).hexdigest()
        self.ttl_seconds = min(float(ttl_seconds), 600.0)
        self.source_root_guard = source_root_guard
        self.tenant_ref = hashlib.sha256(
            str(service.data_root.expanduser().resolve()).encode()
        ).hexdigest()

    def _require_external_corpus(self, corpus_id: str) -> dict[str, Any]:
        try:
            corpus = get_corpus(self.service.data_root, corpus_id)
        except CorpusNotFoundError as exc:
            raise CorpusNotFoundError("corpus is not available") from exc
        try:
            source_root_allowed = self.source_root_guard(self.service, corpus) is True
        except Exception:
            source_root_allowed = False
        if corpus["execution_policy"] != "external_host_allowed" or not source_root_allowed:
            raise CorpusNotFoundError("corpus is not available")
        return corpus

    def _require_external_corpora(self, corpus_ids: list[str]) -> None:
        if not corpus_ids:
            raise ContextNotFoundError("context does not exist")
        for corpus_id in corpus_ids:
            try:
                self._require_external_corpus(corpus_id)
            except CorpusNotFoundError as exc:
                raise ContextNotFoundError("context does not exist") from exc

    def _context_state_from_connection(
        self,
        connection,
        context_id: str,
    ) -> dict[str, Any]:
        row = connection.execute(
            "SELECT * FROM contexts WHERE context_id = ?",
            (context_id,),
        ).fetchone()
        if row is None:
            raise ContextNotFoundError("context does not exist")
        corpus_ids = [
            value["corpus_id"]
            for value in connection.execute(
                """
                SELECT corpus_id FROM context_corpora
                WHERE context_id = ? ORDER BY corpus_id
                """,
                (context_id,),
            ).fetchall()
        ]
        self._require_external_corpora(corpus_ids)
        counts = {
            "items": connection.execute(
                "SELECT COUNT(*) FROM context_items WHERE context_id = ?",
                (context_id,),
            ).fetchone()[0],
            "source_links": connection.execute(
                """
                SELECT COUNT(*) FROM context_sources s
                JOIN context_items i ON i.item_id = s.item_id
                WHERE i.context_id = ?
                """,
                (context_id,),
            ).fetchone()[0],
            "linked_source_links": connection.execute(
                """
                SELECT COUNT(*) FROM context_external_sources s
                JOIN context_items i ON i.item_id = s.item_id
                WHERE i.context_id = ?
                """,
                (context_id,),
            ).fetchone()[0],
            "general_releases": connection.execute(
                """
                SELECT COUNT(*) FROM context_release_manifests
                WHERE context_id = ?
                """,
                (context_id,),
            ).fetchone()[0],
        }
        identity = {
            "context": dict(row),
            "corpus_ids": corpus_ids,
            "counts": counts,
        }
        return {
            "target_kind": "context",
            "target_id": context_id,
            "state": row["state"],
            "version": row["version"],
            "corpus_ids": corpus_ids,
            "managed_state": counts,
            "source_files_will_be_deleted": False,
            "deletion_ready": True,
            "state_digest": _digest(identity),
        }

    def _context_state(self, context_id: str) -> dict[str, Any]:
        context_id = normalize_context_id(context_id)
        if not (self.service.data_root / "contexts.sqlite3").exists():
            raise ContextNotFoundError("context does not exist")
        with context_read_connection(self.service.data_root) as connection:
            return self._context_state_from_connection(connection, context_id)

    def _binding_state_from_connection(
        self,
        connection,
        binding_id: str,
    ) -> dict[str, Any]:
        row = connection.execute(
            "SELECT * FROM corpus_source_bindings WHERE binding_id = ?",
            (binding_id,),
        ).fetchone()
        if row is None:
            raise ContextNotFoundError("linked source binding does not exist")
        self._require_external_corpus(row["corpus_id"])
        runs = _rows(
            connection,
            """
            SELECT run_id, base_complete_run_id, status, started_at,
                   completed_at, superseded_at
            FROM external_source_runs
            WHERE binding_id = ? ORDER BY run_id
            """,
            (binding_id,),
        )
        records = _rows(
            connection,
            """
            SELECT source_record_id, external_id, metadata_sha256,
                   membership_state, last_seen_run_id, last_seen_at
            FROM external_source_records
            WHERE binding_id = ? ORDER BY source_record_id
            """,
            (binding_id,),
        )
        context_refs = connection.execute(
            """
            SELECT COUNT(*) FROM context_external_sources
            WHERE binding_id = ?
            """,
            (binding_id,),
        ).fetchone()[0]
        ready = context_refs == 0
        identity = {
            "binding": dict(row),
            "runs": runs,
            "records": records,
            "context_reference_count": context_refs,
        }
        result = {
            "target_kind": "source_binding",
            "target_id": binding_id,
            "corpus_id": row["corpus_id"],
            "provider_kind": row["provider_kind"],
            "state": row["state"],
            "managed_state": {
                "observation_runs": len(runs),
                "source_records": len(records),
                "context_references": context_refs,
            },
            "source_files_will_be_deleted": False,
            "deletion_ready": ready,
            "state_digest": _digest(identity),
        }
        if not ready:
            result["blockers"] = ["context_references"]
        return result

    def _binding_state(self, binding_id: str) -> dict[str, Any]:
        binding_id = normalize_source_binding_id(binding_id)
        if not (self.service.data_root / "contexts.sqlite3").exists():
            raise ContextNotFoundError("linked source binding does not exist")
        with context_read_connection(self.service.data_root) as connection:
            return self._binding_state_from_connection(connection, binding_id)

    def _corpus_dependencies(self) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
        contexts_by_corpus: dict[str, list[str]] = {}
        bindings_by_corpus: dict[str, list[str]] = {}
        if not (self.service.data_root / "contexts.sqlite3").exists():
            return contexts_by_corpus, bindings_by_corpus
        with context_read_connection(self.service.data_root) as connection:
            for row in connection.execute(
                "SELECT corpus_id, context_id FROM context_corpora ORDER BY corpus_id, context_id"
            ).fetchall():
                contexts_by_corpus.setdefault(row["corpus_id"], []).append(row["context_id"])
            for row in connection.execute(
                """
                SELECT corpus_id, binding_id FROM corpus_source_bindings
                ORDER BY corpus_id, binding_id
                """
            ).fetchall():
                bindings_by_corpus.setdefault(row["corpus_id"], []).append(row["binding_id"])
        return contexts_by_corpus, bindings_by_corpus

    def _corpus_state(self, corpus_id: str) -> dict[str, Any]:
        corpus_id = normalize_corpus_id(corpus_id)
        require_no_remote_delete_intent(self.service.data_root, corpus_id)
        corpus = self._require_external_corpus(corpus_id)
        contexts_by_corpus, bindings_by_corpus = self._corpus_dependencies()
        context_ids = contexts_by_corpus.get(corpus_id, [])
        binding_ids = bindings_by_corpus.get(corpus_id, [])
        blockers = []
        if context_ids:
            blockers.append("linked_contexts")
        if binding_ids:
            blockers.append("linked_source_bindings")
        source_sync_blocker = source_sync_delete_blocker(
            self.service.data_root,
            corpus_id,
        )
        if source_sync_blocker is not None:
            blockers.append("source_sync_in_progress")
        paths = RuntimePaths(data_root=self.service.data_root, corpus_id=corpus_id)
        identity = {
            "registration": corpus,
            "context_ids": context_ids,
            "binding_ids": binding_ids,
            "source_sync_blocker": source_sync_blocker,
        }
        result = {
            "target_kind": "corpus",
            "target_id": corpus_id,
            "provider_kind": corpus["provider_kind"],
            "managed_state": {
                "registration": True,
                "index": paths.corpus_root.exists(),
                "linked_context_ids": context_ids,
                "linked_source_binding_ids": binding_ids,
                "retained_capture_copies": paths.blobs.exists(),
                "remote_source_vault": False,
                "source_sync_state": (
                    source_sync_blocker["index_state"]
                    if source_sync_blocker is not None
                    else "settled_or_absent"
                ),
            },
            "source_files_will_be_deleted": False,
            "deletion_ready": not blockers,
            "state_digest": _digest(identity),
        }
        if blockers:
            result["blockers"] = blockers
        return result

    def _state(self, target_kind: DeleteTargetKind, target_id: str) -> dict[str, Any]:
        if target_kind == "context":
            return self._context_state(target_id)
        if target_kind == "source_binding":
            return self._binding_state(target_id)
        if target_kind == "corpus":
            return self._corpus_state(target_id)
        raise InvalidRemoteDeleteTicketError()

    def preview(
        self,
        *,
        target_kind: DeleteTargetKind,
        target_id: str,
    ) -> dict[str, Any]:
        state = self._state(target_kind, target_id)
        public_state = {key: value for key, value in state.items() if key != "state_digest"}
        if not state["deletion_ready"]:
            return {
                **public_state,
                "deletion_ticket": None,
                "confirmation_required": False,
            }
        expires_at = time.time() + self.ttl_seconds
        payload = {
            "action": "delete_corpus_managed_state_v1",
            "resource": self.resource,
            "tenant_ref": self.tenant_ref,
            "target_kind": state["target_kind"],
            "target_id": state["target_id"],
            "state_digest": state["state_digest"],
            "operation_id": uuid.uuid4().hex,
            "expires_at": expires_at,
        }
        return {
            **public_state,
            "deletion_ticket": self.codec.seal(encode_json(payload).encode()),
            "expires_at": expires_at,
            "confirmation_required": True,
        }

    def _decode_ticket(self, ticket: str) -> dict[str, Any]:
        try:
            raw = self.codec.unseal(ticket)
            payload = json.loads(raw)
        except Exception as exc:
            raise InvalidRemoteDeleteTicketError() from exc
        invalid = (
            not isinstance(payload, dict)
            or payload.get("action") != "delete_corpus_managed_state_v1"
            or payload.get("resource") != self.resource
            or payload.get("tenant_ref") != self.tenant_ref
            or payload.get("target_kind") not in {"context", "source_binding", "corpus"}
            or not isinstance(payload.get("target_id"), str)
            or not isinstance(payload.get("state_digest"), str)
            or not isinstance(payload.get("operation_id"), str)
            or not isinstance(payload.get("expires_at"), int | float)
        )
        if invalid:
            raise InvalidRemoteDeleteTicketError()
        if payload["expires_at"] <= time.time():
            if payload["target_kind"] != "corpus":
                raise InvalidRemoteDeleteTicketError()
            try:
                corpus_id = normalize_corpus_id(payload["target_id"])
                intent = read_remote_delete_intent(self.service.data_root, corpus_id)
            except CorpusError as exc:
                raise InvalidRemoteDeleteTicketError() from exc
            if intent is None or not self._intent_matches_payload(intent, payload):
                raise InvalidRemoteDeleteTicketError()
        return payload

    def _intent_matches_payload(
        self,
        intent: dict[str, Any],
        payload: dict[str, Any],
    ) -> bool:
        return (
            intent["corpus_id"] == payload.get("target_id")
            and intent["operation_id"] == payload.get("operation_id")
            and intent["state_digest"] == payload.get("state_digest")
            and intent["tenant_ref"] == self.tenant_ref
            and intent["resource_sha256"] == self.resource_sha256
        )

    @staticmethod
    def _require_same_state(current: dict[str, Any], payload: dict[str, Any]) -> None:
        if not current["deletion_ready"]:
            raise RemoteDeleteBlockedError(
                "the previewed target now has managed-state dependencies",
                details={"blockers": current.get("blockers", [])},
            )
        if current["state_digest"] != payload["state_digest"]:
            raise ContextConflictError(
                "the previewed deletion target changed; preview it again",
                details={"reason": "delete_target_changed"},
            )

    @staticmethod
    def _sync_deleted_database(path: Path) -> None:
        """Close rollback-journal durability gaps before reporting live deletion."""

        with private_directory(path.parent) as parent_descriptor:
            descriptor, _created = open_private_file_at(
                parent_descriptor,
                path.name,
                path=path,
            )
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            for suffix in ("-journal", "-wal", "-shm"):
                try:
                    os.stat(
                        f"{path.name}{suffix}",
                        dir_fd=parent_descriptor,
                        follow_symlinks=False,
                    )
                except FileNotFoundError:
                    continue
                raise RemoteDeletionRecoveryRequiredError(
                    "database deletion journal cleanup did not converge",
                    details={"reason": "database_journal_remains"},
                )
            os.fsync(parent_descriptor)

    def _delete_context(self, payload: dict[str, Any]) -> dict[str, Any]:
        context_id = normalize_context_id(payload["target_id"])
        with context_writer_lock(self.service.data_root):
            if not (self.service.data_root / "contexts.sqlite3").exists():
                return {
                    "target_kind": "context",
                    "target_id": context_id,
                    "removed": False,
                    "idempotent_replay": True,
                    "source_files_deleted": False,
                }
            with context_connection(self.service.data_root) as connection:
                exists = connection.execute(
                    "SELECT 1 FROM contexts WHERE context_id = ?",
                    (context_id,),
                ).fetchone()
                if exists is None:
                    return {
                        "target_kind": "context",
                        "target_id": context_id,
                        "removed": False,
                        "idempotent_replay": True,
                        "source_files_deleted": False,
                    }
                current = self._context_state_from_connection(connection, context_id)
                self._require_same_state(current, payload)
                connection.execute("DELETE FROM contexts WHERE context_id = ?", (context_id,))
            self._sync_deleted_database(self.service.data_root / "contexts.sqlite3")
        return {
            "target_kind": "context",
            "target_id": context_id,
            "removed": True,
            "idempotent_replay": False,
            "removed_managed_state": current["managed_state"],
            "source_files_deleted": False,
        }

    def _delete_binding(self, payload: dict[str, Any]) -> dict[str, Any]:
        binding_id = normalize_source_binding_id(payload["target_id"])
        with context_writer_lock(self.service.data_root):
            if not (self.service.data_root / "contexts.sqlite3").exists():
                return {
                    "target_kind": "source_binding",
                    "target_id": binding_id,
                    "removed": False,
                    "idempotent_replay": True,
                    "source_files_deleted": False,
                }
            with context_connection(self.service.data_root) as connection:
                exists = connection.execute(
                    "SELECT 1 FROM corpus_source_bindings WHERE binding_id = ?",
                    (binding_id,),
                ).fetchone()
                if exists is None:
                    return {
                        "target_kind": "source_binding",
                        "target_id": binding_id,
                        "removed": False,
                        "idempotent_replay": True,
                        "source_files_deleted": False,
                    }
                current = self._binding_state_from_connection(connection, binding_id)
                self._require_same_state(current, payload)
                connection.execute(
                    "DELETE FROM external_source_records WHERE binding_id = ?",
                    (binding_id,),
                )
                connection.execute(
                    "DELETE FROM external_source_runs WHERE binding_id = ?",
                    (binding_id,),
                )
                connection.execute(
                    "DELETE FROM corpus_source_bindings WHERE binding_id = ?",
                    (binding_id,),
                )
            self._sync_deleted_database(self.service.data_root / "contexts.sqlite3")
        return {
            "target_kind": "source_binding",
            "target_id": binding_id,
            "removed": True,
            "idempotent_replay": False,
            "removed_managed_state": current["managed_state"],
            "source_files_deleted": False,
        }

    @staticmethod
    def _private_tree_exists(path: Path) -> bool:
        try:
            with private_directory(path):
                return True
        except CorpusError as exc:
            if exc.details.get("reason") in {"missing", "missing_parent"}:
                return False
            raise RemoteDeletionRecoveryRequiredError(
                "Corpus-managed deletion state is unsafe",
                details={"reason": "managed_index_state_unsafe"},
            ) from exc

    def _quarantine_managed_index(
        self,
        *,
        corpus_id: str,
        quarantine_name: str,
    ) -> bool:
        corpora_root = self.service.data_root / "corpora"
        live = corpora_root / corpus_id
        quarantine = corpora_root / quarantine_name
        live_exists = self._private_tree_exists(live)
        quarantine_exists = self._private_tree_exists(quarantine)
        if live_exists and quarantine_exists:
            raise RemoteDeletionRecoveryRequiredError(
                "both live and quarantined Corpus index trees exist",
                details={"reason": "ambiguous_managed_index_state"},
            )
        if not live_exists:
            with private_directory(corpora_root) as corpora_descriptor:
                os.fsync(corpora_descriptor)
            return quarantine_exists
        with private_directory(corpora_root) as corpora_descriptor:
            os.rename(
                corpus_id,
                quarantine_name,
                src_dir_fd=corpora_descriptor,
                dst_dir_fd=corpora_descriptor,
            )
            os.fsync(corpora_descriptor)
        return True

    def _catalog_registration_sha256(self, corpus_id: str) -> str:
        catalog = self.service.data_root / "catalog.sqlite"
        if not catalog.exists():
            raise CorpusNotFoundError("corpus is not available")
        with closing(connect(catalog)) as connection:
            row = connection.execute(
                "SELECT * FROM corpora WHERE corpus_id = ?",
                (corpus_id,),
            ).fetchone()
        if row is None:
            raise CorpusNotFoundError("corpus is not available")
        return _digest(dict(row))

    def _delete_catalog_registration(
        self,
        corpus_id: str,
        *,
        expected_sha256: str,
    ) -> bool:
        catalog = self.service.data_root / "catalog.sqlite"
        if not catalog.exists():
            return False
        with closing(connect(catalog)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM corpora WHERE corpus_id = ?",
                    (corpus_id,),
                ).fetchone()
                if row is None:
                    connection.rollback()
                    removed = 0
                else:
                    if _digest(dict(row)) != expected_sha256:
                        raise RemoteDeletionRecoveryRequiredError(
                            "the Corpus registration changed after deletion was accepted",
                            details={"reason": "registration_changed_during_deletion"},
                        )
                    removed = connection.execute(
                        "DELETE FROM corpora WHERE corpus_id = ?",
                        (corpus_id,),
                    ).rowcount
                    connection.commit()
            except BaseException:
                connection.rollback()
                raise
        self._sync_deleted_database(catalog)
        return removed == 1

    def _quarantine_source_sync_state(
        self,
        *,
        corpus_id: str,
        quarantine_name: str,
    ) -> bool:
        source_sync_root = self.service.data_root / "source-sync"
        live = source_sync_root / corpus_id
        quarantine = source_sync_root / quarantine_name
        live_exists = self._private_tree_exists(live)
        quarantine_exists = self._private_tree_exists(quarantine)
        if live_exists and quarantine_exists:
            raise RemoteDeletionRecoveryRequiredError(
                "both live and quarantined source-sync state trees exist",
                details={"reason": "ambiguous_source_sync_state"},
            )
        if not live_exists:
            with private_directory(source_sync_root) as source_sync_descriptor:
                os.fsync(source_sync_descriptor)
            return quarantine_exists
        with private_directory(source_sync_root) as source_sync_descriptor:
            os.rename(
                corpus_id,
                quarantine_name,
                src_dir_fd=source_sync_descriptor,
                dst_dir_fd=source_sync_descriptor,
            )
            os.fsync(source_sync_descriptor)
        return True

    @staticmethod
    def _require_private_removal_entry(metadata: os.stat_result, *, path: Path) -> None:
        if metadata.st_uid != os.geteuid():
            raise RemoteDeletionRecoveryRequiredError(
                "Corpus-managed deletion tree has an unexpected owner",
                details={"reason": "managed_state_owner_mismatch"},
            )
        if stat.S_ISDIR(metadata.st_mode):
            expected_mode = 0o700
        elif stat.S_ISREG(metadata.st_mode):
            expected_mode = 0o600
            if metadata.st_nlink != 1:
                raise RemoteDeletionRecoveryRequiredError(
                    "Corpus-managed deletion file has multiple links",
                    details={"reason": "managed_state_link_count"},
                )
        else:
            raise RemoteDeletionRecoveryRequiredError(
                "Corpus-managed deletion tree contains a special entry",
                details={"reason": "managed_state_special_entry"},
            )
        if stat.S_IMODE(metadata.st_mode) != expected_mode:
            raise RemoteDeletionRecoveryRequiredError(
                "Corpus-managed deletion tree has unsafe permissions",
                details={"reason": "managed_state_permissions"},
            )

    @classmethod
    def _durable_clear_directory(cls, descriptor: int, *, path: Path) -> None:
        # A retry may follow a removal whose parent fsync did not complete.
        os.fsync(descriptor)
        for name in os.listdir(descriptor):
            entry_path = path / name
            try:
                before = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            except FileNotFoundError:
                os.fsync(descriptor)
                continue
            cls._require_private_removal_entry(before, path=entry_path)
            if stat.S_ISREG(before.st_mode):
                os.unlink(name, dir_fd=descriptor)
                os.fsync(descriptor)
                continue
            flags = (
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            child = os.open(name, flags, dir_fd=descriptor)
            try:
                opened = os.fstat(child)
                if opened.st_dev != before.st_dev or opened.st_ino != before.st_ino:
                    raise RemoteDeletionRecoveryRequiredError(
                        "Corpus-managed deletion directory changed while opening",
                        details={"reason": "managed_state_changed"},
                    )
                cls._require_private_removal_entry(opened, path=entry_path)
                cls._durable_clear_directory(child, path=entry_path)
            finally:
                os.close(child)
            os.rmdir(name, dir_fd=descriptor)
            os.fsync(descriptor)

    @classmethod
    def _durable_remove_private_tree(cls, parent: Path, name: str) -> bool:
        path = parent / name
        with private_directory(parent) as parent_descriptor:
            try:
                before = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
            except FileNotFoundError:
                os.fsync(parent_descriptor)
                return False
            cls._require_private_removal_entry(before, path=path)
            if not stat.S_ISDIR(before.st_mode):
                raise RemoteDeletionRecoveryRequiredError(
                    "Corpus-managed deletion target is not a directory",
                    details={"reason": "managed_state_not_directory"},
                )
            flags = (
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            descriptor = os.open(name, flags, dir_fd=parent_descriptor)
            try:
                opened = os.fstat(descriptor)
                if opened.st_dev != before.st_dev or opened.st_ino != before.st_ino:
                    raise RemoteDeletionRecoveryRequiredError(
                        "Corpus-managed deletion target changed while opening",
                        details={"reason": "managed_state_changed"},
                    )
                cls._require_private_removal_entry(opened, path=path)
                cls._durable_clear_directory(descriptor, path=path)
            finally:
                os.close(descriptor)
            os.rmdir(name, dir_fd=parent_descriptor)
            os.fsync(parent_descriptor)
        return True

    def _remove_quarantined_index(self, quarantine_name: str) -> bool:
        return self._durable_remove_private_tree(
            self.service.data_root / "corpora",
            quarantine_name,
        )

    def _remove_quarantined_source_sync(self, quarantine_name: str) -> bool:
        return self._durable_remove_private_tree(
            self.service.data_root / "source-sync",
            quarantine_name,
        )

    def _finish_corpus_deletion(
        self,
        intent: dict[str, Any],
    ) -> None:
        corpus_id = intent["corpus_id"]
        quarantine_name = intent["quarantine_name"]
        source_sync_quarantine_name = intent["source_sync_quarantine_name"]

        persist_source_sync_deletion_epoch(
            self.service.data_root,
            corpus_id,
            intent["source_generation_tombstone"],
            intent["source_manifest_tombstone_sha256"],
        )

        self._quarantine_managed_index(
            corpus_id=corpus_id,
            quarantine_name=quarantine_name,
        )
        self._quarantine_source_sync_state(
            corpus_id=corpus_id,
            quarantine_name=source_sync_quarantine_name,
        )
        intent["phase"] = "index_quarantined"
        write_remote_delete_intent(self.service.data_root, intent)

        self._delete_catalog_registration(
            corpus_id,
            expected_sha256=intent["registration_sha256"],
        )
        intent["phase"] = "catalog_deleted"
        write_remote_delete_intent(self.service.data_root, intent)

        intent["phase"] = "cleanup_pending"
        write_remote_delete_intent(self.service.data_root, intent)
        self._remove_quarantined_index(quarantine_name)
        self._remove_quarantined_source_sync(source_sync_quarantine_name)

        live = self.service.data_root / "corpora" / corpus_id
        quarantine = self.service.data_root / "corpora" / quarantine_name
        source_sync_live = self.service.data_root / "source-sync" / corpus_id
        source_sync_quarantine = (
            self.service.data_root / "source-sync" / source_sync_quarantine_name
        )
        if (
            self._private_tree_exists(live)
            or self._private_tree_exists(quarantine)
            or self._private_tree_exists(source_sync_live)
            or self._private_tree_exists(source_sync_quarantine)
        ):
            raise RemoteDeletionRecoveryRequiredError(
                "Corpus-managed deletion did not converge",
                details={"reason": "managed_state_cleanup_incomplete"},
            )
        try:
            get_corpus(self.service.data_root, corpus_id)
        except CorpusNotFoundError:
            pass
        else:
            raise RemoteDeletionRecoveryRequiredError(
                "Corpus catalog deletion did not converge",
                details={"reason": "catalog_cleanup_incomplete"},
            )
        remove_remote_delete_intent(self.service.data_root, corpus_id)

    def _delete_corpus(self, payload: dict[str, Any]) -> dict[str, Any]:
        corpus_id = normalize_corpus_id(payload["target_id"])
        paths = RuntimePaths(data_root=self.service.data_root, corpus_id=corpus_id)
        if not shutil.rmtree.avoids_symlink_attacks:
            raise RemoteDeleteBlockedError(
                "this host cannot safely remove the private Corpus state tree"
            )
        with remote_source_sync_deletion_lock(self.service.data_root, corpus_id):
            intent = read_remote_delete_intent(self.service.data_root, corpus_id)
            if intent is not None:
                if not self._intent_matches_payload(intent, payload):
                    raise RemoteDeletionRecoveryRequiredError(
                        "another exact deletion must finish before this corpus can change",
                        details={"reason": "different_deletion_intent_active"},
                    )
                self._finish_corpus_deletion(intent)
                return {
                    "target_kind": "corpus",
                    "target_id": corpus_id,
                    "removed": False,
                    "idempotent_replay": True,
                    "removed_managed_state": intent["managed_state"],
                    "source_files_deleted": False,
                }
            try:
                self._require_external_corpus(corpus_id)
            except CorpusNotFoundError:
                return {
                    "target_kind": "corpus",
                    "target_id": corpus_id,
                    "removed": False,
                    "idempotent_replay": True,
                    "source_files_deleted": False,
                }
            current = self._corpus_state(corpus_id)
            self._require_same_state(current, payload)
            lock = (
                writer_lock(paths.corpus_root / "writer.lock")
                if self._private_tree_exists(paths.corpus_root)
                else nullcontext()
            )
            with lock:
                current = self._corpus_state(corpus_id)
                self._require_same_state(current, payload)
                now = utc_now()
                source_tombstone = next_source_sync_deletion_epoch(
                    self.service.data_root,
                    corpus_id,
                )
                intent = {
                    "format": REMOTE_DELETE_INTENT_FORMAT,
                    "corpus_id": corpus_id,
                    "operation_id": payload["operation_id"],
                    "state_digest": payload["state_digest"],
                    "registration_sha256": self._catalog_registration_sha256(corpus_id),
                    "tenant_ref": self.tenant_ref,
                    "resource_sha256": self.resource_sha256,
                    "quarantine_name": f".deleting-{payload['operation_id']}",
                    "source_sync_quarantine_name": (f".deleting-sync-{payload['operation_id']}"),
                    "source_generation_tombstone": source_tombstone["generation"],
                    "source_manifest_tombstone_sha256": source_tombstone["manifest_sha256"],
                    "phase": "prepared",
                    "managed_state": current["managed_state"],
                    "created_at": now,
                    "updated_at": now,
                }
                write_remote_delete_intent(self.service.data_root, intent)
                self._finish_corpus_deletion(intent)
        return {
            "target_kind": "corpus",
            "target_id": corpus_id,
            "removed": True,
            "idempotent_replay": False,
            "removed_managed_state": current["managed_state"],
            "source_files_deleted": False,
        }

    def delete(self, *, deletion_ticket: str) -> dict[str, Any]:
        payload = self._decode_ticket(deletion_ticket)
        target_kind = payload["target_kind"]
        if target_kind == "context":
            return self._delete_context(payload)
        if target_kind == "source_binding":
            return self._delete_binding(payload)
        return self._delete_corpus(payload)


__all__ = [
    "DeleteTargetKind",
    "InvalidRemoteDeleteTicketError",
    "RemoteDeleteBlockedError",
    "RemoteDeletionService",
]
