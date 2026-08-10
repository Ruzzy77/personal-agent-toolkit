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

from .errors import HypesError
from .model import (
    EvidenceKind,
    RelationDraft,
    ToolError,
    ToolFailure,
    ToolResponse,
    ToolSuccess,
)
from .service import HypesService

SERVER_INSTRUCTIONS = (
    "Hypes adapts explanations using scoped, revisable clues about what the user understands and "
    "which explanations helped. Read only for the current topic and task. Never infer knowledge, "
    "fatigue, agreement, preference, personality, health, or ability from silence or short replies. "
    "Do not store transcripts, full answers, reasoning, sensitive traits, Sense guidance, Corpus "
    "sources, or project facts. Pending observations do not affect answers. Use hypes_observe only "
    "for a compact relation that could change a later explanation; explicit corrections and applied "
    "outcomes may become active immediately, while ordinary observations require repetition across "
    "distinct episodes. Use hypes_revise only after the user explicitly corrects the stored relation. "
    "Each write requires the current revision and an idempotency key. The MCP transport is "
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
    server = MCPServer("Hypes", version="0.3.0", instructions=SERVER_INSTRUCTIONS)

    @server.tool(
        name="hypes_read",
        title="Read Cognitive Model",
        description=(
            "Read active explanation clues for one explicit topic, with optional task and "
            "responsibility narrowing. Pending observations are never returned. Recheck-due items "
            "are omitted unless requested. Treat results as revisable clues, not facts about the "
            "whole person. This does not modify the model."
        ),
        annotations=READ_ONLY,
    )
    def hypes_read(
        topic: Annotated[str, Field(min_length=1, max_length=160)],
        task: Annotated[str | None, Field(min_length=1, max_length=160)] = None,
        responsibility: Annotated[str | None, Field(min_length=1, max_length=160)] = None,
        include_recheck: bool = False,
        limit: Annotated[int, Field(ge=1, le=50)] = 20,
    ) -> ToolResponse:
        return _safe_call(
            lambda: service.read(
                topic=topic,
                task=task,
                responsibility=responsibility,
                include_recheck=include_recheck,
                limit=limit,
            )
        )

    @server.tool(
        name="hypes_observe",
        title="Record Explanation Evidence",
        description=(
            "Store one compact, scoped observation that could improve a later explanation. Do not "
            "send a transcript, full answer, hidden reasoning, preference, agreement, health or "
            "ability claim. user_statement remains pending. explicit_correction or applied_outcome "
            "may activate immediately. repeated_observation activates only after matching evidence "
            "arrives from at least two distinct opaque episode keys. The episode key is stored only "
            "as a keyed digest. Read first and supply its exact revision and a unique idempotency key."
        ),
        annotations=WRITE,
    )
    def hypes_observe(
        expected_revision: Annotated[int, Field(ge=0)],
        observation_id: Annotated[str, Field(pattern=r"^[a-zA-Z0-9._:-]{1,160}$")],
        idempotency_key: Annotated[str, Field(pattern=r"^[a-zA-Z0-9._:-]{1,160}$")],
        episode_key: Annotated[str, Field(min_length=8, max_length=256)],
        evidence_kind: EvidenceKind,
        relation: RelationDraft,
    ) -> ToolResponse:
        return _safe_call(
            lambda: service.observe(
                expected_revision=expected_revision,
                observation_id=observation_id,
                idempotency_key=idempotency_key,
                episode_key=episode_key,
                evidence_kind=evidence_kind,
                relation=relation,
            )
        )

    @server.tool(
        name="hypes_revise",
        title="Correct Cognitive Model",
        description=(
            "Replace or activate one relation only after the user explicitly corrects what Hypes "
            "believes they understand or which explanation helped. Do not use this for inferred "
            "preferences or ordinary assent. Read first and supply its exact revision and a unique "
            "idempotency key."
        ),
        annotations=WRITE,
    )
    def hypes_revise(
        expected_revision: Annotated[int, Field(ge=0)],
        idempotency_key: Annotated[str, Field(pattern=r"^[a-zA-Z0-9._:-]{1,160}$")],
        relation: RelationDraft,
    ) -> ToolResponse:
        return _safe_call(
            lambda: service.revise(
                expected_revision=expected_revision,
                idempotency_key=idempotency_key,
                relation=relation,
            )
        )

    @server.tool(
        name="hypes_overview",
        title="Inspect Cognitive Model",
        description=(
            "Show the current revision, counts by lifecycle state, and topic names so the user can "
            "inspect what Hypes retains. It never returns raw conversation because none is stored."
        ),
        annotations=READ_ONLY,
    )
    def hypes_overview() -> ToolResponse:
        return _safe_call(service.overview)

    @server.tool(
        name="hypes_preview_forget",
        title="Preview Cognitive Model Deletion",
        description=(
            "Show the exact relations that would be removed and mint a short-lived signed forget "
            "ticket. The ticket carries all cross-call state explicitly and does not create an MCP "
            "session. This preview does not change the cognitive-model revision."
        ),
        annotations=READ_ONLY,
    )
    def hypes_preview_forget(
        relation_ids: Annotated[list[str], Field(min_length=1, max_length=50)],
    ) -> ToolResponse:
        return _safe_call(lambda: service.preview_forget(relation_ids))

    @server.tool(
        name="hypes_forget",
        title="Forget Cognitive Model Relations",
        description=(
            "Permanently remove the exact relations and their compact observations after the user "
            "has reviewed hypes_preview_forget. Requires the unmodified short-lived ticket, its "
            "exact model revision, and a unique idempotency key."
        ),
        annotations=DELETE,
    )
    def hypes_forget(
        expected_revision: Annotated[int, Field(ge=0)],
        forget_ticket: Annotated[str, Field(min_length=32, max_length=8192)],
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
            "resource server binds each request to the authenticated user."
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
