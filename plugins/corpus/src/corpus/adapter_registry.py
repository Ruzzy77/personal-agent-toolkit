"""Route Corpus source capture to the Document Files extraction process.

Corpus owns source registration, capture, revisions, projections, identifiers,
anchors, and search. Format parsing and OCR are supplied only by the separately
installed Document Files plugin through a strict read-only subprocess boundary.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

from .adapters import (
    AdapterBudgets,
    AdapterCapabilities,
    AdapterDescriptor,
    ExternalJSONLAdapter,
    ExtractionAdapter,
)
from .errors import ExtractionError

DESCRIPTOR_SCHEMA_VERSION = "document-files.descriptor.v1"
SUPPORTED_FORMATS = (
    "docx",
    "htm",
    "html",
    "hwp",
    "hwpx",
    "markdown",
    "md",
    "pdf",
    "pptx",
    "txt",
    "xlsx",
)


def _candidate_executables() -> tuple[Path, ...]:
    candidates: list[Path] = []
    configured = os.environ.get("DOCUMENT_FILES_EXECUTABLE")
    if configured:
        candidates.append(Path(configured).expanduser())
    resolved = shutil.which("document-files")
    if resolved:
        candidates.append(Path(resolved))

    package_root = Path(__file__).resolve().parents[2]
    candidates.append(package_root.parent / "document-files" / "bin" / "document-files")

    cache_root = Path.home() / ".codex" / "plugins" / "cache"
    if cache_root.is_dir():
        candidates.extend(
            sorted(
                cache_root.glob("*/document-files/*/bin/document-files"),
                reverse=True,
            )
        )
    unique: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            normalized = candidate.resolve(strict=False)
        except OSError:
            continue
        if normalized not in seen:
            seen.add(normalized)
            unique.append(normalized)
    return tuple(unique)


def resolve_document_files_executable() -> Path:
    for candidate in _candidate_executables():
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise ExtractionError(
        "Document Files is required for source extraction but its executable was not found",
        details={
            "environment_variable": "DOCUMENT_FILES_EXECUTABLE",
            "required_plugin": "document-files",
        },
    )


def _descriptor_capabilities(raw: object, *, format_id: str) -> AdapterCapabilities:
    if not isinstance(raw, Mapping):
        raise ExtractionError("Document Files descriptor capabilities are invalid")
    try:
        capabilities = AdapterCapabilities(
            format_ids=tuple(raw["format_ids"]),
            structural_unit_types=tuple(raw["structural_unit_types"]),
            execution_mode=raw["execution_mode"],
            preserves_reading_order=raw.get("preserves_reading_order", True),
            supports_geometry=raw.get("supports_geometry", False),
            supports_confidence=raw.get("supports_confidence", False),
            supports_ocr=raw.get("supports_ocr", False),
            may_emit_partial=raw.get("may_emit_partial", True),
            protocol_version=raw.get(
                "protocol_version", "document-files.extraction-result.v2"
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ExtractionError(
            "Document Files descriptor capabilities are invalid",
            details={"format_id": format_id},
        ) from exc
    if format_id not in capabilities.format_ids:
        raise ExtractionError(
            "Document Files descriptor does not declare its requested format",
            details={"format_id": format_id},
        )
    return capabilities


def _load_routes(
    executable: Path,
) -> dict[str, tuple[AdapterDescriptor, Mapping[str, object]]]:
    try:
        completed = subprocess.run(
            (str(executable), "process", "--describe"),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ExtractionError(
            "Document Files could not report its extraction descriptor",
            details={"error_type": type(exc).__name__},
        ) from exc
    if completed.returncode != 0:
        raise ExtractionError(
            "Document Files descriptor command failed",
            details={
                "return_code": completed.returncode,
                "stderr_bytes": len(completed.stderr),
            },
        )
    try:
        raw = json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExtractionError("Document Files descriptor is not valid UTF-8 JSON") from exc
    if not isinstance(raw, Mapping) or raw.get("schema_version") != DESCRIPTOR_SCHEMA_VERSION:
        raise ExtractionError("Document Files descriptor schema is unsupported")
    formats = raw.get("formats")
    if not isinstance(formats, Mapping):
        raise ExtractionError("Document Files descriptor has no format routes")
    if set(formats) != set(SUPPORTED_FORMATS):
        raise ExtractionError(
            "Document Files format routes do not match the Corpus contract",
            details={
                "required": list(SUPPORTED_FORMATS),
                "reported": sorted(str(key) for key in formats),
            },
        )

    routes: dict[str, tuple[AdapterDescriptor, Mapping[str, object]]] = {}
    for format_id in SUPPORTED_FORMATS:
        route = formats[format_id]
        if not isinstance(route, Mapping):
            raise ExtractionError("Document Files format descriptor is invalid")
        descriptor_raw = route.get("descriptor")
        config = route.get("config")
        if not isinstance(descriptor_raw, Mapping) or not isinstance(config, Mapping):
            raise ExtractionError("Document Files format descriptor is incomplete")
        capabilities = _descriptor_capabilities(
            descriptor_raw.get("capabilities"), format_id=format_id
        )
        try:
            descriptor = AdapterDescriptor(
                adapter_id=descriptor_raw["adapter_id"],
                adapter_version=descriptor_raw["adapter_version"],
                config_hash=descriptor_raw["config_hash"],
                capabilities=capabilities,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ExtractionError(
                "Document Files format identity is invalid",
                details={"format_id": format_id},
            ) from exc
        routes[format_id] = (descriptor, config)
    return routes


class AdapterRegistry:
    """Immutable routing table from source format to extraction adapter."""

    def __init__(self, adapters_by_format: Mapping[str, ExtractionAdapter]) -> None:
        routes = dict(adapters_by_format)
        for format_id, adapter in routes.items():
            if format_id not in adapter.descriptor.capabilities.format_ids:
                raise ExtractionError(
                    "adapter registry route is not declared by the adapter",
                    details={
                        "format_id": format_id,
                        "adapter_id": adapter.descriptor.adapter_id,
                    },
                )
        self._adapters_by_format = MappingProxyType(routes)

    @property
    def descriptors(self) -> tuple[AdapterDescriptor, ...]:
        return tuple(
            self._adapters_by_format[format_id].descriptor
            for format_id in sorted(self._adapters_by_format)
        )

    def resolve(self, format_id: str) -> ExtractionAdapter:
        try:
            return self._adapters_by_format[format_id]
        except KeyError as exc:
            raise ExtractionError(
                "no extraction adapter is registered for this format",
                details={"format_id": format_id},
            ) from exc

    def accepts_projection(
        self,
        format_id: str,
        adapter_id: str,
        adapter_version: str,
        config_hash: str,
    ) -> bool:
        try:
            current = self.resolve(format_id).descriptor
        except ExtractionError:
            return False
        return (adapter_id, adapter_version, config_hash) == (
            current.adapter_id,
            current.adapter_version,
            current.config_hash,
        )


def build_default_registry(
    runtime_root: Path | None = None,
    *,
    overrides: Mapping[str, ExtractionAdapter] | None = None,
) -> AdapterRegistry:
    """Build Document Files subprocess routes, then apply exact test overrides."""

    del runtime_root  # Extraction runtime ownership belongs to Document Files.
    executable = resolve_document_files_executable()
    described = _load_routes(executable)
    budgets = AdapterBudgets(
        timeout_seconds=600,
        max_input_bytes=2 * 1024 * 1024 * 1024,
        max_stdout_bytes=512 * 1024 * 1024,
        max_stderr_bytes=512 * 1024,
        max_units=1_000_000,
        max_issues=1_000_000,
        max_unit_content_chars=50_000_000,
        max_total_content_chars=500_000_000,
    )
    routes: dict[str, ExtractionAdapter] = {
        format_id: ExternalJSONLAdapter(
            descriptor,
            (str(executable), "process"),
            budgets,
            config=config,
        )
        for format_id, (descriptor, config) in described.items()
    }
    routes.update(overrides or {})
    return AdapterRegistry(routes)
