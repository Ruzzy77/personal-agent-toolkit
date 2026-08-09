"""Bounded profile views for local and future remote surfaces."""

from __future__ import annotations

import re
from typing import Any

from .model import ProfileDocument, ProfileSection, section_sha256

SECTION_PRESENTATION = {
    "working-together": {
        "title": "결정과 책임",
        "group": "함께 일하기",
    },
    "work-process": {
        "title": "일을 진행하는 방식",
        "group": "함께 일하기",
    },
    "evidence-and-judgment": {
        "title": "자료를 읽는 기준",
        "group": "자료와 표현",
    },
    "conversation-and-writing": {
        "title": "대화와 글",
        "group": "자료와 표현",
    },
    "research-and-long-term-goals": {
        "title": "연구와 장기 작업",
        "group": "오래 이어갈 일",
    },
    "learning-across-work": {
        "title": "작업에서 배우는 방식",
        "group": "오래 이어갈 일",
    },
}
GROUP_ORDER = ("함께 일하기", "자료와 표현", "오래 이어갈 일", "그 밖의 내용")
ORIGIN_LABELS = {
    "user_set": "직접 설정",
    "learned_from_work": "작업에서 배운 것",
}
SOURCE_LABELS = {
    ("user_set", "conversation"): "직접 확인한 대화",
    ("user_set", "file"): "직접 설정한 내용",
    ("user_set", "corpus"): "직접 확인한 업무 자료",
    ("user_set", "result"): "직접 확인한 작업 결과",
    ("learned_from_work", "conversation"): "작업에서 확인한 대화",
    ("learned_from_work", "file"): "작업을 통해 확인한 자료",
    ("learned_from_work", "corpus"): "업무에서 이어 온 이해",
    ("learned_from_work", "result"): "작업에서 확인한 결과",
}
DISPLAY_VALUE_END = r"""(?=(?:[.;,!?)}\]](?:\s|$))|["'<>`]|[\r\n]|$)"""
DISPLAY_LOCATOR_PATTERNS = (
    re.compile(
        r"\b[a-zA-Z][a-zA-Z0-9+.-]*://[^<>\r\n\"']*?"
        + DISPLAY_VALUE_END
    ),
    re.compile(r"git:[0-9a-f]{40}:[^<>\r\n\"']*?" + DISPLAY_VALUE_END),
    re.compile(r"(?<![\w:])/(?!/)[^<>\r\n\"']*?" + DISPLAY_VALUE_END),
    re.compile(r"\b[A-Za-z]:[/\\][^<>\r\n\"']*?" + DISPLAY_VALUE_END),
    re.compile(r"\\\\[^<>\r\n\"']*?" + DISPLAY_VALUE_END),
    re.compile(r"\b[0-9a-fA-F]{64}\b"),
)
UNSAFE_DIRECTIONAL_CODEPOINTS = {
    *range(0x202A, 0x202F),
    *range(0x2066, 0x206A),
}


def section_view(
    section: ProfileSection,
    *,
    include_sources: bool,
    include_change_token: bool,
) -> dict[str, Any]:
    result = section.model_dump(mode="json")
    if not include_sources:
        result.pop("source_refs", None)
    if include_change_token:
        result["section_sha256"] = section_sha256(section)
    return result


def local_profile_index(profile: ProfileDocument) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for section in profile.sections:
        if section.sensitivity == "sensitive":
            items.append(
                {
                    "id": section.id,
                    "sensitivity": section.sensitivity,
                    "available_by_explicit_id": True,
                }
            )
            continue
        items.append(
            {
                "id": section.id,
                "purpose": section.purpose,
                "use_for": section.use_for,
                "sensitivity": section.sensitivity,
            }
        )
    return items


def _source_summaries(section: ProfileSection) -> list[dict[str, Any]]:
    counts: dict[tuple[str, str], int] = {}
    for source in section.source_refs:
        key = (source.origin, source.kind)
        counts[key] = counts.get(key, 0) + 1
    return [
        {
            "label": SOURCE_LABELS[key],
            "count": count,
        }
        for key, count in counts.items()
    ]


def _display_text(value: str, exact_redactions: tuple[str, ...]) -> str:
    result = "".join(
        character
        for character in value
        if (
            character in {"\n", "\t"}
            or (
                ord(character) >= 32
                and not 0x7F <= ord(character) <= 0x9F
                and ord(character) not in UNSAFE_DIRECTIONAL_CODEPOINTS
            )
        )
    )
    for redaction in exact_redactions:
        result = result.replace(redaction, "[연결된 자료]")
    for pattern in DISPLAY_LOCATOR_PATTERNS:
        result = pattern.sub("[연결된 자료]", result)
    return result


def work_profile_overview(
    profile: ProfileDocument,
    *,
    lifecycle: str,
    updated_at: str,
) -> dict[str, Any]:
    """Return the source-free product view used by the review component."""

    grouped: dict[str, list[dict[str, Any]]] = {group: [] for group in GROUP_ORDER}
    exact_redactions = tuple(
        sorted(
            {
                value
                for section in profile.sections
                for source in section.source_refs
                for value in (source.locator, source.sha256)
            },
            key=len,
            reverse=True,
        )
    )
    for section in profile.sections:
        if section.sensitivity == "sensitive":
            continue
        presentation = SECTION_PRESENTATION.get(
            section.id,
            {
                "title": "추가로 참고할 내용",
                "group": "그 밖의 내용",
            },
        )
        grouped[presentation["group"]].append(
            {
                "title": presentation["title"],
                "purpose": _display_text(section.purpose, exact_redactions),
                "text": _display_text(section.text, exact_redactions),
                "origins": [ORIGIN_LABELS[origin] for origin in section.origins],
                "related_work": [
                    _display_text(item, exact_redactions)
                    for item in section.use_for
                ],
                "review_when": [
                    _display_text(item, exact_redactions)
                    for item in section.review_when
                ],
                "sources": _source_summaries(section),
            }
        )

    groups = [
        {"title": group, "sections": grouped[group]}
        for group in GROUP_ORDER
        if grouped[group]
    ]
    return {
        "title": "작업 프로필",
        "description": (
            "여러 AI가 함께 일할 때 공통으로 참고하는 내용입니다. "
            "대화 기록과 프로젝트 자료는 이곳에 복사하지 않습니다."
        ),
        "state": {
            "label": "미리보기" if lifecycle == "preview" else "사용 중",
            "description": (
                "지금은 요청할 때만 이 내용을 참고합니다."
                if lifecycle == "preview"
                else "해석하거나 선택할 일이 있는 작업에서 이 내용을 참고합니다."
            ),
        },
        "groups": groups,
        "recent_change": (
            "처음 만든 작업 프로필입니다."
            if profile.revision == 1
            else ""
        ),
        "updated_at": updated_at,
        "privacy": [
            "대화 원문은 저장하지 않습니다.",
            "민감한 내용은 사용자가 저장을 직접 승인한 경우에만 남깁니다.",
            "민감하게 표시된 내용은 이 화면에 나타내지 않습니다.",
            "각 앱의 대화 기억은 해당 앱이 관리합니다.",
        ],
    }


def remote_profile_view(profile: ProfileDocument) -> dict[str, Any]:
    """Return a source-free view suitable for a future authenticated web gateway."""

    return {
        "schema_version": profile.schema_version,
        "revision": profile.revision,
        "sections": [
            section_view(
                section,
                include_sources=False,
                include_change_token=False,
            )
            for section in profile.sections
            if section.sensitivity == "ordinary"
        ],
        "controls": profile.controls.model_dump(mode="json"),
    }
