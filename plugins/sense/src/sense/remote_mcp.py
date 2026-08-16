"""Authenticated, bounded MCP surface for remote Sense access."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Annotated, Any, Literal

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import TokenVerifier
from mcp.server.auth.routes import build_resource_metadata_url
from mcp.server.auth.settings import AuthSettings
from mcp.server.context import (
    HandlerResult,
    ServerMiddleware,
    ServerRequestContext,
)
from mcp.server.mcpserver import MCPServer
from mcp.server.request_state import RequestStateSecurity
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from . import __version__
from .errors import (
    ConfirmationRequiredError,
    IdempotencyConflictError,
    InvalidDeleteTicketError,
    PreviewReadOnlyError,
    ProfileNotFoundError,
    RevisionConflictError,
    SectionNotFoundError,
    SenseError,
)
from .exposure import _display_text, public_section_id, stored_section_id
from .model import (
    ProfileDocument,
    ProfileSection,
    ToolError,
    ToolFailure,
    ToolResponse,
    ToolSuccess,
)
from .service import SenseService

READ_SCOPE = "sense:read"
UPDATE_SCOPE = "sense:update"
DELETE_SCOPE = "sense:delete"
REMOTE_SERVER_INSTRUCTIONS = (
    "Use Sense only when durable guidance could change an important choice or when the "
    "authenticated user asks to inspect or change it. Some clients defer individual Sense tool "
    "schemas. If a required Sense tool is not currently loaded and the host provides tool discovery, "
    "use that mechanism; in ChatGPT, call api_tool.list_resources with paths=['Sense'] and a concise "
    "query for the needed action before concluding that the capability is unavailable. Do not repeat "
    "discovery after the required schema is loaded. Discovery establishes availability only and "
    "never authorizes a profile update, deletion, or other state-changing action. The current request "
    "and current sources take "
    "priority. Sense informs the judgment but does not supply the wording or structure of the "
    "answer. The remote connection exposes only ordinary sections and omits source locators and "
    "sensitive content. Changes require an explicit request, and deletion requires an exact "
    "preview and host confirmation."
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

_TOOL_SCOPES = {
    "sense_read": (READ_SCOPE,),
    "sense_overview": (READ_SCOPE,),
    "sense_update": (READ_SCOPE, UPDATE_SCOPE),
    "sense_delete_preview": (READ_SCOPE,),
    "sense_delete": (READ_SCOPE, DELETE_SCOPE),
}


def _oauth_meta(*scopes: str) -> dict[str, Any]:
    return {"securitySchemes": [{"type": "oauth2", "scopes": list(scopes)}]}


RemoteReadView = Annotated[
    Literal["index", "sections"],
    Field(
        description=(
            "Use index to discover relevant ordinary sections; use sections only with explicit "
            "section_ids returned by the index."
        )
    ),
]
RemoteSectionId = Annotated[
    str,
    Field(
        min_length=1,
        max_length=64,
        description="Exact ordinary section id returned by sense_read or sense_overview.",
    ),
]
IdempotencyKey = Annotated[
    str,
    Field(
        pattern=r"^[a-zA-Z0-9._:-]{1,160}$",
        description=(
            "Stable unique key for retrying this exact write; reusing it with different content "
            "is rejected."
        ),
    ),
]


class RemotePublicSection(BaseModel):
    """Fields an authenticated remote caller may replace."""

    model_config = ConfigDict(extra="forbid")

    purpose: str = Field(
        min_length=1,
        max_length=320,
        description="Which important choices this section is meant to inform.",
    )
    text: str = Field(
        min_length=1,
        max_length=12_000,
        description="Complete replacement text for the ordinary section.",
    )
    origins: list[Literal["user_set", "learned_from_results"]] = Field(
        min_length=1,
        max_length=2,
        description="Whether the guidance was user-set, learned from observed results, or both.",
    )
    use_for: list[str] = Field(
        default_factory=list,
        max_length=16,
        description="Bounded situations where this guidance should affect future choices.",
    )
    review_when: list[str] = Field(
        default_factory=list,
        max_length=12,
        description="Concrete conditions that should trigger a later review of this guidance.",
    )


class RemoteRequestError(ValueError):
    """Client-visible validation error produced by the bounded remote surface."""


class _SenseScopeMiddleware:
    """Keep standalone remote Sense fail-closed without common hosting."""

    def __init__(self, *, resource: str) -> None:
        self.resource = resource
        self.resource_metadata_url = str(build_resource_metadata_url(resource))

    @staticmethod
    def _error(*, challenge: str | None = None) -> CallToolResult:
        meta = {"mcp/www_authenticate": [challenge]} if challenge else None
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text="This operation is not authorized for the Sense endpoint.",
                )
            ],
            isError=True,
            _meta=meta,
        )

    async def __call__(
        self,
        context: ServerRequestContext[Any, Any],
        call_next: Callable[
            [ServerRequestContext[Any, Any]],
            Awaitable[HandlerResult],
        ],
    ) -> HandlerResult:
        if context.method != "tools/call" or not isinstance(context.params, Mapping):
            return await call_next(context)
        tool = context.params.get("name")
        if not isinstance(tool, str) or tool not in _TOOL_SCOPES:
            return self._error()
        required = _TOOL_SCOPES[tool]
        token = get_access_token()
        granted = frozenset(token.scopes) if token else frozenset()
        if (
            token is None
            or not token.subject
            or token.resource != self.resource
            or not set(required) <= granted
        ):
            challenge = (
                'Bearer error="insufficient_scope", '
                f'scope="{" ".join(required)}", '
                f'resource_metadata="{self.resource_metadata_url}"'
            )
            return self._error(challenge=challenge)
        return await call_next(context)


def _success(result: dict[str, Any]) -> ToolResponse:
    return ToolResponse(ToolSuccess(result=result))


def _failure(
    *,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> ToolResponse:
    return ToolResponse(
        ToolFailure(
            error=ToolError(
                code=code,
                message=message,
                details=details or {},
            )
        )
    )


def _safe_call(operation: Callable[[], dict[str, Any]]) -> ToolResponse:
    try:
        return _success(operation())
    except ProfileNotFoundError:
        return _failure(
            code="profile_not_found",
            message="Sense data is not available for this account",
        )
    except SectionNotFoundError as exc:
        return _failure(
            code=exc.code,
            message="Sense section was not found",
            details=exc.details,
        )
    except RevisionConflictError as exc:
        return _failure(
            code=exc.code,
            message="Sense changed after it was read; read the current revision",
            details={
                key: value
                for key, value in exc.details.items()
                if key in {"current_revision", "section_id"}
            },
        )
    except PreviewReadOnlyError:
        return _failure(
            code="preview_read_only",
            message="Sense preview is read-only until it is activated",
        )
    except ConfirmationRequiredError:
        return _failure(
            code="confirmation_required",
            message="This Sense change requires a trusted local review",
        )
    except IdempotencyConflictError as exc:
        return _failure(code=exc.code, message=str(exc))
    except InvalidDeleteTicketError as exc:
        return _failure(code=exc.code, message=str(exc))
    except RemoteRequestError as exc:
        return _failure(code="invalid_request", message=str(exc))
    except (TypeError, ValueError) as exc:
        return _failure(code="invalid_request", message=str(exc))
    except SenseError as exc:
        return _failure(
            code=exc.code,
            message="Sense is temporarily unavailable",
        )
    except Exception:  # noqa: BLE001 - remote responses must not expose internals
        return _failure(
            code="unexpected_error",
            message="unexpected Sense operation failure",
        )


def _principal_binding(*, resource: str, issuer: str) -> str:
    token = get_access_token()
    if token is None or not token.subject:
        raise RemoteRequestError("authenticated principal is unavailable")
    value = f"{issuer}\0{resource}\0{token.subject}".encode()
    return hashlib.sha256(value).hexdigest()


def _profile_redactions(profile: ProfileDocument) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                value
                for section in profile.sections
                for source in section.source_refs
                for value in (source.locator, source.sha256)
            },
            key=len,
            reverse=True,
        )
    )


def _public_section(
    section: ProfileSection,
    *,
    exact_redactions: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "id": public_section_id(section.id),
        "purpose": _display_text(section.purpose, exact_redactions),
        "text": _display_text(section.text, exact_redactions),
        "origins": [
            "learned_from_results" if value == "learned_from_work" else value
            for value in section.origins
        ],
        "use_for": [_display_text(item, exact_redactions) for item in section.use_for],
        "review_when": [
            _display_text(item, exact_redactions) for item in section.review_when
        ],
    }


def _remote_read(
    service: SenseService,
    *,
    view: RemoteReadView,
    section_ids: list[str] | None,
) -> dict[str, Any]:
    stored = service.store.read()
    exact_redactions = _profile_redactions(stored.profile)
    ordinary_sections = {
        section.id: section
        for section in stored.profile.sections
        if section.sensitivity == "ordinary"
    }
    result: dict[str, Any] = {
        "lifecycle": stored.lifecycle,
        "schema_version": stored.profile.schema_version,
        "revision": stored.profile.revision,
    }
    if view == "index":
        result["sections"] = [
            {
                "id": public_section_id(section.id),
                "purpose": _display_text(section.purpose, exact_redactions),
                "use_for": [
                    _display_text(item, exact_redactions) for item in section.use_for
                ],
            }
            for section in stored.profile.sections
            if section.sensitivity == "ordinary"
        ]
        return result

    if not section_ids:
        raise RemoteRequestError("section_ids are required when view=sections")
    requested = list(dict.fromkeys(section_ids))
    sections: list[dict[str, Any]] = []
    for public_id in requested:
        section_id = stored_section_id(public_id)
        section = ordinary_sections.get(section_id)
        if section is None:
            raise SectionNotFoundError(
                "Sense section was not found",
                details={"section_id": public_id},
            )
        sections.append(_public_section(section, exact_redactions=exact_redactions))
    result["sections"] = sections
    return result


def create_remote_server(
    *,
    service_factory: Callable[[], SenseService],
    token_verifier: TokenVerifier,
    auth: AuthSettings,
    request_state_security: RequestStateSecurity,
    middleware: Sequence[ServerMiddleware[Any]] = (),
) -> MCPServer:
    """Create the remote Sense server around caller-owned auth and tenant routing.

    ``service_factory`` is invoked inside every tool call. The common remote runtime
    therefore resolves the authenticated principal and its Sense namespace for the
    current request instead of binding one data root when the server starts.
    """

    if auth.resource_server_url is None:
        raise ValueError("remote Sense auth must define resource_server_url")
    resource_server_url = str(auth.resource_server_url)
    if READ_SCOPE not in (auth.required_scopes or []):
        raise ValueError(f"remote Sense auth must require {READ_SCOPE}")
    if request_state_security.audience != resource_server_url:
        raise ValueError(
            "remote Sense request-state audience must exactly match resource_server_url"
        )
    if request_state_security.bind_principal is None:
        raise ValueError(
            "remote Sense request state must bind the authenticated principal"
        )

    issuer = str(auth.issuer_url)
    server_middleware = [
        *middleware,
        _SenseScopeMiddleware(resource=resource_server_url),
    ]

    server = MCPServer(
        "Sense",
        version=__version__,
        instructions=REMOTE_SERVER_INSTRUCTIONS,
        token_verifier=token_verifier,
        auth=auth,
        middleware=server_middleware,
        request_state_security=request_state_security,
    )

    @server.tool(
        name="sense_read",
        title="Read Sense",
        description=(
            "Use this when retained Sense guidance could change an important choice, or when the "
            "user asks what Sense contains. Start with view=index, then read only the relevant "
            "section ids. The result includes the current revision but omits sensitive sections, "
            "source locators, local paths, revision history, and section digests."
        ),
        annotations=READ_ONLY,
        meta={**_oauth_meta(READ_SCOPE), "ui": {"visibility": ["model"]}},
    )
    def sense_read(
        view: RemoteReadView = "index",
        section_ids: Annotated[
            list[RemoteSectionId] | None,
            Field(max_length=12),
        ] = None,
    ) -> ToolResponse:
        return _safe_call(
            lambda: _remote_read(
                service_factory(),
                view=view,
                section_ids=section_ids,
            )
        )

    @server.tool(
        name="sense_overview",
        title="Show Sense",
        description=(
            "Use this when the user asks to review all ordinary guidance kept in Sense. The result "
            "omits sensitive sections, source locators, local paths, revision history, and change "
            "tokens."
        ),
        annotations=READ_ONLY,
        meta={**_oauth_meta(READ_SCOPE), "ui": {"visibility": ["model"]}},
    )
    def sense_overview() -> ToolResponse:
        return _safe_call(lambda: service_factory().overview())

    @server.tool(
        name="sense_update",
        title="Update Sense Section",
        description=(
            "Use this after an explicit correction or observed result establishes guidance that "
            "should remain useful in other contexts. Do not store project facts, one-project "
            "notes, or user-model relations that belong in Hypes. Replace only the public fields "
            "of one existing ordinary section using the current revision and a unique idempotency "
            "key. Hidden fields remain unchanged."
        ),
        annotations=WRITE,
        meta={
            **_oauth_meta(READ_SCOPE, UPDATE_SCOPE),
            "ui": {"visibility": ["model"]},
        },
    )
    def sense_update(
        expected_revision: Annotated[
            int,
            Field(
                ge=1,
                description="Exact Sense revision returned by the preceding read.",
            ),
        ],
        idempotency_key: IdempotencyKey,
        section_id: RemoteSectionId,
        previous_understanding: Annotated[
            str,
            Field(
                min_length=1,
                max_length=2000,
                description="Concise statement of the section guidance that is being corrected.",
            ),
        ],
        changed_future_judgment: Annotated[
            str,
            Field(
                min_length=1,
                max_length=2000,
                description=(
                    "How this correction should change important choices in other contexts."
                ),
            ),
        ],
        updated_section: RemotePublicSection,
    ) -> ToolResponse:
        return _safe_call(
            lambda: service_factory().remote_update(
                expected_revision=expected_revision,
                idempotency_key=idempotency_key,
                principal_binding=_principal_binding(
                    resource=resource_server_url,
                    issuer=issuer,
                ),
                section_id=section_id,
                previous_understanding=previous_understanding,
                changed_future_judgment=changed_future_judgment,
                public_fields={
                    **updated_section.model_dump(mode="json"),
                    "origins": [
                        "learned_from_work"
                        if value == "learned_from_results"
                        else value
                        for value in updated_section.origins
                    ],
                },
            )
        )

    @server.tool(
        name="sense_delete_preview",
        title="Preview Sense Deletion",
        description=(
            "Use this when the user asks to remove one ordinary Sense section. It shows the exact "
            "section that would be removed from the current data and retained revisions, then "
            "returns a short-lived signed ticket. Read-only; source refs, sensitive content, "
            "paths, and section digests are omitted."
        ),
        annotations=READ_ONLY,
        meta={
            **_oauth_meta(READ_SCOPE),
            "ui": {"visibility": ["model"]},
        },
    )
    def sense_delete_preview(section_id: RemoteSectionId) -> ToolResponse:
        def preview() -> dict[str, Any]:
            result = service_factory().remote_delete_preview(
                section_id=section_id,
                principal_binding=_principal_binding(
                    resource=resource_server_url,
                    issuer=issuer,
                ),
            )
            section = result.pop("section")
            redactions = tuple(
                sorted(
                    {
                        value
                        for source in section.source_refs
                        for value in (source.locator, source.sha256)
                    },
                    key=len,
                    reverse=True,
                )
            )
            result["section"] = _public_section(
                section,
                exact_redactions=redactions,
            )
            return result

        return _safe_call(preview)

    @server.tool(
        name="sense_delete",
        title="Delete Sense Section",
        description=(
            "Use this only after sense_delete_preview and host confirmation for that exact "
            "preview. It permanently removes the ordinary section from current data and retained "
            "revisions. The unmodified short-lived ticket, exact revision, and a unique "
            "idempotency key are required; model text cannot supply confirmation."
        ),
        annotations=DELETE,
        meta={
            **_oauth_meta(READ_SCOPE, DELETE_SCOPE),
            "ui": {"visibility": ["model"]},
        },
    )
    def sense_delete(
        expected_revision: Annotated[int, Field(ge=1)],
        delete_ticket: Annotated[str, Field(min_length=32, max_length=32768)],
        idempotency_key: IdempotencyKey,
    ) -> ToolResponse:
        return _safe_call(
            lambda: service_factory().remote_delete(
                expected_revision=expected_revision,
                idempotency_key=idempotency_key,
                principal_binding=_principal_binding(
                    resource=resource_server_url,
                    issuer=issuer,
                ),
                delete_ticket=delete_ticket,
            )
        )

    return server


__all__ = ["DELETE_SCOPE", "READ_SCOPE", "UPDATE_SCOPE", "create_remote_server"]
