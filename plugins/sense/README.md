# Sense

Sense는 여러 AI가 공유하는 비공개 작업 프로필이다. 프로필은 범용 지침을 저장하며, 각 항목에는
그 지침과 이어지는 작업 방법을 Section Skill로 둘 수 있다. 프로젝트 정보는 각 프로젝트가,
출처 기반 지식은 Corpus가, 사용자 모델은 Hypes가 관리한다.

현재 요청과 자료가 우선한다. Sense는 의사결정에 범용 지침을 제공한다. 단순 조회와 단일 작업은
현재 입력으로 수행한다.

## 도구

| 도구 | 역할 |
| --- | --- |
| `sense_read` | 색인 또는 관련 항목과 연결 Skill 조회 |
| `sense_overview` | 일반 프로필과 연결 Skill 표시 |
| `sense_revise` | 최종 항목 교체를 한 트랜잭션으로 저장함 |
| `sense_skill_revise` | 일반 Section Skill 전체를 최신 버전과 대조해 교체 |

`sense_read`의 색인은 Section Skill의 이름과 설명만 보여 준다. 관련 항목을 열면 항목 본문과
Skill 전체 지침을 함께 반환한다. Section Skill은 사용자가 반영한 `SKILL.md`이며, 자료나 사실의
출처로 사용하지 않는다.

Sense 개정은 관련 항목의 `section_sha256`와 최종 항목 전체를 한 번에 전달한다. 항목 변경
충돌은 현재 내용을 보존하며, 동일한 최종 상태는 무변경 결과를 반환한다. 개정은 현재 프로필의
원자적 교체로 이루어진다.

에이전트가 문안을 작성했거나 여러 항목을 함께 바꿀 때에는 Chat에서 최종 문안을 먼저 보여 준다.
일반 Section Skill도 현재 `version`과 전체 교체안을 사용해 Chat에서 수정할 수 있다. 민감 항목과
민감 Section Skill의 저장, Skill 제거와 영구 삭제는 로컬 명령에서 처리한다.

## 저장 위치

기본 위치는 다음과 같다.

```text
~/Library/Application Support/Sense/
├── sense.sqlite3
├── runtime.lock
└── sections/
    └── <section-id>/
        └── skill/
            └── SKILL.md
```

디렉터리는 `0700`, 데이터베이스와 잠금 파일 및 Section Skill은 `0600`으로 유지한다.
데이터베이스에는 현재 프로필 하나를 저장하고, Section Skill은 연결된 항목의 비공개 폴더에 둔다.
대화 전문, 작업 로그, 원자료와 출처 위치는 각 원본 시스템에서 관리한다. 이전 스키마를 처음 열면
현재 활성 프로필을 새 형식으로 옮기고 현재 데이터 모델로 정리한다.

## 로컬 관리

```sh
uv sync
./launchers/sense read --view full
./launchers/sense status
```

프로필을 처음 가져오거나 기존 프로필을 교체할 수 있습니다.

```sh
./launchers/sense import-profile --input profile.json
./launchers/sense import-profile --input profile.json --replace --confirm-replace
```

검토한 `SKILL.md`는 다음과 같이 한 항목에 반영한다. `expected-version`에는 새로 만들 때
`absent`, 교체할 때 현재 Skill의 버전을 넣는다.

```sh
./launchers/sense section skill show --id conversation-and-writing
./launchers/sense section skill set \
  --id conversation-and-writing \
  --skill-file /path/to/SKILL.md \
  --expected-version absent \
  --confirm-section-skill-write
```

항목 또는 전체 데이터의 영구 삭제에는 로컬 확인 플래그가 필요합니다.
