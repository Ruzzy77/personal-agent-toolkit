"""Execute remote Work requests through the existing local Corpus authority."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import SyncConfig
from .errors import PolicyDenied, SyncError
from .state import SyncState


class WorkExecutor:
    def __init__(self, config: SyncConfig, state: SyncState) -> None:
        self.config = config
        self.state = state

    def _service(self):
        if self.config.corpus_data_root is None:
            raise SyncError(
                "local_corpus_unavailable",
                "Corpus local data root is not configured for Work operations",
            )
        try:
            from corpus.service import CorpusService  # type: ignore[import-not-found]
        except ImportError as exc:
            raise SyncError(
                "local_corpus_unavailable",
                "the installed Corpus Work authority is unavailable",
            ) from exc
        return CorpusService(Path(self.config.corpus_data_root))

    @staticmethod
    def _mapping(value: object, name: str) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise SyncError("invalid_job", f"job {name} must be an object")
        return value

    def execute(
        self,
        operation: str,
        scope_value: object,
        request_value: object,
    ) -> dict[str, Any]:
        scope = self._mapping(scope_value, "scope")
        request = self._mapping(request_value, "request")
        space_id = scope.get("spaceId")
        connection_id = scope.get("connectionId")
        generation = scope.get("generation")
        if not isinstance(space_id, str) or not isinstance(connection_id, str):
            raise SyncError("invalid_job", "job Connection identity is invalid")
        row = self.state.connection_for_scope(space_id, connection_id)
        if row["access_scope"] != "remote_allowed":
            raise PolicyDenied("the selected local Connection is not remote-visible")
        if generation != row["generation"]:
            raise SyncError(
                "connection_generation_conflict", "Connection binding changed"
            )
        if request.get("space_id") != space_id or request.get("connection_id") not in {
            None,
            connection_id,
        }:
            raise SyncError(
                "connection_scope_mismatch", "job request escaped its Connection scope"
            )
        if operation.startswith("work.file.") and "work" not in set(
            __import__("json").loads(row["roles_json"])
        ):
            raise PolicyDenied("the selected Connection has no Work role")
        if (
            operation
            in {
                "work.file.write",
                "work.file.delete",
                "work.file.select_current",
                "work.file.restore",
            }
            and row["permission"] != "read_write"
        ):
            raise PolicyDenied("the selected Connection is read-only")

        service = self._service()
        common = {
            "space_id": space_id,
            "connection_id": connection_id,
            "audience": "external_mcp",
        }
        if operation == "work.file.list":
            return service.space_file_list(
                **common,
                mode=request.get("mode", "list_directory"),
                relative_path=request.get("relative_path"),
                query=request.get("query"),
                cursor=request.get("cursor"),
                limit=request.get("limit", 100),
            )
        if operation == "work.file.read":
            return service.space_file_read(
                **common,
                relative_path=request.get("relative_path"),
                read_ref=None,
                encoding=request.get("encoding", "utf8"),
                max_bytes=request.get("max_bytes", 16 * 1024 * 1024),
                neighbor_span=0,
                include_structure_context=False,
                max_chars=request.get("max_chars", 100_000),
                start_char=request.get("start_char", 0),
            )
        if operation == "work.file.write":
            return service.space_file_write(
                **common,
                relative_path=request.get("relative_path"),
                content=request.get("content"),
                content_encoding=request.get("content_encoding"),
                expected_version=request.get("expected_version"),
                replace_start_marker=request.get("replace_start_marker"),
                replace_end_marker=request.get("replace_end_marker"),
                make_current=request.get("make_current", False),
            )
        if operation == "work.file.delete":
            return service.space_file_delete(
                **common,
                relative_path=request.get("relative_path"),
                expected_version=request.get("expected_version"),
                confirm_delete=request.get("confirm_delete", False),
            )
        if operation == "work.file.select_current":
            return service.space_file_select_current(
                **common,
                relative_path=request.get("relative_path"),
            )
        if operation == "work.file.restore":
            return service.space_file_restore(
                **common,
                recovery_id=request.get("recovery_id"),
                expected_version=request.get("expected_version"),
            )
        raise SyncError(
            "unsupported_job", "Sync app does not support this job operation"
        )
