"""Bounded local Vision assistance for otherwise textless Office content."""

from __future__ import annotations

import hashlib
import tempfile
import time
import zipfile
from collections import Counter
from dataclasses import replace
from pathlib import Path

from .adapters import (
    AdapterBudgets,
    AdapterCapabilities,
    AdapterDescriptor,
    ExternalJSONLAdapter,
    ExtractedUnit,
    ExtractionEnvelope,
    ExtractionIssue,
)
from .errors import BudgetExceededError, CorpusError
from .extractors import MAX_UNIT_CHARS
from .native_adapters import PDFKitVisionAdapter

_MESSAGES = {
    "office_image_placement_unresolved": "A cropped or ambiguous image placement was left unread.",
    "office_image_size_limit": "An embedded image exceeds the configured byte or pixel budget.",
    "office_image_ocr_budget_reached": "Further image recognition exceeds the count or time budget.",
    "office_image_ocr_failed": "Vision could not process an embedded image; native content was retained.",
    "office_image_format_unsupported": "The local image decoder does not support an embedded image format.",
    "office_image_without_text": "Vision found no text in an embedded image.",
    "office_image_ocr_output_limit": "Image recognition exceeds the configured result size.",
    "office_image_ocr_observed": "Local OCR recovered text at the original embedded image location.",
    "office_image_visual_content_partial": "OCR text does not reconstruct the image's non-text visual content.",
}


class OfficeVisionAdapter:
    """Native XML always wins; OCR never replaces existing native units."""

    def __init__(self, native, runtime_root):
        self.native = native
        self.vision = PDFKitVisionAdapter(runtime_root)
        source_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        self.config = {
            "native": native.descriptor.to_dict(),
            "vision_source": self.vision.source_hash,
            "runtime": self.vision.config["runtime"],
            "scope": "textless_slides_or_document",
            "native_text_min_alphanumeric_characters": 32,
            "max_images": 16,
            "max_image_bytes": 16 * 1024 * 1024,
            "max_total_image_bytes": 32 * 1024 * 1024,
            "timeout_seconds": 60,
            "max_edge_pixels": 3000,
            "recognition_languages": ["ko-KR", "en-US"],
            "cropped_images": "leave_unread",
        }
        caps = native.descriptor.capabilities
        self.descriptor = AdapterDescriptor.from_config(
            adapter_id=f"work-corpus.native.office-vision.{native.adapter_name}",
            adapter_version=f"1.0.0+source.{source_hash[:12]}",
            config=self.config,
            capabilities=replace(
                caps,
                structural_unit_types=(*caps.structural_unit_types, "image_text"),
                supports_ocr=True,
                supports_geometry=True,
                supports_confidence=True,
            ),
        )

    def _image_text(self, image_path, seconds):
        started = time.monotonic()
        config = {
            "max_edge_pixels": self.config["max_edge_pixels"],
            "recognition_languages": self.config["recognition_languages"],
        }
        descriptor = AdapterDescriptor.from_config(
            adapter_id="work-corpus.native.vision-image",
            adapter_version=self.vision.descriptor.adapter_version,
            config=config,
            capabilities=AdapterCapabilities(
                format_ids=("image",),
                structural_unit_types=("page_region",),
                execution_mode="jsonl_subprocess",
                preserves_reading_order=False,
                supports_ocr=True,
                supports_geometry=True,
                supports_confidence=True,
            ),
        )
        executable = self.vision._build(timeout_seconds=max(0.01, seconds))
        seconds -= time.monotonic() - started
        if seconds <= 0:
            raise BudgetExceededError("Office OCR exhausted its runtime budget")
        adapter = ExternalJSONLAdapter(
            descriptor,
            (str(executable),),
            AdapterBudgets(
                timeout_seconds=max(0.01, seconds),
                max_input_bytes=self.config["max_image_bytes"],
                max_units=20_000,
                max_unit_content_chars=MAX_UNIT_CHARS,
                max_total_content_chars=2_000_000,
            ),
            config=config,
        )
        return adapter.extract(image_path, format_id="image")

    def extract(self, path, *, format_id):
        native = self.native.extract(path, format_id=format_id)
        native_characters = Counter()
        for unit in native.units:
            if unit.unit_type not in {"embedded_object", "speaker_notes", "field"}:
                native_characters[unit.structure_path.get("slide")] += sum(
                    c.isalnum() for c in unit.content
                )
        insertions, observations = {}, Counter()
        added_characters, added_count = 0, 0
        native_total = sum(len(u.content) for u in native.units)
        by_part, by_digest, placements = {}, {}, set()
        bytes_read = 0
        deadline = time.monotonic() + self.config["timeout_seconds"]
        with (
            zipfile.ZipFile(path) as archive,
            tempfile.TemporaryDirectory(prefix="corpus-image-") as temporary,
        ):
            for position, unit in enumerate(native.units):
                location = unit.to_dict()["structure_path"]
                if (
                    unit.unit_type != "embedded_object"
                    or location.get("object_type") != "image"
                ):
                    continue
                if (
                    native_characters[location.get("slide")]
                    >= self.config["native_text_min_alphanumeric_characters"]
                ):
                    continue
                placement = (location.get("part"), location.get("element"))
                if placement in placements:
                    continue
                placements.add(placement)
                parts = location.get("image_parts", [])
                if location.get("source_crops") or len(parts) != 1:
                    observations["office_image_placement_unresolved"] += 1
                    continue
                part = parts[0]
                if part not in by_part:
                    info = archive.getinfo(part)
                    if (
                        info.file_size > self.config["max_image_bytes"]
                        or bytes_read + info.file_size
                        > self.config["max_total_image_bytes"]
                    ):
                        observations["office_image_size_limit"] += 1
                        continue
                    if (
                        len(by_part) >= self.config["max_images"]
                        or time.monotonic() >= deadline
                    ):
                        observations["office_image_ocr_budget_reached"] += 1
                        continue
                    raw = archive.read(part)
                    bytes_read += len(raw)
                    digest = hashlib.sha256(raw).hexdigest()
                    if digest not in by_digest:
                        image_path = Path(temporary) / "image.bin"
                        image_path.write_bytes(raw)
                        try:
                            by_digest[digest] = self._image_text(
                                image_path, deadline - time.monotonic()
                            )
                        except (CorpusError, OSError):
                            by_digest[digest] = None
                        finally:
                            image_path.unlink(missing_ok=True)
                    by_part[part] = digest, by_digest[digest]
                digest, recognized = by_part[part]
                if recognized is None:
                    observations["office_image_ocr_failed"] += 1
                    continue
                codes = {i.code for i in recognized.issues}
                if "image_format_unsupported" in codes:
                    observations["office_image_format_unsupported"] += 1
                    continue
                if "image_pixel_budget_exceeded" in codes:
                    observations["office_image_size_limit"] += 1
                    continue
                if any(
                    i.code == "image_text_budget_exceeded" for i in recognized.issues
                ):
                    observations["office_image_ocr_output_limit"] += 1
                    continue
                if not recognized.units:
                    observations["office_image_without_text"] += 1
                    continue
                regions = []
                for index, region in enumerate(recognized.units):
                    if (
                        len(native.units) + added_count >= 200_000
                        or native_total + added_characters + len(region.content)
                        > 50_000_000
                    ):
                        observations["office_image_ocr_output_limit"] += 1
                        break
                    regions.append(
                        ExtractedUnit(
                            unit_type="image_text",
                            structure_path={
                                **location,
                                "image_part": part,
                                "image_sha256": digest,
                                "image_region": index,
                                "geometry_scope": "embedded_image_pixels",
                                "text_representation": "vision_ocr",
                            },
                            content=region.content,
                            derivation_method="ocr",
                            geometry=region.geometry,
                            confidence=region.confidence,
                            quality_flags=(*region.quality_flags, "embedded_image_ocr"),
                            issues=region.issues,
                        )
                    )
                    added_characters += len(region.content)
                    added_count += 1
                if regions:
                    insertions[position] = regions
                    observations["office_image_ocr_observed"] += 1
                if observations["office_image_ocr_output_limit"]:
                    break
        issues = []
        for issue in native.issues:
            if added_count and issue.code == "no_extractable_text":
                continue
            if (
                issue.code == "office_image_content_unread"
                and observations["office_image_ocr_observed"]
            ):
                remaining = max(
                    0,
                    issue.details.get("occurrences", 0)
                    - observations["office_image_ocr_observed"],
                )
                if remaining:
                    issues.append(
                        replace(
                            issue, details={**issue.details, "occurrences": remaining}
                        )
                    )
            else:
                issues.append(issue)
        if observations["office_image_ocr_observed"]:
            observations["office_image_visual_content_partial"] = observations[
                "office_image_ocr_observed"
            ]
        for code, count in sorted(observations.items()):
            if count:
                issues.append(
                    ExtractionIssue(
                        code=code,
                        severity="info"
                        if code == "office_image_ocr_observed"
                        else "warning",
                        message=_MESSAGES[code],
                        details={"occurrences": count, "scope": self.config["scope"]},
                    )
                )
        units = tuple(
            value
            for position, unit in enumerate(native.units)
            for value in (unit, *insertions.get(position, ()))
        )
        return ExtractionEnvelope.create(
            descriptor=self.descriptor,
            completeness="partial"
            if (not units or any(i.severity in {"warning", "error"} for i in issues))
            else "complete",
            units=units,
            issues=issues,
        )
