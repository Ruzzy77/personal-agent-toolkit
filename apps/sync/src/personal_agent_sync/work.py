"""Execute remote Work requests through the isolated local Corpus authority."""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any

from .config import SyncConfig
from .errors import PolicyDenied, SyncError
from .state import SyncState, canonical

_MAX_HELPER_OUTPUT = 18 * 1024 * 1024
WORK_OPERATIONS = (
    "work.file.list",
    "work.file.read",
    "work.file.write",
    "work.file.delete",
    "work.file.select_current",
    "work.file.restore",
)
SOURCE_OPERATIONS = ("source.refresh",)
SYNC_OPERATIONS = WORK_OPERATIONS + SOURCE_OPERATIONS
_WORK_HELPER = r"""
import json
import sys
from pathlib import Path

from corpus.errors import CorpusError
from corpus.service import CorpusService


def main():
    payload = json.load(sys.stdin)
    service = CorpusService(Path(sys.argv[1]))
    operation = payload["operation"]
    request = payload["request"]
    common = {
        "space_id": payload["space_id"],
        "connection_id": payload["connection_id"],
        "audience": "external_mcp",
    }
    if operation == "work.file.list":
        result = service.space_file_list(
            **common,
            mode=request.get("mode", "list_directory"),
            relative_path=request.get("relative_path"),
            query=request.get("query"),
            cursor=request.get("cursor"),
            limit=request.get("limit", 100),
        )
    elif operation == "work.file.read":
        result = service.space_file_read(
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
    elif operation == "work.file.write":
        result = service.space_file_write(
            **common,
            relative_path=request.get("relative_path"),
            content=request.get("content"),
            content_encoding=request.get("content_encoding"),
            expected_version=request.get("expected_version"),
            replace_start_marker=request.get("replace_start_marker"),
            replace_end_marker=request.get("replace_end_marker"),
            make_current=request.get("make_current", False),
        )
    elif operation == "work.file.delete":
        result = service.space_file_delete(
            **common,
            relative_path=request.get("relative_path"),
            expected_version=request.get("expected_version"),
            confirm_delete=request.get("confirm_delete", False),
        )
    elif operation == "work.file.select_current":
        result = service.space_file_select_current(
            **common, relative_path=request.get("relative_path")
        )
    elif operation == "work.file.restore":
        result = service.space_file_restore(
            **common,
            recovery_id=request.get("recovery_id"),
            expected_version=request.get("expected_version"),
        )
    else:
        raise ValueError("unsupported operation")
    print(json.dumps({"ok": True, "result": result}, ensure_ascii=False))


try:
    main()
except CorpusError as error:
    print(json.dumps({"ok": False, "error": {"code": error.code, "message": str(error)}}))
except Exception:
    print(json.dumps({"ok": False, "error": {"code": "local_operation_failed", "message": "the local Work operation failed"}}))
"""


class WorkExecutor:
    def __init__(self, config: SyncConfig, state: SyncState) -> None:
        self.config = config
        self.state = state

    @staticmethod
    def _mapping(value: object, name: str) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise SyncError("invalid_job", f"job {name} must be an object")
        return value

    def _invoke(
        self,
        operation: str,
        space_id: str,
        connection_id: str,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        if self.config.corpus_data_root is None or self.config.corpus_python is None:
            raise SyncError(
                "local_corpus_unavailable",
                "the Corpus Work authority is not configured",
            )
        payload = canonical(
            {
                "operation": operation,
                "space_id": space_id,
                "connection_id": connection_id,
                "request": request,
            }
        )
        try:
            completed = subprocess.run(
                [
                    str(self.config.corpus_python),
                    "-c",
                    _WORK_HELPER,
                    str(self.config.corpus_data_root),
                ],
                input=payload,
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise SyncError(
                "local_corpus_unavailable",
                "the Corpus Work authority could not be started",
            ) from exc
        if (
            completed.returncode != 0
            or len(completed.stdout.encode()) > _MAX_HELPER_OUTPUT
        ):
            raise SyncError("local_operation_failed", "the local Work operation failed")
        try:
            response = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise SyncError(
                "local_operation_failed",
                "the local Work operation returned invalid data",
            ) from exc
        if not isinstance(response, dict) or type(response.get("ok")) is not bool:
            raise SyncError(
                "local_operation_failed",
                "the local Work operation returned invalid data",
            )
        if not response["ok"]:
            error = response.get("error")
            if not isinstance(error, dict):
                raise SyncError(
                    "local_operation_failed", "the local Work operation failed"
                )
            code = error.get("code")
            message = error.get("message")
            if not isinstance(code, str) or not isinstance(message, str):
                raise SyncError(
                    "local_operation_failed", "the local Work operation failed"
                )
            raise SyncError(code, message)
        result = response.get("result")
        if not isinstance(result, dict):
            raise SyncError(
                "local_operation_failed",
                "the local Work operation returned invalid data",
            )
        return result

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
        if (
            not isinstance(operation, str)
            or not isinstance(space_id, str)
            or not isinstance(connection_id, str)
        ):
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
        roles = json.loads(row["roles_json"])
        if operation.startswith("work.file.") and "work" not in set(roles):
            raise PolicyDenied("the selected Connection has no Work role")
        if operation.startswith("source.") and "source" not in set(roles):
            raise PolicyDenied("the selected Connection has no Source role")
        if operation not in SYNC_OPERATIONS:
            raise SyncError(
                "unsupported_job", "Sync app does not support this job operation"
            )
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
        if operation == "source.refresh":
            document_id = request.get("document_id")
            expected_revision = request.get("expected_revision_sha256")
            if not isinstance(document_id, str) or not re.fullmatch(
                r"doc_[0-9a-f]{32}", document_id
            ):
                raise SyncError("invalid_job", "Source document identity is invalid")
            if expected_revision is not None and (
                not isinstance(expected_revision, str)
                or not re.fullmatch(r"[0-9a-f]{64}", expected_revision)
            ):
                raise SyncError("invalid_job", "expected Source revision is invalid")
            return self.state.request_refresh(
                space_id,
                connection_id,
                document_id,
                expected_revision,
            )
        return self._invoke(operation, space_id, connection_id, request)
