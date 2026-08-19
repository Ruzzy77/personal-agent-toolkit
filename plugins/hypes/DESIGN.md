# Hypes 설계

Hypes는 에이전트가 현재 사용자를 이해하기 위해 만든 수정 가능한 관계를 로컬 SQLite에 저장한다.
그래프 서버, 임베딩 모델, 별도 추론 서비스나 세션 상태는 사용하지 않는다.

## 구조

```text
nodes
  node_id, labels_json, name, description, aliases_json, attributes_json

predicates
  predicate_id, name, description, aliases_json

edges
  edge_id, source_id, predicate_id, target_id, qualifiers_json
```

영속 참조는 서버가 `node_<uuid>`, `pred_<uuid>`, `edge_<uuid>` 형태로 만든다. Node와
Predicate의 이름·별칭·설명은 SQLite FTS5로 찾고, Edge는 source·target·predicate index로
탐색한다.

## 쓰기

`hypes_rewrite`는 `put_node`, `put_predicate`, `put_edge`, `delete`만 받는다. 새 객체는
`$concept` 같은 patch 내부 참조를 사용할 수 있고, 기존 영속 참조에 put하면 ID를 유지한 채
값 전체를 교체한다.

한 patch는 다음 순서로 적용한다.

1. 입력과 참조 유형 확인
2. 임시 참조에 영속 ID 배정
3. Node·Predicate 저장
4. 명시된 Edge 삭제와 저장
5. Node·Predicate 삭제
6. 외래 키 확인 후 commit

연결된 객체를 자동 cascade로 지우지 않는다. 어느 단계든 실패하면 FTS 변경을 포함해 전체
트랜잭션을 rollback한다.

## 읽기

`hypes_read`는 `focus` 또는 `seed_refs`에서 시작하고 최대 두 단계까지 그래프를 확장한다.
둘 다 없으면 이름순 목차를 반환한다. 목차가 `limit`을 넘으면 다음 위치를 나타내는
`continuation`을 돌려준다.

반환되는 모든 Edge의 source Node, target Node와 Predicate는 같은 결과에 포함된다. 전체
객체 수가 `limit`을 넘지 않도록 닫힌 조각을 만들기 때문에 결과가 상한보다 작을 수 있다.
일반 사용은 짧은 검색어와 한 단계 이내의 확장으로 충분하며, 전체 목차 순회는 사용자가 모델
전체를 요청한 경우에만 한다.

서버는 관계의 의미를 판정하거나 다음 조회를 자동 실행하지 않는다. 추가 read 상태, 복구 token,
세션별 orchestration이나 legacy 입력을 별도 계약으로 두지 않는다.

## MCP 경계

공개 도구는 `hypes_read`와 `hypes_rewrite` 두 개다. 현재 상호작용에서 이후에도 유용할 관계가
드러나거나 기존 이해가 달라지면 에이전트가 별도의 저장 요청·미리보기·확인 없이 rewrite할 수
있다. 기존 관계를 적용했다는 사실이나 작업을 완료했다는 사실만으로는 쓰지 않는다. 모든 저장
관계는 사용자 승인 사실이 아니라 이후 상호작용에서 다시 바꿀 수 있는 에이전트의 현재 모델이다.

표준 입력 MCP가 기본이다. 개인용 Chat 연결에서는 Personal Agent Tunnel이 같은 로컬 서버를
loopback에서 연결한다. Hypes는 별도 원격 저장소나 호스팅 계층을 소유하지 않는다.

## 저장

기본 데이터 디렉터리는 `~/Library/Application Support/Hypes/`이고 파일 이름은
`hypes-ontology.sqlite3`다. 디렉터리는 `0700`, 데이터베이스와 SQLite 보조 파일은 `0600`으로
유지한다. 경로, 소유자, 파일 종류와 권한을 SQLite가 열기 전에 확인한다.

저장하지 않는 내용은 다음과 같다.

- 원 대화, 답변, 작업 기록과 숨은 추론
- 프로젝트 사실, 원자료와 Corpus의 출처 연결 맥락
- Sense가 관리하는 사용자 통제형 지침
- 자격 증명, 직접 식별자, 민감한 개인 특성과 포괄적 성격 평가

## Plugin과 개발

이 plugin 폴더가 실행 코드, 하나의 스킬, asset, launcher와 manifest를 함께 보관하는 정본이다.
marketplace는 이 폴더를 직접 설치하므로 별도 생성 사본, package builder와 생성 version 파일을
두지 않는다.

테스트는 원자적 rewrite, 참조 무결성, 제한된 읽기와 비공개 저장 경계에 필요한 소수만 유지한다.
모델 응답 평가, scenario matrix와 golden 결과는 운영 코드의 계약으로 만들지 않는다.
