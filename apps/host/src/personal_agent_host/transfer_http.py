"""Authenticated, bounded binary transfer and isolated preview responses."""
from __future__ import annotations

import asyncio
import html
import re
from html.parser import HTMLParser
from urllib.parse import quote

from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from personal_agent_host.files import ToolError
from personal_agent_host.transfers import CHUNK_BYTES, Transfers

TOKEN_HEADER = "X-Toolkit-Transfer-Token"
PREVIEW_TEXT_BYTES = 2 * 1024 * 1024
PREVIEW_CSP = (
    "sandbox; default-src 'none'; script-src 'none'; connect-src 'none'; "
    "img-src data:; media-src 'none'; font-src data:; style-src 'unsafe-inline'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
)
SAFE_HEADERS = {
    "Cache-Control": "private, no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": PREVIEW_CSP,
}


class PreviewHTML(HTMLParser):
    """Preserve passive document markup, never navigation or active content."""
    allowed = frozenset(['html', 'head', 'body', 'title', 'style', 'main', 'article', 'section', 'header', 'footer', 'nav', 'aside', 'div', 'span', 'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'ul', 'ol', 'li', 'dl', 'dt', 'dd', 'blockquote', 'pre', 'code', 'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'caption', 'colgroup', 'col', 'br', 'hr', 'strong', 'em', 'b', 'i', 'u', 's', 'small', 'sub', 'sup', 'mark', 'a', 'img', 'figure', 'figcaption', 'details', 'summary'])
    attributes = frozenset(['class', 'id', 'title', 'style', 'lang', 'dir', 'colspan', 'rowspan', 'scope', 'width', 'height', 'alt', 'open'])

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.output: list[str] = []
        self.blocked = 0
        self.in_style = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "iframe", "object", "embed", "svg", "math", "template"}:
            self.blocked += 1
            return
        if self.blocked or tag not in self.allowed:
            return
        safe: list[str] = []
        for name, value in attrs:
            if value is None:
                if name == "open":
                    safe.append("open")
            elif name in self.attributes or (
                tag == "img" and name == "src"
                and re.match(r"^data:image/(png|jpeg|gif|webp|avif);base64,", value, re.IGNORECASE)
            ):
                safe.append(f'{name}="{html.escape(value, quote=True)}"')
        self.output.append("<" + tag + (" " + " ".join(safe) if safe else "") + ">")
        if tag == "style":
            self.in_style = True

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "iframe", "object", "embed", "svg", "math", "template"}:
            self.blocked = max(0, self.blocked - 1)
            return
        if not self.blocked and tag in self.allowed:
            self.output.append("</" + tag + ">")
        if tag == "style":
            self.in_style = False

    def handle_data(self, data: str) -> None:
        if not self.blocked:
            self.output.append(data.replace("<", "\\3c ") if self.in_style else html.escape(data))


def passive_html(source: str) -> str:
    parser = PreviewHTML()
    parser.feed(source)
    parser.close()
    return '<!doctype html><meta charset="utf-8">' + "".join(parser.output)


def error_response(error: ToolError) -> JSONResponse:
    status = {
        "not_found": 404, "policy_denied": 403, "version_conflict": 409,
        "offset_conflict": 409, "incomplete": 409, "too_large": 413,
        "invalid_range": 416,
    }.get(error.code, 400)
    return JSONResponse(
        {"error": {"code": error.code, "message": str(error)}},
        status_code=status, headers=SAFE_HEADERS,
    )


class TransferHTTP:
    def __init__(self, transfers: Transfers) -> None:
        self.transfers = transfers

    def routes(self) -> list[Route]:
        return [Route(
            "/transfers/{transfer_id:str}/{action:str}", self.handle,
            methods=["GET", "PUT", "POST", "DELETE"],
        )]

    async def handle(self, request: Request) -> Response:
        identifier = request.path_params["transfer_id"]
        action = request.path_params["action"]
        token = request.headers.get(TOKEN_HEADER, "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{12,80}", identifier) or not 32 <= len(token) <= 128:
            return error_response(ToolError("not_found", "transfer is unavailable"))
        try:
            if request.method == "GET" and action == "status":
                result = await asyncio.to_thread(self.transfers.status, identifier, token)
                return JSONResponse(result, headers=SAFE_HEADERS)
            if request.method == "PUT" and action == "chunk":
                if request.headers.get("Content-Type", "").split(";")[0] != "application/octet-stream":
                    raise ToolError("invalid_request", "binary content type is required")
                offset = int(request.headers.get("Upload-Offset", "-1"))
                payload = bytearray()
                async for part in request.stream():
                    if len(payload) + len(part) > CHUNK_BYTES:
                        raise ToolError("too_large", "chunk exceeds 8 MiB")
                    payload.extend(part)
                result = await asyncio.to_thread(
                    self.transfers.write_chunk, identifier, token, offset, bytes(payload),
                )
                return JSONResponse(result, headers=SAFE_HEADERS)
            if request.method == "POST" and action == "commit":
                result = await asyncio.to_thread(self.transfers.commit, identifier, token)
                return JSONResponse(result, headers=SAFE_HEADERS)
            if request.method == "DELETE" and action == "status":
                await asyncio.to_thread(self.transfers.cancel, identifier, token)
                return Response(status_code=204, headers=SAFE_HEADERS)
            if request.method == "GET" and action == "content":
                return await self.content(request, identifier, token)
            return Response(status_code=405, headers=SAFE_HEADERS)
        except ToolError as error:
            return error_response(error)
        except (ValueError, UnicodeError):
            return error_response(ToolError("invalid_request", "invalid transfer request"))

    async def content(self, request: Request, identifier: str, token: str) -> Response:
        info = await asyncio.to_thread(self.transfers.download_info, identifier, token)
        size = int(info["size"])
        path = info["path"]
        preview = request.query_params.get("preview") == "1"
        mime = str(info["mime"])
        headers = {
            **SAFE_HEADERS, "ETag": '"' + str(info["version"]) + '"',
            "Accept-Ranges": "bytes",
            "Content-Disposition": ("inline" if preview else "attachment")
                + "; filename*=UTF-8''" + quote(path.name, safe=""),
        }
        if preview and path.suffix.lower() in {".html", ".htm"}:
            if size > PREVIEW_TEXT_BYTES:
                raise ToolError("too_large", "HTML preview exceeds 2 MiB; download the file")
            data = await asyncio.to_thread(
                self.transfers.read_chunk, identifier, token, 0, PREVIEW_TEXT_BYTES,
            )
            return Response(
                passive_html(data.decode("utf-8")), media_type="text/html",
                headers=headers,
            )
        if preview and not (
            mime in {"image/png", "image/jpeg", "image/gif", "image/webp", "image/avif", "application/pdf"}
            or mime.startswith("text/")
        ):
            mime = "application/octet-stream"
            headers["Content-Disposition"] = "attachment; filename*=UTF-8''" + quote(path.name, safe="")
        if preview and mime.startswith("text/"):
            mime = "text/plain; charset=utf-8"
            if size > PREVIEW_TEXT_BYTES:
                raise ToolError("too_large", "text preview exceeds 2 MiB; download the file")
        start, end, status = 0, size - 1, 200
        range_value = request.headers.get("Range")
        if range_value:
            match = re.fullmatch(r"bytes=(\d+)-(\d*)", range_value)
            if not match:
                raise ToolError("invalid_range", "one explicit byte range is required")
            start = int(match[1])
            end = min(int(match[2]) if match[2] else size - 1, size - 1, start + CHUNK_BYTES - 1)
            if start >= size or end < start:
                raise ToolError("invalid_range", "range is outside the file")
            status = 206
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        headers["Content-Length"] = str(max(0, end - start + 1))

        async def stream():
            offset = start
            while offset <= end:
                data = await asyncio.to_thread(
                    self.transfers.read_chunk, identifier, token, offset,
                    min(CHUNK_BYTES, end - offset + 1),
                )
                if not data:
                    raise OSError("download changed during streaming")
                yield data
                offset += len(data)

        return StreamingResponse(stream(), status_code=status, media_type=mime, headers=headers)
