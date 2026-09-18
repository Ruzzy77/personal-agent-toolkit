"""Host MCP tools: the eight `host_*` tools over the configured roots."""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from personal_agent_host import __version__
from personal_agent_host.config import LIMITS, HostConfig
from personal_agent_host.files import ToolError, read_files, search, write_file
from personal_agent_host.jobs import JobManager, clamp_timeout

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


def create_server(config: HostConfig, jobs: JobManager) -> MCPServer:
    server = MCPServer("Host", version=__version__, instructions=SERVER_INSTRUCTIONS)

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
        name="host_exec",
        title="Run a command",
        description=(
            "Run a command in a sandbox container with the root mounted at /workspace. "
            "Give argv (preferred) or shell. Waits up to wait_s; if the job is still "
            "queued or running, poll host_job with the returned job_id."
        ),
        annotations=WRITE,
    )
    async def host_exec(
        root: RootId,
        cwd: Annotated[str, Field(max_length=4096)] = ".",
        argv: Annotated[list[str] | None, Field(min_length=1, max_length=256)] = None,
        shell: Annotated[str | None, Field(min_length=1, max_length=65_536)] = None,
        stdin: Annotated[str | None, Field(max_length=1_048_576)] = None,
        timeout_s: Annotated[int, Field(ge=1, le=LIMITS["timeout_s"])] = 1800,
        wait_s: Annotated[int, Field(ge=0, le=LIMITS["wait_s"])] = 5,
    ) -> dict[str, Any]:
        job = await jobs.submit(
            config.root(root),
            cwd=cwd,
            argv=argv,
            shell=shell,
            stdin=stdin,
            timeout_s=clamp_timeout(timeout_s),
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
        name="host_job_cancel",
        title="Cancel a job",
        description="Cancel a queued or running job. Finished jobs keep their status.",
        annotations=WRITE,
    )
    async def host_job_cancel(job_id: JobId) -> dict[str, Any]:
        job = await jobs.cancel(job_id)
        return {"job_id": job.id, "status": job.status}

    return server
