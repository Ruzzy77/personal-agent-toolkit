"""Reuse proven OCR observations while rebuilding the native document structure.

This reads only the current projection. It does not make an older projection
current, preserve extraction history, or reuse an unverified visual interpretation.
"""

from __future__ import annotations

from collections import defaultdict

# These exact 0.16.0 identities used the same Vision implementation and settings.
# Native structures must still be rebuilt, so this is not a projection whitelist.
_LEGACY_VERSION = "1.0.0+source.b03f80715faf"
_LEGACY_CONFIGS = {
    "docx": "6baba8cdb0e6fed4d29aa68d6b428927b8eaa2ad7f091185d2d2d9adf6332c80",
    "pptx": "1bcc8cb8548a0b51f98fa590f538a37cb9324308ff5c0ec06031cca3ed357a45",
    "hwp": "b8cb89c6112a8879e00dbb48fe0b09910b7b9899e6a2c466e2d253310e7a9b8b",
    "hwpx": "e673d7091f75ebfb9b94cfdbfe9d6f3fba3c5a56e483361c08de25f82cbbcaad",
}
_VISION_SOURCE = "507ccaa03634ac9d2361fa756bff3e52f30adc04a545dff86721479b87de052a"


def can_reuse_ocr(config, adapter_id, adapter_version, config_hash):
    format_id = adapter_id.removeprefix("work-corpus.native.office-vision.")
    return (
        format_id in _LEGACY_CONFIGS
        and adapter_id == f"work-corpus.native.office-vision.{format_id}"
        and adapter_version == _LEGACY_VERSION
        and config_hash == _LEGACY_CONFIGS.get(format_id)
        and config.get("vision_source") == _VISION_SOURCE
        and config.get("runtime")
        == {"macos_release": "27.0", "macos_version_info": "..", "machine": "arm64"}
        and config.get("max_edge_pixels") == 3000
        and list(config.get("recognition_languages", ())) == ["ko-KR", "en-US"]
    )


def _position(location):
    return (
        location.get(
            "part", location.get("section_file", location.get("section_stream"))
        ),
        location.get("element", location.get("image_record")),
    )


def reusable_images(previous, source_sha256, source_crop):
    """Return per-part/crop OCR units; None means a proven no-text observation.

    Empty results can be inferred only when every prior native image was processed
    and the text/no-text occurrence counts account for the complete image set.
    A failed, skipped, ambiguous, or pending image prevents this inference.
    """
    if previous is None:
        return {}
    coverage = [
        i.details for i in previous.issues if i.code == "office_image_range_observed"
    ]
    if len(coverage) != 1 or coverage[0].get("source_sha256") != source_sha256:
        return {}
    images = {}
    regions = defaultdict(list)
    for unit in previous.units:
        location = unit.structure_path
        if unit.unit_type == "embedded_object" and location.get("object_type") in {
            "image",
            "pic",
        }:
            images.setdefault(_position(location), unit)
        elif unit.unit_type == "image_text" and unit.derivation_method == "ocr":
            regions[_position(location)].append(unit)
    count = coverage[0].get("image_count")
    counts = {i.code: i.details.get("occurrences", 0) for i in previous.issues}
    no_text = counts.get("office_image_without_text", 0)
    recognized = counts.get("office_image_ocr_observed", 0)
    not_displayed = sum(
        u.structure_path.get("display_state") == "not_displayed"
        for u in images.values()
    )
    clean = (
        type(count) is int
        and count == len(images)
        and coverage[0].get("next_image") == count
        and type(no_text) is int
        and type(recognized) is int
        and no_text >= 0
        and recognized == len(regions)
        and recognized + no_text + not_displayed == count
        and counts.get("office_image_not_displayed_observed", 0) == not_displayed
        and not any(
            i.code.startswith("office_image_")
            and i.code
            not in {
                "office_image_ocr_observed",
                "office_image_ocr_reused_observed",
                "office_image_not_displayed_observed",
                "office_image_without_text",
                "office_image_content_unread",
                "office_image_visual_content_partial",
                "office_image_range_observed",
            }
            for i in previous.issues
        )
    )
    result = {}
    conflicts = set()
    for position, image in images.items():
        location = image.structure_path
        if location.get("display_state") == "not_displayed":
            continue
        parts = location.get("image_parts", ())
        if len(parts) != 1:
            continue
        try:
            crop = source_crop(location)
        except (TypeError, ValueError):
            continue
        key = parts[0], crop
        values = regions.get(position, [])
        if values:
            digests = {u.structure_path.get("image_sha256") for u in values}
            if len(digests) != 1 or not isinstance(next(iter(digests)), str):
                continue
            digest = next(iter(digests))
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                continue
            try:
                valid = all(
                    u.structure_path.get("image_part") == parts[0]
                    and u.structure_path.get("geometry_scope")
                    == "oriented_embedded_image_pixels"
                    and source_crop(u.structure_path) == crop
                    for u in values
                )
            except (TypeError, ValueError):
                valid = False
            if not valid:
                continue
            value = tuple(values)
        elif clean:
            value = None
        else:
            continue
        if key in result:
            # Different observations of the same input are not resolved by guesswork.
            prior = result[key]

            def signatures(group):
                if group is None:
                    return None
                return [
                    (
                        u.content,
                        u.geometry,
                        u.confidence,
                        u.structure_path.get("image_sha256"),
                    )
                    for u in group
                ]

            if signatures(prior) != signatures(value):
                conflicts.add(key)
        else:
            result[key] = value
    return {key: value for key, value in result.items() if key not in conflicts}
