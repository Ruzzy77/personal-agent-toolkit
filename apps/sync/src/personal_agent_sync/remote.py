"""Authenticated remote transport with resumable projection uploads."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from .config import SyncConfig
from .errors import SyncError


class RemoteClient:
    def __init__(self, config: SyncConfig, token: str) -> None:
        self.config = config
        self.token = token
        self.http = httpx.AsyncClient(
            base_url=config.service_url,
            headers={
                "Authorization": f"Bearer {token}",
                "X-Personal-Agent-Device": config.device_id,
            },
            timeout=httpx.Timeout(connect=20, read=600, write=600, pool=20),
            follow_redirects=False,
            http2=True,
        )

    async def close(self) -> None:
        await self.http.aclose()

    async def _json(self, method: str, path: str, value: object) -> dict[str, Any]:
        response = await self.http.request(method, path, json=value)
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise SyncError(
                "remote_protocol_error", "remote service returned invalid JSON"
            ) from exc
        if (
            not response.is_success
            or not isinstance(payload, dict)
            or payload.get("ok") is not True
        ):
            error = payload.get("error", {}) if isinstance(payload, dict) else {}
            code = (
                error.get("code", "remote_request_failed")
                if isinstance(error, dict)
                else "remote_request_failed"
            )
            message = (
                error.get("message", "remote request failed")
                if isinstance(error, dict)
                else "remote request failed"
            )
            raise SyncError(str(code), str(message))
        result = payload.get("result")
        if not isinstance(result, dict):
            raise SyncError("remote_protocol_error", "remote result is invalid")
        return result

    async def upload_projection(
        self,
        corpus_id: str,
        header: dict[str, Any],
        units: list[dict[str, Any]],
    ) -> dict[str, Any]:
        await self._json(
            "POST", f"/sync/v1/corpora/{corpus_id}/projections:begin", header
        )
        for start in range(0, len(units), 25):
            await self._json(
                "POST",
                f"/sync/v1/corpora/{corpus_id}/projection-units:append",
                {"uploadId": header["uploadId"], "units": units[start : start + 25]},
            )
        return await self._json(
            "POST",
            f"/sync/v1/corpora/{corpus_id}/projections:commit",
            {
                "uploadId": header["uploadId"],
                "expectedUnitCount": len(units),
                "expectedManifestHash": header["projection"]["resultManifestHash"],
            },
        )

    async def update_source_state(
        self,
        corpus_id: str,
        document_id: str,
        state: str,
        observed_at: str,
        relative_path: str | None = None,
    ) -> dict[str, Any]:
        value: dict[str, Any] = {
            "corpusId": corpus_id,
            "documentId": document_id,
            "sourceState": state,
            "observedAt": observed_at,
        }
        if relative_path is not None:
            value["relativePath"] = relative_path
        return await self._json(
            "POST",
            f"/sync/v1/corpora/{corpus_id}/documents/{document_id}/source-state",
            value,
        )

    async def analyze_remote(
        self,
        *,
        job_id: str,
        snapshot: Path,
        sha256: str,
        byte_size: int,
        format_id: str,
    ) -> dict[str, Any]:
        headers = {
            "Content-Type": "application/octet-stream",
            "X-Analysis-Job": job_id,
            "X-Input-Sha256": sha256,
            "X-Format-Id": format_id,
            "X-Source-Size": str(byte_size),
        }
        with snapshot.open("rb") as source:
            response = await self.http.post(
                "/sync/v1/analysis",
                headers=headers,
                content=source,
            )
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise SyncError(
                "remote_analyzer_error", "remote analyzer returned invalid JSON"
            ) from exc
        if not response.is_success or not isinstance(payload, dict):
            raise SyncError(
                "remote_analyzer_error", "remote analyzer rejected the document"
            )
        return payload

    async def import_payload(self, product: str, value: object) -> dict[str, Any]:
        if product not in {"sense", "hypes", "corpus-metadata"}:
            raise SyncError("invalid_import", "unsupported migration product")
        return await self._json("POST", f"/sync/v1/import/{product}", value)
