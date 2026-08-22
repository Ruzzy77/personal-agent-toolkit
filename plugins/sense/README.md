# Sense

Sense는 여러 AI가 사용자와 일할 때 참고하는 하나의 비공개 작업 프로필을 관리한다. 프로필은
여러 작업에 적용되는 목적·책임·협업 방식과 선택 기준을 담는다. 프로젝트 자료와 결과는 각
프로젝트가 관리하고, 출처 기반 지식은 Corpus가, 잠정적인 사용자 해석은 Hypes가 관리한다.

현재 요청과 현재 자료가 우선한다. Sense는 중요한 해석과 선택에 장기 맥락을 제공한다. 단순
조회와 한 단계 실행은 현재 입력으로 수행한다.

## 도구

| 도구 | 역할 |
| --- | --- |
| `sense_read` | 색인 또는 필요한 항목만 읽음 |
| `sense_overview` | 민감 항목을 제외한 현재 프로필을 검토 화면에 표시함 |
| `sense_revise` | 최종 항목 교체를 한 트랜잭션으로 저장함 |

Sense 개정은 관련 항목의 `section_sha256`와 최종 항목 전체를 한 번에 전달한다. 항목 변경
충돌은 현재 내용을 보존하며, 동일한 최종 상태는 무변경 결과를 반환한다. 개정은 현재 프로필의
원자적 교체로 이루어진다.

에이전트가 문안을 작성했거나 여러 항목을 함께 바꿀 때에는 Chat에서 최종 문안을 먼저 보여 준다.
민감 항목의 저장과 영구 삭제는 로컬 명령에서 처리한다.

## 저장 위치

기본 위치는 다음과 같다.

```text
~/Library/Application Support/Sense/
├── sense.sqlite3
└── runtime.lock
```

디렉터리는 `0700`, 데이터베이스와 잠금 파일은 `0600`으로 유지한다. 데이터베이스에는 현재
프로필 하나를 저장한다. 대화 전문, 작업 로그, 원자료와 출처 위치는 각 원본 시스템에서 관리한다.
이전 스키마를 처음 열면 현재 활성 프로필을 새 형식으로 옮기고 현재 데이터 모델로 정리한다.

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

항목 또는 전체 데이터의 영구 삭제에는 로컬 확인 플래그가 필요합니다.
