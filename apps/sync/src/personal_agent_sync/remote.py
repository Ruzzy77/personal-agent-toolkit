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

    async def _json(
        self, method: str, path: str, value: object | None = None
    ) -> dict[str, Any]:
        response = await self.http.request(
            method, path, **({"json": value} if value is not None else {})
        )
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
        begun = await self._json(
            "POST", f"/sync/v1/corpora/{corpus_id}/projections:begin", header
        )
        already_committed = begun.get("alreadyCommitted", False)
        if type(already_committed) is not bool:
            raise SyncError(
                "remote_protocol_error", "remote projection state is invalid"
            )
        if already_committed:
            if (
                begun.get("projectionId") != header["projection"]["projectionId"]
                or begun.get("revisionId") != header["revision"]["revisionId"]
                or begun.get("resultManifestHash")
                != header["projection"]["resultManifestHash"]
                or begun.get("unitCount") != len(units)
            ):
                raise SyncError(
                    "remote_protocol_error",
                    "remote committed projection identity is invalid",
                )
            return begun
        staged = begun.get("stagedUnitCount", 0)
        if (
            isinstance(staged, bool)
            or not isinstance(staged, int)
            or not 0 <= staged <= len(units)
        ):
            raise SyncError(
                "remote_protocol_error", "remote staged-unit count is invalid"
            )
        for batch in self._unit_batches(units[staged:]):
            await self._json(
                "POST",
                f"/sync/v1/corpora/{corpus_id}/projection-units:append",
                {"uploadId": header["uploadId"], "units": batch},
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

    @staticmethod
    def _unit_batches(
        units: list[dict[str, Any]],
        *,
        maximum_count: int = 500,
        maximum_bytes: int = 8 * 1024 * 1024,
    ):
        batch: list[dict[str, Any]] = []
        size = 64
        for unit in units:
            encoded_size = len(
                json.dumps(unit, ensure_ascii=False, separators=(",", ":")).encode(
                    "utf-8"
                )
            )
            if batch and (
                len(batch) >= maximum_count or size + encoded_size > maximum_bytes
            ):
                yield batch
                batch = []
                size = 64
            if encoded_size > maximum_bytes:
                raise SyncError(
                    "projection_unit_too_large",
                    "one Source unit exceeds the upload budget",
                )
            batch.append(unit)
            size += encoded_size + 1
        if batch:
            yield batch

    async def import_documents(
        self, corpus_id: str, documents: list[dict[str, Any]]
    ) -> dict[str, Any]:
        imported = 0
        for start in range(0, len(documents), 500):
            result = await self._json(
                "POST",
                f"/sync/v1/corpora/{corpus_id}/documents:import",
                {"corpusId": corpus_id, "documents": documents[start : start + 500]},
            )
            count = result.get("importedDocumentCount")
            if isinstance(count, bool) or not isinstance(count, int):
                raise SyncError(
                    "remote_protocol_error", "remote document count is invalid"
                )
            imported += count
        return {"corpusId": corpus_id, "importedDocumentCount": imported}

    async def import_external(
        self, corpus_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._json(
            "POST", f"/sync/v1/corpora/{corpus_id}/external:import", payload
        )

    async def inventory(
        self,
        corpus_id: str,
        *,
        document_offset: int = 0,
        projection_offset: int = 0,
        limit: int = 500,
        include_storage_details: bool = False,
        hotspot_limit: int = 0,
    ) -> dict[str, Any]:
        request: dict[str, Any] = {
            "documentOffset": document_offset,
            "projectionOffset": projection_offset,
            "limit": limit,
        }
        if include_storage_details:
            request["includeStorageDetails"] = True
            request["hotspotLimit"] = hotspot_limit
        return await self._json(
            "POST",
            f"/sync/v1/corpora/{corpus_id}/inventory",
            request,
        )

    async def maintain_corpus(
        self,
        corpus_id: str,
        *,
        remove_projection_ids: list[str],
        remove_document_ids: list[str],
        remove_upload_ids: list[str],
        compact_search_index_limit: int = 0,
        compact_unit_metadata_limit: int = 0,
    ) -> dict[str, Any]:
        request: dict[str, Any] = {
            "corpusId": corpus_id,
            "removeProjectionIds": remove_projection_ids,
            "removeDocumentIds": remove_document_ids,
            "removeUploadIds": remove_upload_ids,
        }
        if compact_search_index_limit:
            request["compactSearchIndexLimit"] = compact_search_index_limit
        if compact_unit_metadata_limit:
            request["compactUnitMetadataLimit"] = compact_unit_metadata_limit
        return await self._json(
            "POST",
            f"/sync/v1/corpora/{corpus_id}/maintenance",
            request,
        )

    async def verification_summary(self) -> dict[str, Any]:
        return await self._json("GET", "/sync/v1/verification-summary")

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
