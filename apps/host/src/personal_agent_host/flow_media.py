"""Authenticated, read-only media bridge for registered Flow workspaces."""

from __future__ import annotations

import asyncio
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request as URLRequest, build_opener

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from personal_agent_host.config import HostConfig

MAX_MEDIA = 65 * 1024 * 1024
MAX_TEXT_JSON = 4 * 1024 * 1024
MAX_TEXT_CONTENT = 512 * 1024
_ASSET = re.compile(r"[a-f0-9]{64}\.(?:png|jpg|webp|gif|mp3|wav|ogg|mp4|webm|m4a|aac|pdf)")
_EXAMPLE = re.compile(r"[A-Za-z0-9._-]+\.(?:png|jpe?g|webp|gif|mp4|webm|mp3|wav|ogg|m4a|aac|csv)")
_RANGE = re.compile(r"bytes=\d*-\d*")
_PAGE = re.compile(r"(?:[1-9]\d{0,3}|10000)")
_ALLOWED_TYPES = frozenset({
    "image/png", "image/jpeg", "image/webp", "image/gif",
    "video/mp4", "video/webm", "audio/mpeg", "audio/wav", "audio/ogg", "audio/mp4", "audio/aac", "text/csv", "application/pdf",
})


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


_OPENER = build_opener(ProxyHandler({}), _NoRedirect())


def media_target(config: HostConfig, workspace_id: str, kind: str, name: str, path: str | None, page: str | None = None) -> str:
    if page is not None and (kind != "files" or name != "preview" or not _PAGE.fullmatch(page)):
        raise ValueError("Flow media page is invalid")
    policy = next((item for item in config.flows if item.id == workspace_id), None)
    if policy is None:
        raise ValueError("Flow workspace is not registered")
    origin = policy.service_url
    if kind == "assets" and _ASSET.fullmatch(name):
        return f"{origin}/api/flow/assets/{name}"
    if kind == "examples" and _EXAMPLE.fullmatch(name):
        return f"{origin}/examples/{name}"
    if kind == "files" and name in ("content", "preview") and path and len(path) <= 2048:
        query = {'workspaceId': workspace_id, 'path': path}
        if page is not None:
            query['page'] = page
        return f"{origin}/api/flow/files/{name}?{urlencode(query)}"
    raise ValueError("Flow media path is invalid")


def _read_media(url: str, method: str, byte_range: str | None):
    headers = {}
    if byte_range:
        if len(byte_range) > 80 or not _RANGE.fullmatch(byte_range):
            raise ValueError("Invalid media range")
        headers["Range"] = byte_range
    request = URLRequest(url, method=method, headers=headers)
    try:
        response = _OPENER.open(request, timeout=10)
    except HTTPError as error:
        if error.code in (404, 413, 415, 416):
            return error.code, {}, b""
        return 502, {}, b""
    with response:
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
        address = urlsplit(url)
        text_result = (
            address.path == "/api/flow/files/content"
            and content_type == "application/json"
            and byte_range is None
        )
        if content_type not in _ALLOWED_TYPES and not text_result:
            return 415, {}, b""
        limit = MAX_TEXT_JSON if text_result else MAX_MEDIA
        body = response.read(limit + 1) if method == "GET" else b""
        if len(body) > limit:
            return 413, {}, b""
        if text_result and method == "GET":
            try:
                result = json.loads(body)
                requested = parse_qs(address.query).get("path", [None])[0]
                content = result["content"]
                if (
                    result.get("type") != "text/plain"
                    or result.get("path") != requested
                    or not isinstance(content, str)
                    or len(content.encode("utf-8")) > MAX_TEXT_CONTENT
                ):
                    return 415, {}, b""
            except (ValueError, TypeError, KeyError, AttributeError):
                return 415, {}, b""
        outgoing = {"Content-Type": content_type}
        for name in ("Content-Range", "Accept-Ranges"):
            value = response.headers.get(name)
            if value:
                outgoing[name] = value
        pages = response.headers.get("X-Flow-Pdf-Pages")
        if pages and _PAGE.fullmatch(pages):
            outgoing["X-Flow-Pdf-Pages"] = pages
        if method == "HEAD":
            size = response.headers.get("Content-Length")
            if size and size.isdecimal() and int(size) <= MAX_MEDIA:
                outgoing["Content-Length"] = size
        return response.status, outgoing, body


class FlowMediaHTTP:
    def __init__(self, config: HostConfig):
        self.config = config

    def routes(self) -> list[Route]:
        return [Route("/flow-media/{workspace_id}/{kind}/{name}", self.handle, methods=["GET", "HEAD"])]

    async def handle(self, request: Request) -> Response:
        try:
            url = media_target(
                self.config,
                request.path_params["workspace_id"],
                request.path_params["kind"],
                request.path_params["name"],
                request.query_params.get("path"),
                request.query_params.get("page"),
            )
            status, headers, body = await asyncio.to_thread(
                _read_media, url, request.method, request.headers.get("range")
            )
        except ValueError:
            status, headers, body = 404, {}, b""
        except (URLError, TimeoutError):
            status, headers, body = 503, {}, b""
        headers.update({
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'",
            "Referrer-Policy": "no-referrer",
        })
        if headers.get("Content-Type") == "text/csv":
            headers["Content-Disposition"] = "attachment"
        return Response(body, status_code=status, headers=headers)
