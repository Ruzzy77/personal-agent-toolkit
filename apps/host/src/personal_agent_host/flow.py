"""Constrained local transport for registered Toolkit Flow workspaces."""

from __future__ import annotations

import asyncio
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from personal_agent_host.config import HostConfig
from personal_agent_host.files import ToolError, display_path, file_version, relative_path

MAX_BODY = 8 * 1024 * 1024
MAX_IMAGE = 64 * 1024 * 1024
_IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".webp": "image/webp", ".gif": "image/gif", ".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg", ".mp4": "video/mp4", ".webm": "video/webm", ".m4a": "audio/mp4", ".aac": "audio/aac", ".pdf": "application/pdf"}
_FORBIDDEN = frozenset({".git", ".data", ".ssh", ".codex", "node_modules"})


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


_OPENER = build_opener(ProxyHandler({}), _NoRedirect())


def _policy(config: HostConfig, workspace_id: str, *, write: bool):
    policy = next((item for item in config.flows if item.id == workspace_id), None)
    if policy is None:
        raise ToolError("not_found", "Flow workspace is not registered")
    root = config.root(policy.root_id)
    if write and root.permission != "read_write":
        raise ToolError("policy_denied", "Flow workspace is read-only")
    return policy


def _request(config: HostConfig, workspace_id: str, method: str, path: str,
             payload: dict | None = None, query: dict | None = None) -> dict:
    policy = _policy(config, workspace_id, write=method not in ("GET", "HEAD"))
    url = policy.service_url + "/api/flow/" + path
    if query:
        url += "?" + urlencode(query)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    if body is not None and len(body) > MAX_BODY:
        raise ToolError("invalid_request", "Flow payload is too large")
    request = Request(
        url, data=body, method=method,
        headers={"X-Toolkit-Flow": "1", "Content-Type": "application/json"},
    )
    try:
        with _OPENER.open(request, timeout=10) as response:
            raw = response.read(MAX_BODY + 1)
            if len(raw) > MAX_BODY:
                raise ToolError("invalid_response", "Flow 응답이 너무 큽니다")
            return json.loads(raw)
    except HTTPError as error:
        try:
            detail = json.loads(error.read(4096)).get("error", "Flow request failed")
        except (ValueError, UnicodeError):
            detail = "Flow request failed"
        code = {403: "policy_denied", 404: "not_found", 409: "version_conflict", 422: "invalid_request"}.get(error.code, "flow_error")
        raise ToolError(code, detail) from error
    except (URLError, TimeoutError, ValueError, UnicodeError) as error:
        raise ToolError("flow_unavailable", "등록된 Flow 서비스에 연결하지 못했습니다") from error


async def call_flow(config: HostConfig, workspace_id: str, method: str, path: str,
                    payload: dict | None = None, query: dict | None = None) -> dict:
    return await asyncio.to_thread(_request, config, workspace_id, method, path, payload, query)


def list_registered(config: HostConfig) -> dict:
    return {
        "workspaces": [
            {"id": item.id, "permission": config.root(item.root_id).permission, "root_id": item.root_id}
            for item in config.flows
        ]
    }


def path_id(value: str) -> str:
    if not value or len(value) > 160 or "/" in value or "\\" in value:
        raise ToolError("invalid_request", "ID is invalid")
    return quote(value, safe="")


def _import_asset(config: HostConfig, workspace_id: str, root: str, path: str, expected_version: str) -> dict:
    policy = _policy(config, workspace_id, write=True)
    if root != policy.root_id:
        raise ToolError("policy_denied", "등록된 Flow 작업공간의 파일이 아닙니다")
    registered_root = config.root(root)
    parts = path.replace("\\", "/").split("/")
    if any(part.startswith(".") or part in _FORBIDDEN for part in parts):
        raise ToolError("invalid_path", "이 이미지 경로는 사용할 수 없습니다")
    target = relative_path(registered_root.root, path)
    relative = display_path(registered_root.root, target)
    if any(part.startswith(".") or part in _FORBIDDEN for part in relative.split("/")):
        raise ToolError("invalid_path", "이 이미지 경로는 사용할 수 없습니다")
    mime = _IMAGE_TYPES.get(target.suffix.lower())
    if mime is None:
        raise ToolError("invalid_request", "지원하는 이미지, 미디어 또는 PDF 파일을 선택해 주세요")
    try:
        if not target.is_file():
            raise ToolError("invalid_path", "파일을 찾지 못했습니다")
        if target.stat().st_size > MAX_IMAGE:
            raise ToolError("invalid_request", "파일 크기가 Flow 제한을 넘었습니다")
        with target.open("rb") as handle:
            data = handle.read(MAX_IMAGE + 1)
    except OSError as error:
        raise ToolError("read_failed", "파일을 읽지 못했습니다") from error
    if len(data) > MAX_IMAGE:
        raise ToolError("invalid_request", "파일 크기가 Flow 제한을 넘었습니다")
    if file_version(data) != expected_version:
        raise ToolError("version_conflict", "파일이 변경되었습니다. 다시 선택해 주세요")
    request = Request(policy.service_url + "/api/flow/assets", data=data, method="POST",
                      headers={"X-Toolkit-Flow": "1", "X-Toolkit-Flow-Workspace-ID": workspace_id,
                               "Content-Type": mime})
    try:
        with _OPENER.open(request, timeout=15) as response:
            raw = response.read(MAX_BODY + 1)
            if len(raw) > MAX_BODY:
                raise ToolError("invalid_response", "Flow 응답이 너무 큽니다")
            result = json.loads(raw)
            if not isinstance(result, dict) or not isinstance(result.get("src"), str) or not re.fullmatch(
                r"/api/flow/assets/[a-f0-9]{64}\.(?:png|jpg|webp|gif|mp3|wav|ogg|mp4|webm|m4a|aac|pdf)", result["src"]
            ):
                raise ToolError("invalid_response", "Flow 파일 주소를 확인할 수 없습니다")
            return result
    except HTTPError as error:
        try:
            detail = json.loads(error.read(4096)).get("error", "파일을 가져오지 못했습니다")
        except (ValueError, UnicodeError):
            detail = "파일을 가져오지 못했습니다"
        code = {403: "policy_denied", 404: "not_found", 413: "invalid_request",
                415: "invalid_request"}.get(error.code, "flow_error")
        raise ToolError(code, detail) from error
    except (URLError, TimeoutError, ValueError, UnicodeError) as error:
        raise ToolError("flow_unavailable", "등록된 Flow 서비스에 연결하지 못했습니다") from error


async def import_asset(config: HostConfig, workspace_id: str, root: str, path: str, expected_version: str) -> dict:
    return await asyncio.to_thread(_import_asset, config, workspace_id, root, path, expected_version)
