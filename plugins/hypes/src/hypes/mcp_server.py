"""Stateless MCP surface for Hypes."""

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
    ExplanationClueInput,
    RecheckBasis,
    RetentionBasis,
    ToolError,
    ToolFailure,
    ToolResponse,
    ToolSuccess,
)
from .service import HypesService

SERVER_INSTRUCTIONS = (
    "Hypes keeps narrow, revisable clues about concepts the user has demonstrated and about "
    "explanations whose effect the user confirmed. Use it only when such a clue could materially "
    "change the next explanation or when the user asks to inspect it. The current conversation "
    "comes first. Never infer understanding, agreement, fatigue, preference, personality, health, "
    "or ability from silence or brevity. Store no transcripts, full answers, hidden reasoning, "
    "sensitive traits, project facts, Sense guidance, or Corpus sources. Retain a clue only after a "
    "direct save request, explicit correction, demonstrated application, confirmed explanation "
    "outcome, or repetition across separate conversations. A completed request or an explanation "
    "written by the assistant is not evidence. Mark a conflicting active clue for recheck without "
    "saving the competing claim. Read the current revision before a change and use an idempotency "
    "key for every write."
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
        title="Read Explanation Clues",
        description=(
            "Use this when a retained clue could materially change the current explanation, or "
            "when the user asks what Hypes contains. Read the narrowest matching topic, situation, "
            "and responsibility. Missing scope values are exact, not wildcards. Include broader "
            "scopes only deliberately. Recheck-due clues are omitted unless requested. Results are "
            "revisable clues, not facts about the whole person. Read-only."
        ),
        annotations=READ_ONLY,
    )
    def hypes_read(
        topic: Annotated[str, Field(min_length=1, max_length=160)],
        situation: Annotated[str | None, Field(min_length=1, max_length=160)] = None,
        responsibility: Annotated[
            str | None, Field(min_length=1, max_length=160)
        ] = None,
        include_broader: Annotated[
            bool,
            Field(description="Include deliberately inherited broader scopes when true."),
        ] = False,
        include_recheck: Annotated[
            bool,
            Field(description="Include suspended recheck-due relations only for inspection."),
        ] = False,
        limit: Annotated[int, Field(ge=1, le=50)] = 20,
    ) -> ToolResponse:
        return _safe_call(
            lambda: service.read(
                topic=topic,
                task=situation,
                responsibility=responsibility,
                include_broader=include_broader,
                include_recheck=include_recheck,
                limit=limit,
            )
        )

    @server.tool(
        name="hypes_mark_recheck",
        title="Pause an Explanation Clue",
        description=(
            "Use this when current conversation evidence conflicts with one active clue. The basis "
            "must be an explicit correction, incompatible application outcome, or direct conflict. "
            "Store only the bounded reason, never the competing claim, transcript, full answer, or "
            "hidden reasoning. Read first and supply the exact ref, revision, and a unique "
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
        title="Save an Explanation Clue",
        description=(
            "Use this only after a direct save request, explicit correction, demonstrated "
            "application, confirmed explanation outcome, or repetition across separate "
            "conversations. Save one compact exact-scope clue likely to change a later explanation. "
            "A completed request, short assent, or explanation written by the assistant is not "
            "evidence. Never retain preferences, agreement, project facts, transcripts, personality, "
            "health, or ability claims. Read first and supply the exact revision and a unique "
            "idempotency key."
        ),
        annotations=WRITE,
    )
    def hypes_revise(
        expected_revision: Annotated[int, Field(ge=0)],
        idempotency_key: Annotated[str, Field(pattern=r"^[a-zA-Z0-9._:-]{1,160}$")],
        relation: ExplanationClueInput,
        retention_basis: Annotated[
            RetentionBasis,
            Field(
                description=(
                    "Name the visible evidence: explicit_user_request, explicit_user_correction, "
                    "demonstrated_application, confirmed_explanation_outcome, or "
                    "repeated_across_conversations."
                )
            ),
        ],
        review_in_days: Annotated[
            int | None,
            Field(
                ge=1,
                le=3650,
                description="Optional bounded interval before this relation becomes due for review.",
            ),
        ] = None,
    ) -> ToolResponse:
        return _safe_call(
            lambda: service.revise(
                expected_revision=expected_revision,
                idempotency_key=idempotency_key,
                relation=relation.to_relation_draft(),
                retention_basis=retention_basis,
                review_in_days=review_in_days,
            )
        )

    @server.tool(
        name="hypes_overview",
        title="Show Explanation Clues",
        description=(
            "Use this when the user asks what Hypes retains or needs exact refs for removal. It "
            "shows bounded clues, exact scopes and refs, and active or recheck-due counts. Use "
            "pagination to inspect everything. Raw conversation is never returned because none is "
            "stored."
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
        title="Preview Hypes Removal",
        description=(
            "Use this when the user asks to remove retained Hypes clues. It shows the exact active "
            "or recheck-due clues and returns a short-lived signed ticket bound to their stored "
            "content. The preview is read-only."
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
        title="Remove Hypes Clues",
        description=(
            "Use this only after hypes_preview_forget and host confirmation for that exact preview. "
            "It removes the shown refs using the unmodified short-lived ticket, exact revision, and "
            "a unique idempotency key. External filesystem snapshots and backups remain outside "
            "this deletion boundary."
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
        title="Check Hypes",
        description=(
            "Use this when the user asks about the running Hypes version or when the connection "
            "needs diagnosis. It distinguishes the sessionless connection from the persistent "
            "private store and reports whether remote publication remains disabled."
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
