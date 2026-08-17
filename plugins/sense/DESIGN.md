# Sense 설계

Sense는 여러 작업에서 계속 적용되는 사용자 통제형 작업 프로필을 로컬에 보관한다.
플랫폼마다 별도 사본을 만들지 않고 Codex와 Claude가 같은 저장소를 읽는다.

## 소유 경계

| 대상 | 소유자 |
| --- | --- |
| 여러 작업에 적용되는 목적, 책임, 협업 방식과 선택 기준 | Sense |
| 한 업무의 원자료, 질문, 관계와 이어 갈 맥락 | Corpus |
| 에이전트의 수정 가능한 사용자 관계 모델 | Hypes |
| 프로젝트 사실, 코드와 산출물 | 각 프로젝트 |
| 대화의 자연스러운 연속성 | 각 플랫폼의 메모리 기능 |

완료 작업과 대화를 시간순으로 쌓지 않는다. 다음 선택을 바꾸는 내용이 확인됐을 때 기존
프로필의 관련 항목을 다시 쓴다. 프로젝트 안에서만 유효한 사실은 프로젝트나 Corpus에
남기고, Hypes의 잠정적인 관계를 Sense로 옮기지 않는다.

## 프로필

프로필은 하나의 문서이며 각 항목은 독립적으로 교체할 수 있다.

```json
{
  "schema_version": 1,
  "revision": 12,
  "sections": [
    {
      "id": "relationship-and-agency",
      "purpose": "이 항목을 참고할 이유",
      "text": "현재 적용할 내용",
      "origins": ["user_set", "learned_from_work"],
      "use_for": ["참고할 작업"],
      "review_when": ["다시 확인할 상황"],
      "sensitivity": "ordinary",
      "source_refs": []
    }
  ],
  "controls": {
    "raw_conversation_storage": "never",
    "sensitive_persistence": "explicit_confirmation",
    "external_effects": "responsibility_based",
    "provider_memory_management": "provider_owned"
  }
}
```

`source_refs`에는 원자료 본문 대신 다시 찾을 수 있는 제한된 식별자와 해시만 둔다.
민감 항목은 일반 색인과 검토 화면에서 제외한다. 원자료 위치와 내부 해시는 사용자가
전체 점검을 요청한 경우에만 보여 준다.

## 저장과 수정

기본 저장 위치는 `~/Library/Application Support/Sense/`다. 디렉터리는 `0700`,
데이터베이스와 잠금 파일은 `0600`으로 유지한다. 현재본과 최근 12개 개정본을 SQLite에
저장하며, 삭제한 의미는 남아 있는 개정본에서도 제거한다.

수정은 다음 경계를 지킨다.

- 실제 파일 잠금과 SQLite 트랜잭션 안에서 한 번에 저장
- 읽을 때의 개정 번호와 변경 대상 항목 해시 확인
- 한 묶음의 모든 변경이 유효할 때만 한 개정으로 반영
- 같은 idempotency key와 같은 요청은 최초 결과를 반환
- 민감 항목 또는 적용 범위를 넓히는 변경은 신뢰할 수 있는 로컬 확인 필요
- 삭제 전 정확한 대상과 현재 상태의 digest 확인

관련 없는 항목이 먼저 바뀌어도 대상 항목 해시가 같으면 저장할 수 있다. 대상 항목이
달라졌다면 덮어쓰지 않고 충돌을 반환한다.

## MCP 표면

Sense는 여섯 개의 도구만 제공한다.

| 도구 | 상태 변화 |
| --- | --- |
| `sense_read` | 없음 |
| `sense_overview` | 없음 |
| `sense_preview_revision` | 없음 |
| `sense_revise_batch` | 있음 |
| `sense_control` | 일반 MCP에서는 없음 |
| `sense_status` | 없음 |

한 항목용 legacy 수정 도구는 두지 않는다. 한 항목을 고칠 때도 변경 묶음 하나로 처리한다.
프로필 활성화와 실제 삭제는 일반 모델 도구가 실행하지 못하며 로컬 제어 경로가 맡는다.

MCP는 표준 입력 방식으로 실행한다. 개인용 Chat 연결에서는 Personal Agent Tunnel이 같은
로컬 서버를 loopback에서 연결한다. Sense 안에 별도 원격 adapter, 다중 사용자 저장소,
데이터 이관 계층이나 OAuth 서버를 두지 않는다.

## 검토 화면

`sense_overview`는 다음 정보만 표시한다.

- 일반 항목의 제목, 본문과 참고할 상황
- 사용자가 직접 정한 내용인지 작업에서 배운 내용인지
- 실제 비교가 가능한 경우의 최근 변경

민감한 본문, 원자료 locator, 해시, 내부 식별값과 삭제 확인값은 표시하지 않는다. 수정,
삭제와 내보내기는 정확한 범위를 보여 줄 수 있는 로컬 제어 화면에서 수행한다.

## Plugin 구성

이 plugin 폴더가 OpenAI와 Claude에서 함께 사용하는 정본이다. 실행 코드, 스킬, 읽기 전용 UI,
launcher, manifest와 잠긴 실행 의존성을 포함하며, 작업 프로필과 데이터베이스는 포함하지 않는다.
marketplace는 이 폴더를 직접 설치하므로 별도 생성 사본과 package builder를 두지 않는다.

## 개발 판단

제품 동작보다 큰 검증 구조를 두지 않는다. 일상 변경은 수정한 파일의 기본 검사와 직접
관련된 핵심 사례 한두 개로 확인한다. 데이터 손실, 권한 침범, 원자적 저장과 재현된 핵심
장애를 기존 사례로 확인할 수 없을 때만 테스트를 추가한다.

평가 corpus, golden 결과, 발간 도구와 배포 준비 자료는 운영 코드의 정본이 아니다. 실제
배포에서는 현재 plugin의 설치 경로와 시작만 확인하며, 그 절차를 상시 회귀 체계로 유지하지
않는다.
