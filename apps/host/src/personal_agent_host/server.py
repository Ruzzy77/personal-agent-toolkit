"""Host MCP tools.

The tool surface is fixed at eight tools named ``host_*``. This module registers
the first three; ``files``/``jobs`` modules extend it in later steps.
"""

from __future__ import annotations

import hashlib
import unicodedata
from pathlib import Path
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from personal_agent_sync.errors import SyncError
from pydantic import BaseModel, Field

from personal_agent_host import __version__
from personal_agent_host.config import LIMITS, HostConfig, RootPolicy

SERVER_INSTRUCTIONS = (
    "Host exposes the owner's workspace roots on the always-on host. Start with "
    "host_roots. Paths are relative to a root; paths returned by one tool are valid "
    "inputs for the others. host_write needs only root, path and content. Long "
    "commands return a job_id; read the rest with host_job."
)
READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
WRITE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=False,
    openWorldHint=False,
)

MAX_PATH_BYTES = 4096


class ToolError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ReadRequest(BaseModel):
    path: str
    start_line: Annotated[int, Field(ge=1)] = 1
    end_line: Annotated[int | None, Field(ge=1)] = None


def relative_path(root: Path, value: str) -> Path:
    """Resolve ``value`` inside ``root`` without following symlinks outside it."""

    if not value or len(value.encode("utf-8")) > MAX_PATH_BYTES:
        raise ToolError("invalid_path", "path is empty or too long")
    normalized = unicodedata.normalize("NFC", value).replace("\\", "/")
    if normalized.startswith(("/", "~")):
        raise ToolError("invalid_path", "path must be relative to the root")
    parts = [part for part in normalized.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise ToolError("invalid_path", "path cannot leave the root")
    candidate = root.joinpath(*parts) if parts else root
    try:
        resolved = candidate.resolve(strict=False)
        base = root.resolve(strict=True)
    except OSError as exc:
        raise ToolError("root_unavailable", "root is unavailable") from exc
    if resolved != base and base not in resolved.parents:
        raise ToolError("invalid_path", "path resolves outside the root")
    return resolved


def file_version(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def read_files(
    policy: RootPolicy, files: list[ReadRequest], max_bytes: int
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    budget = max_bytes
    truncated = False
    for request in files:
        target = relative_path(policy.root, request.path)
        try:
            data = target.read_bytes()
        except FileNotFoundError as exc:
            raise ToolError("not_found", f"{request.path} does not exist") from exc
        except IsADirectoryError as exc:
            raise ToolError("invalid_path", f"{request.path} is a directory") from exc
        except OSError as exc:
            raise ToolError("read_failed", f"{request.path} could not be read") from exc
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ToolError("not_text", f"{request.path} is not UTF-8 text") from exc
        lines = text.splitlines(keepends=True)
        start = request.start_line
        end = request.end_line or len(lines)
        if end < start:
            raise ToolError("invalid_range", "end_line is before start_line")
        selected: list[str] = []
        last = start - 1
        for index in range(start - 1, min(end, len(lines))):
            line = lines[index]
            size = len(line.encode("utf-8"))
            if size > budget:
                if not selected and size > max_bytes:
                    raise ToolError(
                        "line_too_long", f"{request.path}:{index + 1} exceeds max_bytes"
                    )
                truncated = True
                break
            selected.append(line)
            budget -= size
            last = index + 1
        results.append(
            {
                "path": request.path,
                "content": "".join(selected),
                "version": file_version(data),
                "start_line": start,
                "end_line": last,
            }
        )
        if truncated:
            break
    return {"files": results, "truncated": truncated}


def create_server(config: HostConfig) -> MCPServer:
    server = MCPServer("Host", version=__version__, instructions=SERVER_INSTRUCTIONS)

    def safe(operation: Any) -> dict[str, Any]:
        try:
            return operation()
        except ToolError as exc:
            return {"error": {"code": exc.code, "message": str(exc)}}
        except SyncError as exc:
            return {"error": {"code": exc.code, "message": str(exc)}}

    @server.tool(
        name="host_capabilities",
        title="Host capabilities",
        description="Return the Host version and the shared size and time limits.",
        annotations=READ_ONLY,
    )
    def host_capabilities() -> dict[str, Any]:
        return {"version": __version__, "limits": dict(LIMITS)}

    @server.tool(
        name="host_roots",
        title="List roots",
        description=(
            "List the registered workspace roots with their write permission, "
            "execution policy, and the Source roots each may read."
        ),
        annotations=READ_ONLY,
    )
    def host_roots() -> dict[str, Any]:
        return {
            "roots": [
                {
                    "id": root.id,
                    "permission": root.permission,
                    "execute": root.execute,
                    "sources": list(root.sources),
                }
                for root in config.roots
            ]
        }

    @server.tool(
        name="host_read",
        title="Read files",
        description=(
            "Read UTF-8 text files from a root, optionally by line range. Returns each "
            "file's content and version. max_bytes caps the total returned content."
        ),
        annotations=READ_ONLY,
    )
    def host_read(
        root: str,
        files: Annotated[list[ReadRequest], Field(min_length=1, max_length=32)],
        max_bytes: Annotated[int, Field(ge=1, le=LIMITS["read_bytes"])] = 65_536,
    ) -> dict[str, Any]:
        return safe(lambda: read_files(config.root(root), files, max_bytes))

    return server
