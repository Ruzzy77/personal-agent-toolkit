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
        "title": "일의 크기와 쓰임",
        "group": "질문과 답",
    },
    "evidence-and-judgment": {
        "title": "자료와 해석",
        "group": "자료와 표현",
    },
    "conversation-and-writing": {
        "title": "대화와 글",
        "group": "자료와 표현",
    },
    "research-and-long-term-goals": {
        "title": "관계 학습 연구",
        "group": "오래 이어갈 내용",
    },
    "what-to-keep": {
        "title": "무엇을 남길지",
        "group": "오래 이어갈 내용",
    },
}
GROUP_ORDER = ("질문과 답", "자료와 표현", "오래 이어갈 내용", "그 밖의 내용")
ORIGIN_LABELS = {
    "user_set": "사용자가 정함",
    "learned_from_results": "함께 작업하며 배움",
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
) -> dict[str, Any]:
    """Return the ordinary guidance shown by the review component."""

    grouped: dict[str, list[dict[str, Any]]] = {group: [] for group in GROUP_ORDER}
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
                "purpose": section.purpose,
                "text": section.text,
                "origins": [ORIGIN_LABELS[origin] for origin in section.origins],
            }
        )

    groups = [
        {"title": group, "sections": grouped[group]}
        for group in GROUP_ORDER
        if grouped[group]
    ]
    return {
        "title": "Sense에서 참고하는 내용",
        "description": (
            "여러 대화에서 이어지는 사용자 의도와 선택을 정리한 내용입니다. "
            "대화 기록과 프로젝트 자료는 각각의 시스템에서 관리합니다."
        ),
        "groups": groups,
        "updated_at": updated_at,
        "privacy": [
            "Sense는 장기 지침을 저장합니다.",
            "민감 정보 저장은 사용자의 직접 승인을 따릅니다.",
            "이 화면은 일반 지침을 표시합니다.",
        ],
    }
