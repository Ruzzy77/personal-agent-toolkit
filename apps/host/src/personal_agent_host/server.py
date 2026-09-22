"""Host MCP tools over the configured workspace roots."""

from __future__ import annotations

import base64
import binascii
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from personal_agent_host import __version__
from personal_agent_host.config import LIMITS, HostConfig
from personal_agent_host.files import ToolError, read_files, search, write_file
from personal_agent_host.jobs import JobManager, clamp_timeout
from personal_agent_host.runtime import execution_capabilities
from personal_agent_host.transfers import CHUNK_BYTES, MAX_FILE_BYTES, Transfers
from personal_agent_host.workspace_files import WorkspaceFiles

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

EXECUTE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=False,
    openWorldHint=True,
)

RootId = Annotated[str, Field(min_length=1, max_length=256)]
RelPath = Annotated[str, Field(min_length=1, max_length=4096)]
JobId = Annotated[str, Field(min_length=1, max_length=256)]


class ReadRequest(BaseModel):
    path: RelPath
    start_line: Annotated[int, Field(ge=1)] = 1
    end_line: Annotated[int | None, Field(ge=1)] = None


class Replacement(BaseModel):
    start_marker: Annotated[str, Field(min_length=1, max_length=4096)]
    end_marker: Annotated[str, Field(min_length=1, max_length=4096)]
    content: str


def decode_exec_stdin(stdin: str | None, stdin_base64: str | None) -> str | None:
    """Preserve exact UTF-8 stdin when an MCP intermediary parses JSON-looking strings."""
    if stdin is not None and stdin_base64 is not None:
        raise ToolError("invalid_request", "give only one stdin representation")
    if stdin_base64 is None:
        return stdin
    try:
        raw = base64.b64decode(stdin_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ToolError("invalid_request", "stdin_base64 is invalid") from exc
    if len(raw) > 1_048_576:
        raise ToolError("invalid_request", "stdin is too large")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ToolError("invalid_request", "stdin must be UTF-8 text") from exc


def root_descriptors(config: HostConfig) -> list[dict[str, Any]]:
    def connection(root):
        if root.connection is None:
            return None
        return {
            "space_id": root.connection.space_id,
            "connection_id": root.connection.connection_id,
        }

    result = []
    for root in config.roots:
        locations = []
        for other in config.roots:
            if other.id == root.id or other.connection is None:
                continue
            try:
                relative = (
                    other.root.resolve().relative_to(root.root.resolve()).as_posix()
                )
            except ValueError:
                continue
            locations.append(
                {
                    "root": other.id,
                    "path": relative,
                    "permission": other.permission,
                    "corpus": connection(other),
                }
            )
        result.append(
            {
                "id": root.id,
                "permission": root.permission,
                "execute": root.execute,
                "sources": list(root.sources),
                "corpus": connection(root),
                "locations": locations,
            }
        )
    return result


def create_server(
    config: HostConfig,
    jobs: JobManager,
    transfers: Transfers | None = None,
    workspace: WorkspaceFiles | None = None,
) -> MCPServer:
    transfers = transfers or Transfers(config)
    workspace = workspace or WorkspaceFiles(config)
    server = MCPServer("Host", version=__version__, instructions=SERVER_INSTRUCTIONS)

    @server.tool(
        name="host_capabilities",
        title="Host capabilities",
        description="Return direct host execution details, installed tools, actual paths and network policy.",
        annotations=READ_ONLY,
    )
    async def host_capabilities() -> dict[str, Any]:
        return {
            "version": __version__,
            "limits": {
                **LIMITS,
                "file_bytes": MAX_FILE_BYTES,
                "chunk_bytes": CHUNK_BYTES,
                "trash_days": 30,
            },
            **await execution_capabilities(config),
        }

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
        return {"roots": root_descriptors(config)}

    @server.tool(
        name="host_search",
        title="Search files",
        description=(
            "Search file contents in a root with a regular expression (ripgrep). "
            "paths are root-relative globs. Returns matching lines with context; "
            "truncated=true means a limit was hit."
        ),
        annotations=READ_ONLY,
    )
    async def host_search(
        root: RootId,
        pattern: Annotated[str, Field(min_length=1, max_length=4096)],
        paths: Annotated[list[str] | None, Field(max_length=32)] = None,
        max_results: Annotated[int, Field(ge=1, le=500)] = 100,
        context: Annotated[int, Field(ge=0, le=5)] = 2,
        ignore_vcs: bool = True,
    ) -> dict[str, Any]:
        return await search(
            config.root(root),
            paths=paths or ["**/*"],
            pattern=pattern,
            max_results=max_results,
            context=context,
            ignore_vcs=ignore_vcs,
        )

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
        root: RootId,
        files: Annotated[list[ReadRequest], Field(min_length=1, max_length=32)],
        max_bytes: Annotated[int, Field(ge=1, le=LIMITS["read_bytes"])] = 65_536,
    ) -> dict[str, Any]:
        return read_files(
            config.root(root), [item.model_dump() for item in files], max_bytes
        )

    @server.tool(
        name="host_write",
        title="Write a file",
        description=(
            "Create or replace a file (content), replace the text between two unique "
            "markers (replace), or delete a file (delete=true). A normal write needs "
            'only root, path and content. expected_version (from host_read, or "absent" '
            "for new files) rejects the write when the file changed."
        ),
        annotations=WRITE,
    )
    def host_write(
        root: RootId,
        path: RelPath,
        content: str | None = None,
        replace: Replacement | None = None,
        delete: bool = False,
        expected_version: Annotated[str | None, Field(max_length=256)] = None,
    ) -> dict[str, Any]:
        return write_file(
            config,
            config.root(root),
            path,
            content=content,
            replace=replace.model_dump() if replace else None,
            delete=delete,
            expected_version=expected_version,
        )

    @server.tool(
        name="host_files",
        title="Workspace files",
        description=(
            "List folders, stat a file, create a folder, move or rename, trash, list "
            "trash, or restore. All paths stay within one registered root. Move and "
            "trash require expected_version from stat; restore never overwrites."
        ),
        annotations=WRITE,
    )
    def host_files(
        root: RootId,
        operation: Literal[
            "list", "stat", "mkdir", "move", "trash", "trash_list", "restore"
        ],
        path: RelPath = ".",
        destination: RelPath | None = None,
        expected_version: Annotated[str | None, Field(max_length=256)] = None,
        trash_id: Annotated[str | None, Field(max_length=128)] = None,
        limit: Annotated[int, Field(ge=1, le=200)] = 200,
        cursor: Annotated[str | None, Field(max_length=4096)] = None,
    ) -> dict[str, Any]:
        return workspace.dispatch(
            root,
            operation,
            path=path,
            destination=destination,
            expected_version=expected_version,
            trash_id=trash_id,
            limit=limit,
            cursor=cursor,
        )

    @server.tool(
        name="host_transfer",
        title="Start a file transfer",
        description=(
            "Prepare an authenticated upload or download without returning binary "
            "bytes in MCP. Uploads require size and expected_version (absent for new "
            "files); chunks are at most 8 MiB and total size at most 1 GiB. Use the "
            "returned transfer credential only in HTTP headers, never in URLs or logs."
        ),
        annotations=WRITE,
    )
    def host_transfer(
        root: RootId,
        path: RelPath,
        direction: Literal["upload", "download"],
        size: Annotated[int | None, Field(ge=0, le=MAX_FILE_BYTES)] = None,
        expected_version: Annotated[str | None, Field(max_length=256)] = None,
        sha256: Annotated[str | None, Field(pattern="^[0-9a-fA-F]{64}$")] = None,
    ) -> dict[str, Any]:
        if direction == "upload":
            if size is None or expected_version is None:
                raise ToolError(
                    "invalid_request", "upload requires size and expected_version"
                )
            return transfers.begin_upload(
                config.root(root),
                path,
                size,
                expected_version,
                sha256,
            )
        return transfers.begin_download(config.root(root), path, expected_version)

    @server.tool(
        name="host_exec",
        title="Run a command",
        description=(
            "Run argv or shell directly as the Host user at the root's real host path. "
            "Uses the owner's installed tools, HOME and network; not an OS sandbox. "
            "Use keep_stdin_open for later host_job_input calls; otherwise stdin closes. "
            "Poll host_job when queued or running. profile and https_hosts are retired and rejected."
        ),
        annotations=EXECUTE,
    )
    async def host_exec(
        root: RootId,
        cwd: Annotated[str, Field(max_length=4096)] = ".",
        argv: Annotated[list[str] | None, Field(min_length=1, max_length=256)] = None,
        shell: Annotated[str | None, Field(min_length=1, max_length=65_536)] = None,
        stdin: Annotated[str | None, Field(max_length=1_048_576)] = None,
        stdin_base64: Annotated[str | None, Field(max_length=1_398_104)] = None,
        timeout_s: Annotated[int, Field(ge=1, le=LIMITS["timeout_s"])] = 1800,
        wait_s: Annotated[int, Field(ge=0, le=LIMITS["wait_s"])] = 5,
        profile: Annotated[str | None, Field(min_length=1, max_length=64)] = None,
        https_hosts: list[str] | None = None,
        keep_stdin_open: bool = False,
    ) -> dict[str, Any]:
        job = await jobs.submit(
            config.root(root),
            cwd=cwd,
            argv=argv,
            shell=shell,
            stdin=decode_exec_stdin(stdin, stdin_base64),
            timeout_s=clamp_timeout(timeout_s),
            profile=profile,
            https_hosts=https_hosts,
            keep_stdin_open=keep_stdin_open,
        )
        job = await jobs.wait(job.id, wait_s)
        return {
            "job_id": job.id,
            "status": job.status,
            "exit_code": job.exit_code,
            "stdout_tail": jobs.tail(job, "stdout"),
            "stderr_tail": jobs.tail(job, "stderr"),
            "truncated": job.truncated,
        }

    @server.tool(
        name="host_job",
        title="Job status and output",
        description=(
            "Return a job's status and a slice of its stored stdout or stderr. "
            "offset is a byte position; use next_offset to continue; limit=0 returns "
            "status only. eof is true once the job finished and the output is fully read."
        ),
        annotations=READ_ONLY,
    )
    def host_job(
        job_id: JobId,
        stream: Annotated[str, Field(pattern="^(stdout|stderr)$")] = "stdout",
        offset: Annotated[int, Field(ge=0)] = 0,
        limit: Annotated[int, Field(ge=0, le=LIMITS["job_output_bytes"])] = 65_536,
    ) -> dict[str, Any]:
        job = jobs.store.load(job_id)
        if job is None:
            raise ToolError("not_found", "unknown job")
        return {**JobManager.public(job), **jobs.output(job, stream, offset, limit)}

    @server.tool(
        name="host_job_input",
        title="Send job input",
        description=(
            "Send UTF-8 stdin to a running job created with keep_stdin_open=true. "
            "Set eof=true to close input. Programs may echo received input in their output."
        ),
        annotations=WRITE,
    )
    async def host_job_input(
        job_id: JobId,
        stdin: Annotated[str | None, Field(max_length=1_048_576)] = None,
        stdin_base64: Annotated[str | None, Field(max_length=1_398_104)] = None,
        eof: bool = False,
    ) -> dict[str, Any]:
        data = decode_exec_stdin(stdin, stdin_base64) or ""
        job = await jobs.write_stdin(job_id, data=data, eof=eof)
        return JobManager.public(job)

    @server.tool(
        name="host_job_cancel",
        title="Cancel a job",
        description="Cancel a queued or running job. Finished jobs keep their status.",
        annotations=WRITE,
    )
    async def host_job_cancel(job_id: JobId) -> dict[str, Any]:
        job = await jobs.cancel(job_id)
        return {"job_id": job.id, "status": job.status}

    return server
