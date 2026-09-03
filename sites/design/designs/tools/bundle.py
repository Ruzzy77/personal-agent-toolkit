#!/usr/bin/env python3
"""디자인 라이브러리 매니페스트를 검증하고 프로필 CSS를 조립한다.

사용법
  python designs/tools/bundle.py --list
  python designs/tools/bundle.py --catalog
  python designs/tools/bundle.py --validate
  python designs/tools/bundle.py --design hanji --profile brief
  python designs/tools/bundle.py --inject page.html
  python designs/tools/bundle.py --check page.html
  python designs/tools/bundle.py --ready finished-page.html

HTML 쪽 마커 (마커 이름이 곧 디자인 id, 이 사이가 통째로 교체된다):
  <!-- hanji:styles profile=brief -->
  <style>…</style>
  <!-- /hanji:styles -->

프로필은 마커의 profile 속성 > --profile 플래그 > 디자인의 default_profile
순으로 정해진다. designs/shared/core.css가 있으면 모든 디자인의 모든
프로필 맨 앞에 자동으로 포함된다. 표준 라이브러리만 사용한다.
"""

import argparse
import hashlib
import json
import math
import re
import shutil
import sys
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SHARED_CORE = ROOT / "shared" / "core.css"
LIBRARY_METADATA = ROOT / "library.json"
PATTERNS = ROOT / "patterns.json"
LICENSE_FILE = ROOT.parent / "LICENSE"

NAME_RE = re.compile(r"[a-z0-9-]+")
KEY_RE = re.compile(r"[a-z0-9_]+")
HEX_COLOR_RE = re.compile(r"#[0-9A-Fa-f]{6}")
SUPPORTED_SCHEMA_VERSION = 3
STATUS_VALUES = {"draft", "candidate", "validated", "deprecated"}
FORMAT_VALUES = {"web", "document", "slides", "image"}
FORMAT_FIT_VALUES = {"primary", "supported"}
FORMAT_SUPPORT_VALUES = {"guidance", "assets", "verified"}
VISIBILITY_VALUES = {"public", "private"}
PALETTE_REQUIRED_KEYS = ("background", "surface", "text", "muted", "accent")
PALETTE_OPTIONAL_KEYS = ("accent2", "link", "line")
TYPOGRAPHY_REQUIRED_KEYS = ("heading", "body")
TYPOGRAPHY_OPTIONAL_KEYS = ("label", "data", "decorative")
CORE_FIELDS = (
    "id",
    "name",
    "description",
    "version",
    "default_profile",
    "profiles",
    "styleguide",
)

MARKER_RE = re.compile(
    r"(<!--\s*(?P<design>[a-z0-9-]+):styles"
    r"(?:\s+profile=(?P<profile>[a-z0-9-]+))?\s*-->)"
    r"(?P<body>.*?)"
    r"(<!--\s*/(?P=design):styles\s*-->)",
    re.DOTALL,
)

READY_PATTERNS = (
    ("템플릿 자리표시자", re.compile(r"\{\{[^{}\n]+\}\}")),
    ('빈 링크 href="#"', re.compile(r"""href\s*=\s*["']#["']""", re.IGNORECASE)),
    ("미치환 날짜", re.compile(r"\b(?:YYYY|\d{4})-00-00\b")),
)


def _reject_json_constant(value: str) -> None:
    """JSON 표준 밖의 NaN/Infinity 값을 거부한다."""
    raise ValueError(f"허용되지 않는 JSON 숫자 {value}")


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _valid_name(value: Any) -> bool:
    return isinstance(value, str) and NAME_RE.fullmatch(value) is not None


def _valid_key(value: Any) -> bool:
    return isinstance(value, str) and KEY_RE.fullmatch(value) is not None


def _string_array(
    value: Any,
    label: str,
    errors: list[str],
    *,
    names: bool = False,
) -> list[str] | None:
    if not isinstance(value, list) or not value:
        errors.append(f"{label}: 비어 있지 않은 문자열 배열이어야 합니다")
        return None
    if not all(_nonempty_string(item) for item in value):
        errors.append(f"{label}: 모든 항목이 비어 있지 않은 문자열이어야 합니다")
        return None
    if names and not all(_valid_name(item) for item in value):
        errors.append(f"{label}: 항목은 [a-z0-9-]+ 형식이어야 합니다")
        return None
    if len(set(value)) != len(value):
        errors.append(f"{label}: 중복 항목을 포함할 수 없습니다")
        return None
    return value


def _file_path(
    design_dir: Path,
    value: Any,
    label: str,
    errors: list[str],
) -> None:
    if not _nonempty_string(value):
        errors.append(f"{label}: 비어 있지 않은 상대 파일 경로여야 합니다")
        return

    relative = Path(value)
    if relative.is_absolute():
        errors.append(f"{label}: 절대 경로를 사용할 수 없습니다 ({value})")
        return

    base = design_dir.resolve()
    resolved = (design_dir / relative).resolve()
    try:
        resolved.relative_to(base)
    except ValueError:
        errors.append(f"{label}: 디자인 폴더 밖을 가리킬 수 없습니다 ({value})")
        return

    if not resolved.is_file():
        errors.append(f"{label}: 파일이 존재하지 않습니다 ({value})")


def _validate_iso_date(value: Any, label: str, errors: list[str]) -> None:
    if not isinstance(value, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
        errors.append(f"{label}: YYYY-MM-DD 형식의 날짜여야 합니다")
        return
    try:
        date.fromisoformat(value)
    except ValueError:
        errors.append(f"{label}: 유효한 날짜가 아닙니다 ({value})")


def load_library_metadata() -> dict[str, Any]:
    """공개 라이브러리의 이름, 버전과 라이선스를 읽고 검증한다."""
    try:
        metadata = json.loads(
            LIBRARY_METADATA.read_text(encoding="utf-8"),
            parse_constant=_reject_json_constant,
        )
    except (OSError, json.JSONDecodeError, ValueError) as error:
        sys.exit(
            f"{LIBRARY_METADATA.relative_to(ROOT)}: JSON을 읽을 수 없습니다 ({error})"
        )

    errors: list[str] = []
    if not isinstance(metadata, dict):
        errors.append("최상위 값은 객체여야 합니다")
    else:
        if metadata.get("schema_version") != 1:
            errors.append("schema_version: 1이어야 합니다")
        if not _valid_name(metadata.get("id")):
            errors.append("id: [a-z0-9-]+ 형식이어야 합니다")
        for field in ("name", "version", "license", "description"):
            if not _nonempty_string(metadata.get(field)):
                errors.append(f"{field}: 비어 있지 않은 문자열이어야 합니다")

    if errors:
        print("[라이브러리 메타데이터 오류]", file=sys.stderr)
        for error in errors:
            print(f"- {LIBRARY_METADATA.relative_to(ROOT)}: {error}", file=sys.stderr)
        sys.exit(1)
    return metadata


def load_patterns() -> list[dict[str, Any]]:
    """레시피가 참조하는 일반 설계 패턴을 읽고 검증한다."""
    try:
        payload = json.loads(
            PATTERNS.read_text(encoding="utf-8"),
            parse_constant=_reject_json_constant,
        )
    except (OSError, json.JSONDecodeError, ValueError) as error:
        sys.exit(f"{PATTERNS.relative_to(ROOT)}: JSON을 읽을 수 없습니다 ({error})")

    errors: list[str] = []
    patterns: list[dict[str, Any]] = []
    if not isinstance(payload, dict):
        errors.append("최상위 값은 객체여야 합니다")
    else:
        if payload.get("schema_version") != 1:
            errors.append("schema_version: 1이어야 합니다")
        raw_patterns = payload.get("patterns")
        if not isinstance(raw_patterns, list) or not raw_patterns:
            errors.append("patterns: 비어 있지 않은 배열이어야 합니다")
        else:
            patterns = raw_patterns

    seen_ids: set[str] = set()
    for index, pattern in enumerate(patterns):
        label = f"patterns[{index}]"
        if not isinstance(pattern, dict):
            errors.append(f"{label}: 객체여야 합니다")
            continue
        pattern_id = pattern.get("id")
        if not _valid_name(pattern_id):
            errors.append(f"{label}.id: [a-z0-9-]+ 형식이어야 합니다")
        elif pattern_id in seen_ids:
            errors.append(f"{label}.id: 중복 id {pattern_id!r}")
        else:
            seen_ids.add(pattern_id)
        for field in ("name", "use_when"):
            if not _nonempty_string(pattern.get(field)):
                errors.append(f"{label}.{field}: 비어 있지 않은 문자열이어야 합니다")
        _string_array(pattern.get("principles"), f"{label}.principles", errors)
        _string_array(pattern.get("checks"), f"{label}.checks", errors)

    if errors:
        print("[패턴 오류]", file=sys.stderr)
        for error in errors:
            print(f"- {PATTERNS.relative_to(ROOT)}: {error}", file=sys.stderr)
        sys.exit(1)
    return patterns


def _validate_format_guide(
    design_dir: Path,
    relative_path: Any,
    design_id: Any,
    declared_formats: list[str],
    errors: list[str],
) -> None:
    """형식별 디자인 규칙 파일의 최소 구조를 확인한다."""
    _file_path(design_dir, relative_path, "format_guide", errors)
    if not _nonempty_string(relative_path):
        return

    path = (design_dir / relative_path).resolve()
    if not path.is_file():
        return

    try:
        guide = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=_reject_json_constant,
        )
    except (OSError, json.JSONDecodeError, ValueError) as error:
        errors.append(f"format_guide: JSON을 읽을 수 없습니다 ({error})")
        return

    if not isinstance(guide, dict):
        errors.append("format_guide: 최상위 값은 객체여야 합니다")
        return
    if guide.get("schema_version") != 1:
        errors.append("format_guide.schema_version: 1이어야 합니다")
    if guide.get("design_id") != design_id:
        errors.append("format_guide.design_id: design.json의 id와 같아야 합니다")

    shared = guide.get("shared")
    if not isinstance(shared, dict):
        errors.append("format_guide.shared: 객체여야 합니다")
    else:
        for field in ("palette", "typography", "shape", "image"):
            if field not in shared:
                errors.append(f"format_guide.shared.{field}: 필수 필드입니다")

        palette = shared.get("palette")
        if isinstance(palette, dict):
            for key in PALETTE_REQUIRED_KEYS:
                if key not in palette:
                    errors.append(f"format_guide.shared.palette.{key}: 필수 키입니다")
            allowed = set(PALETTE_REQUIRED_KEYS) | set(PALETTE_OPTIONAL_KEYS)
            unknown = set(palette) - allowed
            if unknown:
                errors.append(
                    "format_guide.shared.palette: 어휘 밖의 키가 있습니다 "
                    f"({', '.join(sorted(unknown))}; 허용 추가 키: "
                    f"{', '.join(PALETTE_OPTIONAL_KEYS)})"
                )
            for key, value in palette.items():
                if not isinstance(value, str) or HEX_COLOR_RE.fullmatch(value) is None:
                    errors.append(
                        f"format_guide.shared.palette.{key}: #RRGGBB 형식이어야 합니다"
                    )
        elif "palette" in shared:
            errors.append("format_guide.shared.palette: 객체여야 합니다")

        typography = shared.get("typography")
        if isinstance(typography, dict):
            for key in TYPOGRAPHY_REQUIRED_KEYS:
                if key not in typography:
                    errors.append(
                        f"format_guide.shared.typography.{key}: 필수 키입니다"
                    )
            allowed = set(TYPOGRAPHY_REQUIRED_KEYS) | set(TYPOGRAPHY_OPTIONAL_KEYS)
            unknown = set(typography) - allowed
            if unknown:
                errors.append(
                    "format_guide.shared.typography: 어휘 밖의 키가 있습니다 "
                    f"({', '.join(sorted(unknown))}; 허용 추가 키: "
                    f"{', '.join(TYPOGRAPHY_OPTIONAL_KEYS)})"
                )
            for key, value in typography.items():
                if not _nonempty_string(value):
                    errors.append(
                        f"format_guide.shared.typography.{key}: 비어 있지 않은 문자열이어야 합니다"
                    )
        elif "typography" in shared:
            errors.append("format_guide.shared.typography: 객체여야 합니다")

    guide_formats = guide.get("formats")
    if not isinstance(guide_formats, dict):
        errors.append("format_guide.formats: 객체여야 합니다")
        return

    if set(guide_formats) != set(declared_formats):
        errors.append("format_guide.formats: design.json의 formats와 같아야 합니다")

    for format_name, rules in guide_formats.items():
        label = f"format_guide.formats.{format_name}"
        if not isinstance(rules, dict):
            errors.append(f"{label}: 객체여야 합니다")
            continue
        if not _nonempty_string(rules.get("canvas")):
            errors.append(f"{label}.canvas: 비어 있지 않은 문자열이어야 합니다")
        if not _nonempty_string(rules.get("typography")):
            errors.append(f"{label}.typography: 비어 있지 않은 문자열이어야 합니다")
        _string_array(rules.get("composition"), f"{label}.composition", errors)


def _validate_gallery(data: dict[str, Any], errors: list[str]) -> None:
    """갤러리 표시용 메타데이터를 검증한다. 화면과 정적 갤러리가 이 블록만 읽는다."""
    gallery = data.get("gallery")
    if not isinstance(gallery, dict):
        errors.append("gallery: schema v2 필수 객체입니다 (갤러리 표시용 메타데이터)")
        return

    for field in ("korean_name", "purpose", "note"):
        if not _nonempty_string(gallery.get(field)):
            errors.append(f"gallery.{field}: 비어 있지 않은 문자열이어야 합니다")

    _string_array(gallery.get("directions"), "gallery.directions", errors)

    swatches = gallery.get("swatches")
    if not isinstance(swatches, list) or not swatches:
        errors.append("gallery.swatches: 비어 있지 않은 색 배열이어야 합니다")
    else:
        for index, value in enumerate(swatches):
            if not isinstance(value, str) or HEX_COLOR_RE.fullmatch(value) is None:
                errors.append(f"gallery.swatches[{index}]: #RRGGBB 형식이어야 합니다")

    templates = data.get("templates") if isinstance(data.get("templates"), dict) else {}
    template_labels = gallery.get("template_labels", {})
    if not isinstance(template_labels, dict):
        errors.append("gallery.template_labels: 객체여야 합니다")
    else:
        for role, label_text in template_labels.items():
            if role not in templates:
                errors.append(
                    f"gallery.template_labels.{role}: templates에 없는 역할입니다"
                )
            if not _nonempty_string(label_text):
                errors.append(
                    f"gallery.template_labels.{role}: 비어 있지 않은 문자열이어야 합니다"
                )
        missing_roles = set(templates) - set(template_labels)
        if missing_roles:
            errors.append(
                "gallery.template_labels: 라벨 없는 템플릿 역할이 있습니다 "
                f"({', '.join(sorted(missing_roles))})"
            )

    for field, slugs_field in (
        ("use_labels", "use_for"),
        ("avoid_labels", "avoid_for"),
    ):
        labels = gallery.get(field)
        slugs = data.get(slugs_field)
        slug_set = set(slugs) if isinstance(slugs, list) else set()
        if not isinstance(labels, dict):
            errors.append(f"gallery.{field}: 객체여야 합니다")
            continue
        for slug, label_text in labels.items():
            if slug not in slug_set:
                errors.append(
                    f"gallery.{field}.{slug}: {slugs_field}에 없는 용도입니다"
                )
            if not _nonempty_string(label_text):
                errors.append(
                    f"gallery.{field}.{slug}: 비어 있지 않은 문자열이어야 합니다"
                )
        missing = slug_set - set(labels)
        if missing:
            errors.append(
                f"gallery.{field}: 번역 없는 용도가 있습니다 ({', '.join(sorted(missing))})"
            )

    specimen = gallery.get("specimen")
    if specimen is not None:
        if not isinstance(specimen, dict):
            errors.append("gallery.specimen: 객체여야 합니다")
        else:
            for key in ("canvas", "ink", "accent"):
                if key not in specimen:
                    errors.append(f"gallery.specimen.{key}: 필수 키입니다")
            for key, value in specimen.items():
                if not isinstance(value, str) or HEX_COLOR_RE.fullmatch(value) is None:
                    errors.append(f"gallery.specimen.{key}: #RRGGBB 형식이어야 합니다")


def validate_manifest(data: Any, manifest: Path) -> list[str]:
    """한 매니페스트의 구조, 참조 관계, 로컬 파일 경계를 검증한다."""
    source = manifest.relative_to(ROOT)
    if not isinstance(data, dict):
        return [f"{source}: 최상위 JSON 값은 객체여야 합니다"]

    local_errors: list[str] = []
    missing = [field for field in CORE_FIELDS if field not in data]
    if missing:
        local_errors.append(f"필수 필드 누락: {', '.join(missing)}")

    for field in ("name", "description", "version"):
        if field in data and not _nonempty_string(data[field]):
            local_errors.append(f"{field}: 비어 있지 않은 문자열이어야 합니다")

    design_id = data.get("id")
    if design_id is not None and not _valid_name(design_id):
        local_errors.append("id: [a-z0-9-]+ 형식이어야 합니다")
    elif isinstance(design_id, str) and design_id != manifest.parent.name:
        local_errors.append(
            f"id: 폴더 이름과 같아야 합니다 ({design_id!r} != {manifest.parent.name!r})"
        )

    schema_version = data.get("schema_version", 1)
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version < 1
    ):
        local_errors.append("schema_version: 1 이상의 정수여야 합니다")
        schema_version = 1
    elif schema_version > SUPPORTED_SCHEMA_VERSION:
        local_errors.append(
            "schema_version: 이 번들러가 지원하는 최신 버전은 "
            f"{SUPPORTED_SCHEMA_VERSION}입니다"
        )

    profiles = data.get("profiles")
    valid_profiles: dict[str, list[str]] = {}
    if profiles is not None:
        if not isinstance(profiles, dict) or not profiles:
            local_errors.append("profiles: 비어 있지 않은 객체여야 합니다")
        else:
            for profile_name, layers in profiles.items():
                label = f"profiles.{profile_name}"
                if not _valid_name(profile_name):
                    local_errors.append(
                        f"{label}: 프로필 이름은 [a-z0-9-]+ 형식이어야 합니다"
                    )
                    continue
                checked_layers = _string_array(layers, label, local_errors)
                if checked_layers is None:
                    continue
                valid_profiles[profile_name] = checked_layers
                for index, layer in enumerate(checked_layers):
                    _file_path(
                        manifest.parent,
                        layer,
                        f"{label}[{index}]",
                        local_errors,
                    )

    aliases = data.get("aliases", {})
    valid_aliases: dict[str, str] = {}
    if not isinstance(aliases, dict):
        local_errors.append("aliases: 객체여야 합니다")
    else:
        for alias, target in aliases.items():
            label = f"aliases.{alias}"
            if not _valid_name(alias):
                local_errors.append(f"{label}: 별칭은 [a-z0-9-]+ 형식이어야 합니다")
                continue
            if alias in valid_profiles:
                local_errors.append(f"{label}: 프로필 이름과 중복될 수 없습니다")
                continue
            if not _valid_name(target):
                local_errors.append(f"{label}: 대상은 [a-z0-9-]+ 형식이어야 합니다")
                continue
            if target not in valid_profiles:
                local_errors.append(
                    f"{label}: 존재하지 않는 프로필 {target!r}을 가리킵니다"
                )
                continue
            valid_aliases[alias] = target

    default_profile = data.get("default_profile")
    if default_profile is not None:
        if not _valid_name(default_profile):
            local_errors.append(
                "default_profile: 프로필 이름은 [a-z0-9-]+ 형식이어야 합니다"
            )
        elif (
            default_profile not in valid_profiles
            and default_profile not in valid_aliases
        ):
            local_errors.append(
                f"default_profile: 존재하지 않는 프로필 {default_profile!r}입니다"
            )

    templates = data.get("templates", {})
    if not isinstance(templates, dict):
        local_errors.append("templates: 객체여야 합니다")
    else:
        for role, relative in templates.items():
            label = f"templates.{role}"
            if not _valid_name(role):
                local_errors.append(
                    f"{label}: 역할 이름은 [a-z0-9-]+ 형식이어야 합니다"
                )
            _file_path(manifest.parent, relative, label, local_errors)

    if "styleguide" in data:
        _file_path(manifest.parent, data["styleguide"], "styleguide", local_errors)
    if "design_doc" in data:
        _file_path(manifest.parent, data["design_doc"], "design_doc", local_errors)

    writing = data.get("writing")
    if writing is not None:
        if isinstance(writing, str):
            _file_path(manifest.parent, writing, "writing", local_errors)
        elif isinstance(writing, dict):
            if "guide" not in writing:
                local_errors.append("writing.guide: 필수 필드입니다")
            else:
                _file_path(
                    manifest.parent,
                    writing["guide"],
                    "writing.guide",
                    local_errors,
                )
            if "profiles" not in writing:
                local_errors.append("writing.profiles: 필수 필드입니다")
            else:
                _string_array(
                    writing["profiles"],
                    "writing.profiles",
                    local_errors,
                    names=True,
                )
        else:
            local_errors.append(
                "writing: 상대 파일 경로 또는 guide/profiles 객체여야 합니다"
            )

    if "project_scope" in data:
        _string_array(
            data["project_scope"],
            "project_scope",
            local_errors,
            names=True,
        )

    if "created" in data:
        _validate_iso_date(data["created"], "created", local_errors)

    if schema_version >= 2:
        status = data.get("status")
        if status not in STATUS_VALUES:
            local_errors.append(
                "status: draft, candidate, validated, deprecated 중 하나여야 합니다"
            )

        selection_arrays: dict[str, list[str]] = {}
        for field in ("mediums", "use_for", "avoid_for"):
            if field not in data:
                local_errors.append(f"{field}: schema v2 이상 필수 필드입니다")
            else:
                checked = _string_array(
                    data[field],
                    field,
                    local_errors,
                    names=True,
                )
                if checked is not None:
                    selection_arrays[field] = checked

        declared_formats: list[str] = []
        if "formats" not in data:
            local_errors.append("formats: schema v2 이상 필수 필드입니다")
        else:
            checked_formats = _string_array(
                data["formats"],
                "formats",
                local_errors,
                names=True,
            )
            if checked_formats is not None:
                declared_formats = checked_formats
                unknown_formats = set(checked_formats) - FORMAT_VALUES
                if unknown_formats:
                    local_errors.append(
                        "formats: 지원하지 않는 형식이 있습니다 "
                        f"({', '.join(sorted(unknown_formats))})"
                    )

        format_fit = data.get("format_fit")
        if not isinstance(format_fit, dict):
            local_errors.append("format_fit: 객체여야 합니다")
        else:
            if set(format_fit) != set(declared_formats):
                local_errors.append(
                    "format_fit: formats의 모든 형식을 한 번씩 포함해야 합니다"
                )
            for format_name, fit in format_fit.items():
                if format_name not in FORMAT_VALUES:
                    local_errors.append(
                        f"format_fit.{format_name}: 지원하지 않는 형식입니다"
                    )
                if fit not in FORMAT_FIT_VALUES:
                    local_errors.append(
                        f"format_fit.{format_name}: primary 또는 supported여야 합니다"
                    )

        if "format_guide" not in data:
            local_errors.append("format_guide: schema v2 이상 필수 필드입니다")
        else:
            _validate_format_guide(
                manifest.parent,
                data["format_guide"],
                design_id,
                declared_formats,
                local_errors,
            )

        overlap = set(selection_arrays.get("use_for", ())) & set(
            selection_arrays.get("avoid_for", ())
        )
        if overlap:
            local_errors.append(
                "use_for/avoid_for: 같은 용도를 동시에 포함할 수 없습니다 "
                f"({', '.join(sorted(overlap))})"
            )

        capabilities = data.get("capabilities")
        if not isinstance(capabilities, dict) or not capabilities:
            local_errors.append("capabilities: 비어 있지 않은 객체여야 합니다")
        else:
            for key, value in capabilities.items():
                if not _valid_key(key):
                    local_errors.append(
                        "capabilities: 키는 [a-z0-9_]+ 형식이어야 합니다"
                    )
                allowed = (
                    isinstance(value, (bool, str, int, float)) and value is not None
                )
                if isinstance(value, float) and not math.isfinite(value):
                    allowed = False
                if not allowed:
                    local_errors.append(
                        f"capabilities.{key}: boolean, string, number 중 하나여야 합니다"
                    )

        validation = data.get("validation")
        if not isinstance(validation, dict):
            local_errors.append("validation: 객체여야 합니다")
        else:
            if "checked_on" not in validation:
                local_errors.append("validation.checked_on: 필수 필드입니다")
            else:
                _validate_iso_date(
                    validation["checked_on"],
                    "validation.checked_on",
                    local_errors,
                )
            if "checks" not in validation:
                local_errors.append("validation.checks: 필수 필드입니다")
            else:
                _string_array(
                    validation["checks"],
                    "validation.checks",
                    local_errors,
                    names=True,
                )

        _validate_gallery(data, local_errors)

    if schema_version == 3:
        if data.get("kind") != "recipe":
            local_errors.append("kind: recipe여야 합니다")

        visibility = data.get("visibility")
        if visibility not in VISIBILITY_VALUES:
            local_errors.append("visibility: public 또는 private이어야 합니다")

        _string_array(
            data.get("pattern_refs"),
            "pattern_refs",
            local_errors,
            names=True,
        )

        declared_formats = (
            data.get("formats") if isinstance(data.get("formats"), list) else []
        )
        format_support = data.get("format_support")
        if not isinstance(format_support, dict):
            local_errors.append("format_support: 객체여야 합니다")
        else:
            if set(format_support) != set(declared_formats):
                local_errors.append(
                    "format_support: formats의 모든 형식을 한 번씩 포함해야 합니다"
                )
            for format_name, levels in format_support.items():
                label = f"format_support.{format_name}"
                checked_levels = _string_array(levels, label, local_errors, names=True)
                if checked_levels is None:
                    continue
                unknown_levels = set(checked_levels) - FORMAT_SUPPORT_VALUES
                if unknown_levels:
                    local_errors.append(
                        f"{label}: 지원하지 않는 수준이 있습니다 "
                        f"({', '.join(sorted(unknown_levels))})"
                    )
                if "guidance" not in checked_levels:
                    local_errors.append(f"{label}: guidance를 포함해야 합니다")

        provenance = data.get("provenance")
        if not isinstance(provenance, dict):
            local_errors.append("provenance: 객체여야 합니다")
        else:
            for field in ("origin", "license"):
                if not _nonempty_string(provenance.get(field)):
                    local_errors.append(
                        f"provenance.{field}: 비어 있지 않은 문자열이어야 합니다"
                    )
            references = provenance.get("references")
            if not isinstance(references, list):
                local_errors.append("provenance.references: 배열이어야 합니다")
            else:
                for index, reference in enumerate(references):
                    label = f"provenance.references[{index}]"
                    if not isinstance(reference, dict):
                        local_errors.append(f"{label}: 객체여야 합니다")
                        continue
                    for field in ("title", "url", "adopted"):
                        if not _nonempty_string(reference.get(field)):
                            local_errors.append(
                                f"{label}.{field}: 비어 있지 않은 문자열이어야 합니다"
                            )
                    url = reference.get("url")
                    if _nonempty_string(url) and not url.startswith("https://"):
                        local_errors.append(f"{label}.url: https URL이어야 합니다")
    return [f"{source}: {message}" for message in local_errors]


def load_designs(
    *,
    pattern_ids: set[str],
    show_legacy_warnings: bool = False,
) -> dict[str, dict[str, Any]]:
    designs: dict[str, dict[str, Any]] = {}
    seen_ids: dict[str, Path] = {}
    errors: list[str] = []
    legacy: list[str] = []
    manifests = sorted(ROOT.glob("*/design.json"))

    if not manifests:
        sys.exit(f"{ROOT} 아래에 design.json을 가진 디자인이 없습니다")

    for manifest in manifests:
        try:
            data = json.loads(
                manifest.read_text(encoding="utf-8"),
                parse_constant=_reject_json_constant,
            )
        except (OSError, json.JSONDecodeError, ValueError) as error:
            errors.append(
                f"{manifest.relative_to(ROOT)}: design.json 파싱 실패 — {error}"
            )
            continue

        errors.extend(validate_manifest(data, manifest))
        if not isinstance(data, dict):
            continue

        design_id = data.get("id")
        if isinstance(design_id, str):
            if design_id in seen_ids:
                first = seen_ids[design_id].relative_to(ROOT)
                second = manifest.relative_to(ROOT)
                errors.append(f"{second}: 중복 id {design_id!r} (먼저 선언: {first})")
            else:
                seen_ids[design_id] = manifest

        if not _valid_name(design_id):
            continue
        if data.get("schema_version") == 3:
            pattern_refs = data.get("pattern_refs")
            if isinstance(pattern_refs, list):
                unknown_patterns = {
                    pattern for pattern in pattern_refs if isinstance(pattern, str)
                } - pattern_ids
                if unknown_patterns:
                    errors.append(
                        f"{manifest.relative_to(ROOT)}: pattern_refs에 등록되지 않은 패턴이 있습니다 "
                        f"({', '.join(sorted(unknown_patterns))})"
                    )
        data["_dir"] = manifest.parent
        designs[design_id] = data
        if data.get("schema_version", 1) == 1:
            legacy.append(design_id)

    if errors:
        print("[매니페스트 오류]", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        sys.exit(1)

    if show_legacy_warnings:
        for design_id in legacy:
            print(
                f"[경고] {design_id}: legacy schema v1 — 선택 메타데이터가 준비되지 않았습니다",
                file=sys.stderr,
            )

    return designs


def get_design(designs: dict[str, dict[str, Any]], design_id: str) -> dict[str, Any]:
    if design_id not in designs:
        sys.exit(f"등록되지 않은 디자인: {design_id} (가능: {', '.join(designs)})")
    return designs[design_id]


def resolve_profile(design: dict[str, Any], profile: str | None) -> str:
    name = profile or design.get("default_profile")
    name = design.get("aliases", {}).get(name, name)
    if name not in design["profiles"]:
        options = ", ".join(design["profiles"])
        sys.exit(f"{design['id']}: 알 수 없는 프로필 {name!r} (가능: {options})")
    return name


def build_css(
    designs: dict[str, dict[str, Any]],
    design_id: str,
    profile: str | None,
) -> str:
    design = get_design(designs, design_id)
    layers = design["profiles"][resolve_profile(design, profile)]
    parts = []
    if SHARED_CORE.is_file():
        parts.append(SHARED_CORE.read_text(encoding="utf-8").rstrip() + "\n")
    for name in layers:
        path = design["_dir"] / name
        parts.append(path.read_text(encoding="utf-8").rstrip() + "\n")
    return "\n".join(parts)


def render_block(
    designs: dict[str, dict[str, Any]],
    design_id: str,
    profile: str | None,
) -> str:
    return "\n  <style>\n" + build_css(designs, design_id, profile) + "  </style>\n  "


def _read_html(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        print(f"[오류] {path}: 읽을 수 없습니다 — {error}", file=sys.stderr)
        return None


def process(
    designs: dict[str, dict[str, Any]],
    path: Path,
    cli_profile: str | None,
    *,
    check: bool,
) -> bool:
    """파일 안의 모든 마커를 갱신하거나 현재 번들과 일치하는지 검사한다."""
    text = _read_html(path)
    if text is None:
        return False
    matches = list(MARKER_RE.finditer(text))
    if not matches:
        print(
            f"[오류] {path}: <design>:styles 마커가 없습니다",
            file=sys.stderr,
        )
        return False

    ok = True
    updated = text
    for match in reversed(matches):
        design_id = match.group("design")
        marker_profile = match.group("profile")
        selected_profile = marker_profile or cli_profile
        design = get_design(designs, design_id)
        profile_name = resolve_profile(design, selected_profile)
        new_body = render_block(designs, design_id, selected_profile)
        in_sync = match.group("body") == new_body
        label = f"{design_id}/{profile_name}"
        if check:
            state = "일치" if in_sync else "어긋남"
            print(f"[{state}] {path} ({label})")
            ok = ok and in_sync
        elif in_sync:
            print(f"[유지] {path} ({label})")
        else:
            updated = (
                updated[: match.start("body")] + new_body + updated[match.end("body") :]
            )
            print(f"[갱신] {path} ({label})")

    if not check and updated != text:
        try:
            path.write_text(updated, encoding="utf-8")
        except OSError as error:
            print(f"[오류] {path}: 쓸 수 없습니다 — {error}", file=sys.stderr)
            return False
    return ok


def check_ready(
    designs: dict[str, dict[str, Any]],
    path: Path,
    cli_profile: str | None,
) -> bool:
    """완성 HTML의 스타일 동기화와 대표적인 미치환 값을 함께 검사한다."""
    ok = process(designs, path, cli_profile, check=True)
    text = _read_html(path)
    if text is None:
        return False

    for label, pattern in READY_PATTERNS:
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            preview = match.group(0)
            print(f"[미치환] {path}:{line}: {label} — {preview}", file=sys.stderr)
            ok = False

    if ok:
        print(f"[준비됨] {path}")
    return ok


def list_designs(designs: dict[str, dict[str, Any]]) -> None:
    for design in designs.values():
        profiles = ", ".join(design["profiles"])
        print(f"{design['id']}  —  {design.get('name', design['id'])}")
        print(f"  {design.get('description', '')}")
        if design.get("formats"):
            print(f"  형식: {', '.join(design['formats'])}")
        print(f"  프로필: {profiles} (기본 {design.get('default_profile')})")
        for role, relative in design.get("templates", {}).items():
            print(f"  템플릿[{role}]: {design['_dir'].relative_to(ROOT)}/{relative}")
        if design.get("styleguide"):
            print(
                f"  스타일가이드: "
                f"{design['_dir'].relative_to(ROOT)}/{design['styleguide']}"
            )


def build_catalog(
    designs: dict[str, dict[str, Any]],
    library: dict[str, Any],
    patterns: list[dict[str, Any]],
) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for design in designs.values():
        if design.get("visibility") != "public":
            continue
        entry = {key: value for key, value in design.items() if key != "_dir"}
        entry["selection_ready"] = (
            entry.get("schema_version", 1) == SUPPORTED_SCHEMA_VERSION
            and entry.get("status") == "validated"
            and entry.get("visibility") == "public"
        )
        entries.append(entry)
    return {
        "catalog_schema_version": 2,
        "library": library,
        "patterns": patterns,
        "recipes": entries,
    }


def emit_catalog(
    designs: dict[str, dict[str, Any]],
    library: dict[str, Any],
    patterns: list[dict[str, Any]],
) -> None:
    catalog = build_catalog(designs, library, patterns)
    json.dump(catalog, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def _public_source_files(designs: dict[str, dict[str, Any]]) -> list[tuple[Path, Path]]:
    """공개 묶음에 포함할 원본과 묶음 안 상대 경로를 돌려준다."""
    files: list[tuple[Path, Path]] = [
        (LIBRARY_METADATA, Path("library.json")),
        (PATTERNS, Path("patterns.json")),
    ]
    if LICENSE_FILE.is_file():
        files.append((LICENSE_FILE, Path("LICENSE")))
    if SHARED_CORE.is_file():
        files.append((SHARED_CORE, Path("shared/core.css")))

    for design in designs.values():
        if design.get("visibility") != "public":
            continue
        design_dir = design["_dir"]
        for source in sorted(design_dir.rglob("*")):
            if not source.is_file() or source.name == ".DS_Store":
                continue
            relative = Path("recipes") / design["id"] / source.relative_to(design_dir)
            files.append((source, relative))
    return files


def _content_hash(files: list[tuple[Path, Path]]) -> str:
    digest = hashlib.sha256()
    for source, relative in sorted(files, key=lambda item: item[1].as_posix()):
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(source.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def export_public_pack(
    designs: dict[str, dict[str, Any]],
    library: dict[str, Any],
    patterns: list[dict[str, Any]],
    target: Path,
) -> None:
    """공개 레시피와 자산을 원자적으로 교체 가능한 휴대용 묶음으로 만든다."""
    target = target.resolve()
    if target == ROOT or target in ROOT.parents or ROOT in target.parents:
        sys.exit(f"공개 묶음 출력 경로가 원본을 덮을 수 없습니다: {target}")

    staging = target.parent / f".{target.name}.staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    files = _public_source_files(designs)
    for source, relative in files:
        destination = staging / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    catalog = build_catalog(designs, library, patterns)
    catalog["content_sha256"] = _content_hash(files)
    (staging / "catalog.json").write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (staging / "README.md").write_text(
        "# Design Reference Library pack\n\n"
        "Design 플러그인이 필요할 때만 읽거나 결과물에 적용하는 공개 참고 묶음입니다. "
        "`catalog.json`에서 패턴과 레시피를 찾고, `recipes/`의 원칙·형식 규칙과 자산을 "
        "현재 프로젝트에 맞게 선택적으로 사용합니다. 기존 프로젝트의 디자인 시스템을 "
        "이 묶음으로 교체하지 않습니다.\n",
        encoding="utf-8",
    )

    if target.exists():
        shutil.rmtree(target)
    staging.replace(target)
    print(
        f"[공개 묶음] {target} — {len(catalog['recipes'])}개 레시피, "
        f"sha256 {catalog['content_sha256']}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--design",
        help="stdout 모드에서 사용할 디자인 id (하나뿐이면 생략 가능)",
    )
    parser.add_argument(
        "--profile",
        help="프로필 이름 (마커에 profile 속성이 없을 때 사용)",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--inject", nargs="+", type=Path, metavar="HTML")
    group.add_argument("--check", nargs="+", type=Path, metavar="HTML")
    group.add_argument("--ready", nargs="+", type=Path, metavar="HTML")
    group.add_argument("--list", action="store_true")
    group.add_argument("--catalog", action="store_true")
    group.add_argument("--validate", action="store_true")
    group.add_argument("--export-public", type=Path, metavar="DIR")
    args = parser.parse_args()

    library = load_library_metadata()
    patterns = load_patterns()
    designs = load_designs(
        pattern_ids={pattern["id"] for pattern in patterns},
        show_legacy_warnings=args.validate,
    )

    if args.list:
        list_designs(designs)
    elif args.catalog:
        emit_catalog(designs, library, patterns)
    elif args.export_public:
        export_public_pack(designs, library, patterns, args.export_public)
    elif args.validate:
        if SHARED_CORE.is_file():
            print(f"[공용] {SHARED_CORE.relative_to(ROOT)} — 모든 프로필 앞에 포함")
        for design in designs.values():
            schema_version = design.get("schema_version", 1)
            if (
                schema_version == SUPPORTED_SCHEMA_VERSION
                and design.get("status") == "validated"
            ):
                state = "자동 선택 준비"
            elif schema_version == SUPPORTED_SCHEMA_VERSION:
                state = f"자동 선택 보류: {design.get('status')}"
            else:
                state = "legacy 허용"
            print(f"[유효] {design['id']} (schema v{schema_version}, {state})")
    elif args.inject:
        ok = all(
            process(designs, path, args.profile, check=False) for path in args.inject
        )
        sys.exit(0 if ok else 1)
    elif args.check:
        ok = all(
            process(designs, path, args.profile, check=True) for path in args.check
        )
        sys.exit(0 if ok else 1)
    elif args.ready:
        ok = all(check_ready(designs, path, args.profile) for path in args.ready)
        sys.exit(0 if ok else 1)
    else:
        design_id = args.design or (next(iter(designs)) if len(designs) == 1 else None)
        if not design_id:
            sys.exit(f"--design을 지정하세요 (가능: {', '.join(designs)})")
        sys.stdout.write(build_css(designs, design_id, args.profile))


if __name__ == "__main__":
    main()
