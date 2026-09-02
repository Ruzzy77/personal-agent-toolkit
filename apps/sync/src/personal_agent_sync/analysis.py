"""Common local/remote Document Files analysis and Corpus projection mapping."""

from __future__ import annotations

import hashlib
import json
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


def format_id(relative_path: str) -> str:
    suffix = Path(relative_path).suffix.lower().lstrip(".")
    if suffix not in SUPPORTED_FORMATS:
        raise SyncError(
            "unsupported_format", "Document Files does not support this format"
        )
    return suffix


def _identifier(prefix: str, value: str) -> str:
    return f"{prefix}_{hashlib.sha256(value.encode()).hexdigest()[:32]}"


def analyze_local(
    snapshot: Snapshot, selected_format: str, job_id: str
) -> dict[str, Any]:
    try:
        from document_files.analysis import (  # type: ignore[import-not-found]
            AnalysisBudgets,
            AnalysisInput,
            AnalysisJob,
            analyze_document,
        )
        from document_files.formats import (
            FORMAT_SPECS,  # type: ignore[import-not-found]
        )
    except ImportError as exc:
        raise SyncError(
            "local_analyzer_unavailable",
            "the installed Document Files analyzer is unavailable",
        ) from exc
    specification = FORMAT_SPECS[selected_format]
    analysis_input = AnalysisInput(
        format_id=selected_format,
        media_type=specification.media_type,
        byte_size=snapshot.byte_size,
        sha256=snapshot.sha256,
    )
    job = AnalysisJob(
        job_id=job_id,
        input=analysis_input,
        budgets=AnalysisBudgets(
            max_input_bytes=max(1, snapshot.byte_size), completion_seconds=580.0
        ),
    )
    with snapshot.path.open("rb") as source:
        return analyze_document(job, source).to_dict()


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
        return analyze_local(snapshot, selected_format, job_id)
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
    revision_id = _identifier("rev", f"{document_id}:{snapshot.sha256}")
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
            "extension": f".{selected_format}",
            "sourceState": "available",
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
