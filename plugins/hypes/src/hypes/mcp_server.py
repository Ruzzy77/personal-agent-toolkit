"""Stateless MCP surface for the Hypes personal cognitive model."""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from . import __version__
from .errors import HypesError
from .model import (
    RecheckBasis,
    RelationDraft,
    RetentionBasis,
    ToolError,
    ToolFailure,
    ToolResponse,
    ToolSuccess,
)
from .service import HypesService

SERVER_INSTRUCTIONS = (
    "Hypes adapts explanations using scoped, revisable clues about what the user understands and "
    "which explanations helped. Read one exact topic, task, and responsibility scope by default; "
    "inherit broader scopes only when explicitly requested. Never infer knowledge, "
    "fatigue, agreement, preference, personality, health, or ability from silence or short replies. "
    "Do not store transcripts, full answers, reasoning, sensitive traits, Sense guidance, Corpus "
    "sources, or project facts. Keep provisional understanding in the current conversation. At a "
    "task completion, handoff, or material conclusion, use hypes_revise automatically only when a "
    "compact relation is stable, reusable, narrowly scoped, and likely to change a future explanation; "
    "do not ask whether to save it. Use hypes_mark_recheck to suspend an existing active relation when "
    "current evidence conflicts, without storing the competing claim or conversation. Read the current "
    "revision before a write or deletion and use an idempotency key for every write. The MCP transport is "
    "sessionless; no call may depend on a previous connection or server process."
)

READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
WRITE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
DELETE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=True,
    openWorldHint=False,
)


def _safe_call(operation: Callable[[], Any]) -> ToolResponse:
    try:
        return ToolResponse(ToolSuccess(result=operation()))
    except HypesError as exc:
        return ToolResponse(
            ToolFailure(
                error=ToolError(
                    code=exc.code,
                    message=str(exc),
                    details=exc.details,
                )
            )
        )
    except (TypeError, ValueError) as exc:
        return ToolResponse(
            ToolFailure(
                error=ToolError(code="invalid_request", message=str(exc), details={})
            )
        )
    except Exception:  # noqa: BLE001 - tool responses must not expose internals
        return ToolResponse(
            ToolFailure(
                error=ToolError(
                    code="unexpected_error",
                    message="unexpected Hypes operation failure",
                    details={},
                )
            )
        )


def create_server(data_root: Path | None = None) -> MCPServer:
    service = HypesService(data_root)
    server = MCPServer("Hypes", version=__version__, instructions=SERVER_INSTRUCTIONS)

    @server.tool(
        name="hypes_read",
        title="Read Cognitive Model",
        description=(
            "Read active explanation clues for one exact topic, task, and responsibility scope. "
            "Missing task or responsibility values are part of that exact scope, not wildcards. "
            "Set include_broader only when broader-scope inheritance is appropriate. Recheck-due "
            "items are omitted unless explicitly "
            "requested for inspection. Treat results as revisable clues, not facts about the whole "
            "person. This does not modify the model."
        ),
        annotations=READ_ONLY,
    )
    def hypes_read(
        topic: Annotated[str, Field(min_length=1, max_length=160)],
        task: Annotated[str | None, Field(min_length=1, max_length=160)] = None,
        responsibility: Annotated[
            str | None, Field(min_length=1, max_length=160)
        ] = None,
        include_broader: bool = False,
        include_recheck: bool = False,
        limit: Annotated[int, Field(ge=1, le=50)] = 20,
    ) -> ToolResponse:
        return _safe_call(
            lambda: service.read(
                topic=topic,
                task=task,
                responsibility=responsibility,
                include_broader=include_broader,
                include_recheck=include_recheck,
                limit=limit,
            )
        )

    @server.tool(
        name="hypes_mark_recheck",
        title="Suspend a Cognitive Relation",
        description=(
            "Stop one existing active relation from shaping answers when an explicit correction, "
            "incompatible application outcome, or current-conversation conflict makes it unreliable. "
            "Store only the bounded reason, never the competing claim, transcript, full answer, or hidden "
            "reasoning. Read first and supply the server-derived relation ref, exact revision, and a unique "
            "idempotency key."
        ),
        annotations=WRITE,
    )
    def hypes_mark_recheck(
        expected_revision: Annotated[int, Field(ge=0)],
        idempotency_key: Annotated[str, Field(pattern=r"^[a-zA-Z0-9._:-]{1,160}$")],
        relation_ref: Annotated[
            str, Field(pattern=r"^rel_[a-f0-9]{64}$")
        ],
        recheck_basis: RecheckBasis,
    ) -> ToolResponse:
        return _safe_call(
            lambda: service.mark_recheck(
                expected_revision=expected_revision,
                idempotency_key=idempotency_key,
                relation_ref_value=relation_ref,
                recheck_basis=recheck_basis,
            )
        )

    @server.tool(
        name="hypes_revise",
        title="Correct Cognitive Model",
        description=(
            "Create, replace, reactivate, or resolve one durable relation. At task completion, handoff, "
            "or a material conclusion, do this automatically when the relation is stable, reusable, "
            "narrowly scoped, non-sensitive, and likely to change a future explanation. Do not ask the "
            "user whether to save it. Never retain silence, brief assent, preferences, agreement, project "
            "facts, transcripts, personality, health, or ability claims. Set retention_basis to distinguish "
            "an explicit request from an agent-selected conversation conclusion. Read first and supply the "
            "exact revision and a unique idempotency key."
        ),
        annotations=WRITE,
    )
    def hypes_revise(
        expected_revision: Annotated[int, Field(ge=0)],
        idempotency_key: Annotated[str, Field(pattern=r"^[a-zA-Z0-9._:-]{1,160}$")],
        relation: RelationDraft,
        retention_basis: RetentionBasis,
        review_in_days: Annotated[int | None, Field(ge=1, le=3650)] = None,
    ) -> ToolResponse:
        return _safe_call(
            lambda: service.revise(
                expected_revision=expected_revision,
                idempotency_key=idempotency_key,
                relation=relation,
                retention_basis=retention_basis,
                review_in_days=review_in_days,
            )
        )

    @server.tool(
        name="hypes_overview",
        title="Inspect Cognitive Model",
        description=(
            "Show bounded retained relations, their exact scopes and refs, and counts for active and "
            "recheck-due states. Use offset pagination to inspect everything. It never "
            "returns raw conversation because none is stored."
        ),
        annotations=READ_ONLY,
    )
    def hypes_overview(
        offset: Annotated[int, Field(ge=0)] = 0,
        limit: Annotated[int, Field(ge=1, le=100)] = 50,
    ) -> ToolResponse:
        return _safe_call(lambda: service.overview(offset=offset, limit=limit))

    @server.tool(
        name="hypes_preview_forget",
        title="Preview Cognitive Model Deletion",
        description=(
            "Show the exact active/recheck relations that would be removed, then mint a short-lived "
            "signed forget ticket. The ticket carries all cross-call state explicitly and does not "
            "create an MCP session. Stored-content digests bind the preview to the exact retained "
            "relations. This preview does not "
            "change the active-model revision."
        ),
        annotations=READ_ONLY,
    )
    def hypes_preview_forget(
        relation_refs: Annotated[list[str] | None, Field(max_length=50)] = None,
    ) -> ToolResponse:
        return _safe_call(
            lambda: service.preview_forget(relation_refs=relation_refs)
        )

    @server.tool(
        name="hypes_forget",
        title="Forget Cognitive Model Relations",
        description=(
            "Remove the exact relation refs shown by "
            "hypes_preview_forget after the user approves that preview. Requires the unmodified "
            "short-lived ticket, its exact model revision, and a unique idempotency key. A "
            "successful call removes the relation statements and explanations from the live "
            "managed SQLite DB, WAL, and SHM files. Filesystem snapshots and external backups "
            "remain subject to the deployment retention policy."
        ),
        annotations=DELETE,
    )
    def hypes_forget(
        expected_revision: Annotated[int, Field(ge=0)],
        forget_ticket: Annotated[str, Field(min_length=32, max_length=32768)],
        idempotency_key: Annotated[str, Field(pattern=r"^[a-zA-Z0-9._:-]{1,160}$")],
    ) -> ToolResponse:
        return _safe_call(
            lambda: service.forget(
                expected_revision=expected_revision,
                forget_ticket=forget_ticket,
                idempotency_key=idempotency_key,
            )
        )

    @server.tool(
        name="hypes_status",
        title="Check Hypes Status",
        description=(
            "Show the running build and distinguish sessionless MCP transport from the explicit "
            "persistent cognitive-model store. HTTP publication remains disabled until an OAuth "
            "resource server binds each request to the authenticated user. Remote writes also need "
            "a dedicated update scope, and the authorized calling surface must enforce Hypes' "
            "automatic-retention gate for every request."
        ),
        annotations=READ_ONLY,
    )
    def hypes_status() -> ToolResponse:
        return _safe_call(service.status)

    return server


mcp = create_server()


def main() -> None:
    transport = os.environ.get("HYPES_MCP_TRANSPORT", "stdio")
    if transport == "stdio":
        mcp.run(transport="stdio")
        return
    if transport != "streamable-http":
        raise ValueError("HYPES_MCP_TRANSPORT must be stdio or streamable-http")
    host = os.environ.get("HYPES_MCP_HOST", "127.0.0.1")
    try:
        loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    if not loopback:
        raise ValueError(
            "HYPES_MCP_HOST must be loopback until an authenticated OAuth resource server is configured"
        )
    mcp.run(
        transport="streamable-http",
        host=host,
        port=int(os.environ.get("HYPES_MCP_PORT", "8000")),
        streamable_http_path=os.environ.get("HYPES_MCP_PATH", "/hypes/mcp"),
        stateless_http=True,
        json_response=True,
    )


if __name__ == "__main__":
    main()
