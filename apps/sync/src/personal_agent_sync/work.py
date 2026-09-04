"""Execute remote Work requests through the isolated local Corpus authority."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path
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
            max_bytes=request.get("max_bytes", 2 * 1024 * 1024),
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

_ROOT_REBIND_HELPER = r"""
import json
import sys
from pathlib import Path

from corpus.errors import CorpusError
from corpus.service import CorpusService


def main():
    payload = json.load(sys.stdin)
    service = CorpusService(Path(sys.argv[1]))
    replacement = Path(payload["root"])
    source_ids = []
    workspace_ids = []
    for requested in payload["connections"]:
        resolved = service.spaces.resolve_connection(
            space_id=requested["space_id"],
            connection_id=requested["connection_id"],
            audience="local_cli",
            capability="read",
        )
        actual_roles = set(resolved["connection"]["roles"])
        requested_roles = set(requested["roles"])
        if actual_roles != requested_roles:
            raise ValueError("local Corpus Connection roles do not match Sync")
        if "source" in requested_roles:
            corpus_id = requested.get("corpus_id")
            resolved_source_ids = list(resolved["_source_ids"])
            if not isinstance(corpus_id, str) or resolved_source_ids != [corpus_id]:
                raise ValueError("local Corpus Source binding does not match Sync")
            if corpus_id not in source_ids:
                source_ids.append(corpus_id)
        if "work" in requested_roles:
            workspace_id = resolved["_workspace_id"]
            if not isinstance(workspace_id, str):
                raise ValueError("local Corpus Work binding is unavailable")
            if workspace_id not in workspace_ids:
                workspace_ids.append(workspace_id)

    corpora = {item["corpus_id"]: item for item in service.corpora()}
    source_results = []
    for corpus_id in source_ids:
        current = corpora.get(corpus_id)
        if current is None or not isinstance(current.get("source_root"), str):
            raise ValueError("local Corpus Source registration is unavailable")
        source_results.append(
            service.rebind_source_root(
                corpus_id=corpus_id,
                source_root=replacement,
                expected_source_root=Path(current["source_root"]),
            )
        )

    workspace_results = []
    for workspace_id in workspace_ids:
        current = service.workspace_status(
            workspace_id=workspace_id, audience="local_cli"
        )["work_folder"]
        current_root = current.get("root_path")
        if not isinstance(current_root, str):
            raise ValueError("local Corpus Work registration is unavailable")
        workspace_results.append(
            service.workspace_rebind_root(
                workspace_id=workspace_id,
                root=replacement,
                expected_root=Path(current_root),
            )
        )

    print(
        json.dumps(
            {
                "ok": True,
                "result": {
                    "root": str(replacement),
                    "sources": source_results,
                    "workspaces": workspace_results,
                },
            },
            ensure_ascii=False,
        )
    )


try:
    main()
except CorpusError as error:
    print(
        json.dumps(
            {
                "ok": False,
                "error": {
                    "code": error.code,
                    "message": str(error),
                    "details": error.details,
                },
            },
            ensure_ascii=False,
        )
    )
except Exception:
    print(
        json.dumps(
            {
                "ok": False,
                "error": {
                    "code": "local_root_rebind_failed",
                    "message": "the local Corpus root rebind failed",
                },
            }
        )
    )
"""


def _document_files_executable() -> Path:
    executable = Path(sys.executable).with_name("document-files")
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise SyncError(
            "local_document_files_unavailable",
            "the Sync runtime does not include the Document Files executable",
        )
    return executable


def rebind_local_corpus_roots(
    config: SyncConfig, connection_keys: set[str], root: Path
) -> dict[str, Any]:
    """Keep isolated local Corpus Source and Work roots aligned with Sync."""

    if config.corpus_data_root is None or config.corpus_python is None:
        raise SyncError(
            "local_corpus_unavailable",
            "the Corpus root authority is not configured",
        )
    selected = [
        connection
        for connection in config.connections
        if connection.key in connection_keys
    ]
    if {connection.key for connection in selected} != connection_keys:
        raise SyncError(
            "connection_not_found",
            "not every rebound Connection exists in config",
        )
    payload = canonical(
        {
            "root": unicodedata.normalize("NFC", str(root)),
            "connections": [
                {
                    "space_id": connection.space_id,
                    "connection_id": connection.connection_id,
                    "roles": sorted(connection.roles),
                    "corpus_id": connection.corpus_id,
                }
                for connection in selected
            ],
        }
    )
    environment = os.environ.copy()
    environment["DOCUMENT_FILES_EXECUTABLE"] = str(_document_files_executable())
    try:
        completed = subprocess.run(
            [
                str(config.corpus_python),
                "-c",
                _ROOT_REBIND_HELPER,
                str(config.corpus_data_root),
            ],
            input=payload,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SyncError(
            "local_corpus_unavailable",
            "the Corpus root authority could not be started",
        ) from exc
    if completed.returncode != 0 or len(completed.stdout.encode()) > _MAX_HELPER_OUTPUT:
        raise SyncError(
            "local_root_rebind_failed",
            "the local Corpus root rebind failed",
        )
    try:
        response = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise SyncError(
            "local_root_rebind_failed",
            "the local Corpus root rebind returned invalid data",
        ) from exc
    if not isinstance(response, dict) or type(response.get("ok")) is not bool:
        raise SyncError(
            "local_root_rebind_failed",
            "the local Corpus root rebind returned invalid data",
        )
    if not response["ok"]:
        error = response.get("error")
        if not isinstance(error, dict):
            raise SyncError(
                "local_root_rebind_failed",
                "the local Corpus root rebind failed",
            )
        code = error.get("code")
        message = error.get("message")
        if not isinstance(code, str) or not isinstance(message, str):
            raise SyncError(
                "local_root_rebind_failed",
                "the local Corpus root rebind failed",
            )
        raise SyncError(code, message)
    result = response.get("result")
    if not isinstance(result, dict):
        raise SyncError(
            "local_root_rebind_failed",
            "the local Corpus root rebind returned invalid data",
        )
    return result


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
        document_files_executable = _document_files_executable()
        environment = os.environ.copy()
        environment["DOCUMENT_FILES_EXECUTABLE"] = str(document_files_executable)
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
                env=environment,
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
