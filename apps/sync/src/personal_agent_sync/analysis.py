"""Common local/remote Document Files analysis and Corpus projection mapping."""

from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from pathlib import Path
from typing import Any

from .errors import PolicyDenied, SyncError
from .paths import Snapshot
from .remote import RemoteClient
from .state import SyncState, now_iso

SUPPORTED_FORMATS = {
    "md",
    "markdown",
    "txt",
    "html",
    "htm",
    "pdf",
    "docx",
    "pptx",
    "xlsx",
    "hwp",
    "hwpx",
}

MEDIA_TYPES = {
    "md": "text/markdown",
    "markdown": "text/markdown",
    "txt": "text/plain",
    "html": "text/html",
    "htm": "text/html",
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "hwpx": "application/vnd.hancom.hwpx",
    "hwp": "application/x-hwp",
}

_MAX_ANALYSIS_OUTPUT = 256 * 1024 * 1024
_MAX_DESCRIPTOR_OUTPUT = 4 * 1024 * 1024
_ANALYSIS_HELPER = r"""
import json
import sys

from document_files.analysis import AnalysisJob, analyze_document
from document_files.extraction_errors import DocumentExtractionError


try:
    job = AnalysisJob.from_dict(json.load(sys.stdin))
    with open(sys.argv[1], "rb") as source:
        result = analyze_document(job, source).to_dict()
    print(json.dumps({"ok": True, "result": result}, ensure_ascii=False))
except DocumentExtractionError as error:
    print(json.dumps({"ok": False, "error": {"code": error.code, "message": str(error)}}))
except Exception:
    print(json.dumps({"ok": False, "error": {"code": "local_analysis_failed", "message": "local document analysis failed"}}))
"""

_DESCRIPTOR_HELPER = r"""
import json

from document_files.processor import describe_all


print(json.dumps(describe_all(), ensure_ascii=False))
"""


def format_id(relative_path: str) -> str:
    suffix = Path(relative_path).suffix.lower().lstrip(".")
    if suffix not in SUPPORTED_FORMATS:
        raise SyncError(
            "unsupported_format", "Document Files does not support this format"
        )
    return suffix


def local_analyzer_manifest(
    document_files_python: Path | None,
) -> dict[str, dict[str, str]]:
    """Read compact current adapter identities from the pinned Document Files runtime."""

    if document_files_python is None:
        return {}
    try:
        completed = subprocess.run(
            [str(document_files_python), "-c", _DESCRIPTOR_HELPER],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SyncError(
            "local_analyzer_unavailable",
            "the Document Files descriptor manifest could not be read",
        ) from exc
    if (
        completed.returncode != 0
        or len(completed.stdout.encode()) > _MAX_DESCRIPTOR_OUTPUT
    ):
        raise SyncError(
            "local_analyzer_unavailable",
            "the Document Files descriptor manifest could not be read",
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise SyncError(
            "local_analyzer_unavailable",
            "the Document Files descriptor manifest is invalid",
        ) from exc
    formats = payload.get("formats") if isinstance(payload, dict) else None
    if not isinstance(formats, dict):
        raise SyncError(
            "local_analyzer_unavailable",
            "the Document Files descriptor manifest is invalid",
        )
    manifest: dict[str, dict[str, str]] = {}
    for selected_format, value in formats.items():
        config = value.get("config") if isinstance(value, dict) else None
        descriptor = config.get("route") if isinstance(config, dict) else None
        if not isinstance(selected_format, str) or not isinstance(descriptor, dict):
            raise SyncError(
                "local_analyzer_unavailable",
                "the Document Files descriptor manifest is invalid",
            )
        adapter_id = descriptor.get("adapter_id")
        adapter_version = descriptor.get("adapter_version")
        config_hash = descriptor.get("config_hash")
        if (
            not isinstance(adapter_id, str)
            or not adapter_id
            or not isinstance(adapter_version, str)
            or not adapter_version
            or not isinstance(config_hash, str)
            or not config_hash
        ):
            raise SyncError(
                "local_analyzer_unavailable",
                "the Document Files descriptor manifest is invalid",
            )
        manifest[selected_format] = {
            "adapter_id": adapter_id,
            "adapter_version": adapter_version,
            "config_hash": config_hash,
        }
    return manifest


def _identifier(prefix: str, value: str) -> str:
    return f"{prefix}_{hashlib.sha256(value.encode()).hexdigest()[:32]}"


def analyze_local(
    snapshot: Snapshot,
    selected_format: str,
    job_id: str,
    document_files_python: Path | None,
) -> dict[str, Any]:
    if document_files_python is None:
        raise SyncError(
            "local_analyzer_unavailable",
            "the Document Files runtime is not configured",
        )
    job = {
        "schema_version": "document-files.analysis-job.v1",
        "job_id": job_id,
        "operation": "extract",
        "input": {
            "format_id": selected_format,
            "media_type": MEDIA_TYPES[selected_format],
            "byte_size": snapshot.byte_size,
            "sha256": snapshot.sha256,
        },
        "budgets": {
            "max_input_bytes": max(1, snapshot.byte_size),
            "completion_seconds": 580.0,
        },
    }
    try:
        completed = subprocess.run(
            [str(document_files_python), "-c", _ANALYSIS_HELPER, str(snapshot.path)],
            input=json.dumps(job, ensure_ascii=False, allow_nan=False),
            capture_output=True,
            text=True,
            check=False,
            timeout=600,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SyncError(
            "local_analyzer_unavailable",
            "the Document Files runtime could not be started",
        ) from exc
    if (
        completed.returncode != 0
        or len(completed.stdout.encode()) > _MAX_ANALYSIS_OUTPUT
    ):
        raise SyncError("local_analysis_failed", "local document analysis failed")
    try:
        response = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise SyncError(
            "local_analysis_failed", "local document analysis returned invalid data"
        ) from exc
    if not isinstance(response, dict) or type(response.get("ok")) is not bool:
        raise SyncError(
            "local_analysis_failed", "local document analysis returned invalid data"
        )
    if not response["ok"]:
        error = response.get("error")
        if not isinstance(error, dict):
            raise SyncError("local_analysis_failed", "local document analysis failed")
        code = error.get("code")
        message = error.get("message")
        if not isinstance(code, str) or not isinstance(message, str):
            raise SyncError("local_analysis_failed", "local document analysis failed")
        raise SyncError(code, message)
    result = response.get("result")
    if not isinstance(result, dict):
        raise SyncError(
            "local_analysis_failed", "local document analysis returned invalid data"
        )
    return result


async def select_analyzer(
    state: SyncState,
    remote: RemoteClient,
    change: dict[str, Any],
    snapshot: Snapshot,
    selected_format: str,
) -> dict[str, Any]:
    job_id = f"analysis:{uuid.uuid4().hex}"
    route = change["analyzer_route"]
    if route == "local":
        return analyze_local(
            snapshot, selected_format, job_id, remote.config.document_files_python
        )
    if route == "approval_required" and not state.remote_approved(
        change["connection_key"],
        change["document_id"],
        snapshot.sha256,
        snapshot.byte_size,
    ):
        raise PolicyDenied(
            "this document revision requires owner approval before remote analysis"
        )
    if snapshot.byte_size > int(change["max_transfer_bytes"]):
        raise PolicyDenied(
            "this document exceeds the Connection's remote transfer limit"
        )
    return await remote.analyze_remote(
        job_id=job_id,
        snapshot=snapshot.path,
        sha256=snapshot.sha256,
        byte_size=snapshot.byte_size,
        format_id=selected_format,
    )


def _require_mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SyncError("invalid_analysis_result", f"analysis {name} is invalid")
    return value


def build_projection(
    *,
    change: dict[str, Any],
    snapshot: Snapshot,
    selected_format: str,
    result: dict[str, Any],
    revision_id: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    analysis_input = _require_mapping(result.get("input"), "input")
    if (
        analysis_input.get("sha256") != snapshot.sha256
        or analysis_input.get("byte_size") != snapshot.byte_size
        or analysis_input.get("format_id") != selected_format
    ):
        raise SyncError(
            "analysis_identity_mismatch",
            "analysis result does not match captured bytes",
        )
    extraction = _require_mapping(result.get("extraction"), "extraction")
    analyzer = _require_mapping(result.get("analyzer"), "analyzer")
    descriptor = _require_mapping(extraction.get("descriptor", analyzer), "descriptor")
    capabilities = _require_mapping(descriptor.get("capabilities"), "capabilities")
    raw_units = extraction.get("units")
    raw_issues = extraction.get("issues")
    coverage = _require_mapping(extraction.get("coverage"), "coverage")
    manifest_hash = extraction.get("manifest_hash")
    if (
        not isinstance(raw_units, list)
        or not isinstance(raw_issues, list)
        or not isinstance(manifest_hash, str)
        or len(manifest_hash) != 64
    ):
        raise SyncError(
            "invalid_analysis_result", "analysis extraction envelope is invalid"
        )
    document_id = str(change["document_id"])
    if revision_id is not None and not 1 <= len(revision_id) <= 160:
        raise SyncError("invalid_revision_id", "resolved revision id is invalid")
    revision_id = revision_id or _identifier("rev", f"{document_id}:{snapshot.sha256}")
    projection_id = _identifier("projection", f"{revision_id}:{manifest_hash}")
    unit_ids = [
        _identifier("unit", f"{projection_id}:{index + 1}")
        for index in range(len(raw_units))
    ]
    units: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_units):
        unit = _require_mapping(raw, f"unit {index + 1}")
        content = unit.get("content")
        structure = unit.get("structure_path")
        issues = unit.get("issues", [])
        geometry = unit.get("geometry", {})
        flags = unit.get("quality_flags", [])
        if (
            not isinstance(content, str)
            or not isinstance(structure, dict)
            or not isinstance(issues, list)
            or not isinstance(geometry, dict)
            or not isinstance(flags, list)
        ):
            raise SyncError(
                "invalid_analysis_result", "analysis Source unit is invalid"
            )
        derivation = unit.get("derivation_method", "native_text")
        units.append(
            {
                "unitId": unit_ids[index],
                "ordinal": index + 1,
                "unitType": str(unit.get("unit_type", "content")),
                "structurePath": structure,
                "sourceAnchor": {
                    "relative_path": change["relative_path_nfc"],
                    "structure_path": structure,
                },
                "content": content,
                "contentSha256": hashlib.sha256(content.encode()).hexdigest(),
                "previousUnitId": unit_ids[index - 1] if index > 0 else None,
                "nextUnitId": unit_ids[index + 1]
                if index + 1 < len(unit_ids)
                else None,
                "extractionIssues": issues,
                "derivationMethod": str(derivation),
                "geometry": geometry,
                "confidence": unit.get("confidence"),
                "ocr": str(derivation).startswith("ocr"),
                "qualityFlags": [str(flag) for flag in flags],
            }
        )
    header = {
        "uploadId": f"upload_{uuid.uuid4().hex}",
        "corpusId": change["corpus_id"],
        "document": {
            "documentId": document_id,
            "relativePath": change["relative_path_nfc"],
            "extension": selected_format,
            "sourceState": "available",
            "logicalSize": snapshot.byte_size,
            "modifiedNs": str(snapshot.modified_ns),
            "residencyState": "resident",
            "eligibilityState": "supported",
        },
        "revision": {
            "revisionId": revision_id,
            "sha256": snapshot.sha256,
            "sourceSize": snapshot.byte_size,
            "capturedAt": now_iso(),
        },
        "projection": {
            "projectionId": projection_id,
            "adapterId": str(descriptor.get("adapter_id")),
            "adapterVersion": str(descriptor.get("adapter_version")),
            "configHash": str(descriptor.get("config_hash")),
            "resultManifestHash": manifest_hash,
            "completenessState": extraction.get("completeness"),
            "coverage": coverage,
            "capabilityManifest": capabilities,
            "issues": raw_issues,
            "assuranceState": "declared",
            "declaredUnitCount": len(units),
        },
    }
    # A JSON round-trip rejects non-serializable adapter output before any upload begins.
    json.dumps({"header": header, "units": units}, ensure_ascii=False, allow_nan=False)
    return header, units
