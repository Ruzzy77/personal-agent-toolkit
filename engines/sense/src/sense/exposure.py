"""Bounded views of the single Sense profile."""

from __future__ import annotations

from typing import Any

from .model import ProfileDocument, ProfileSection, section_sha256

SECTION_PRESENTATION = {
    "questions-and-choices": {
        "title": "질문과 선택",
        "group": "질문과 답",
    },
    "scope-and-checking": {
        "title": "업무 범위",
        "group": "질문과 답",
    },
    "evidence-and-judgment": {
        "title": "자료와 해석",
        "group": "자료와 표현",
    },
    "explanation-and-output": {
        "title": "설명과 산출물 구성",
        "group": "자료와 표현",
    },
    "conversation-and-writing": {
        "title": "대화와 글",
        "group": "자료와 표현",
    },
    "visual-production": {
        "title": "시각 설계와 제작",
        "group": "자료와 표현",
    },
    "research-exploration": {
        "title": "연구 탐색",
        "group": "연구",
    },
    "research-review": {
        "title": "연구 검토",
        "group": "연구",
    },
    "what-to-keep": {
        "title": "기억 체계",
        "group": "장기 맥락",
    },
}
GROUP_ORDER = ("질문과 답", "자료와 표현", "연구", "장기 맥락", "기타 지침")
ORIGIN_LABELS = {
    "user_set": "사용자 지정",
    "learned_from_results": "경험 학습",
}


def section_view(
    section: ProfileSection,
    *,
    include_change_token: bool,
) -> dict[str, Any]:
    result = section.model_dump(mode="json")
    if include_change_token:
        result["section_sha256"] = section_sha256(section)
    return result


def profile_index(profile: ProfileDocument) -> list[dict[str, Any]]:
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
                "sensitivity": section.sensitivity,
            }
        )
    return items


def guidance_overview(
    profile: ProfileDocument,
    *,
    updated_at: str,
    section_skills: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return the ordinary guidance shown by the review component."""

    grouped: dict[str, list[dict[str, Any]]] = {group: [] for group in GROUP_ORDER}
    for section in profile.sections:
        if section.sensitivity == "sensitive":
            continue
        presentation = SECTION_PRESENTATION.get(
            section.id,
            {
                "title": "기타 지침",
                "group": "기타 지침",
            },
        )
        item = {
            "title": presentation["title"],
            "purpose": section.purpose,
            "text": section.text,
            "origins": [ORIGIN_LABELS[origin] for origin in section.origins],
        }
        skill = (section_skills or {}).get(section.id)
        if skill is not None:
            item["skill"] = skill
        grouped[presentation["group"]].append(item)

    groups = [
        {"title": group, "sections": grouped[group]}
        for group in GROUP_ORDER
        if grouped[group]
    ]
    return {
        "title": "Sense",
        "source": "local",
        "groups": groups,
        "updated_at": updated_at,
    }
