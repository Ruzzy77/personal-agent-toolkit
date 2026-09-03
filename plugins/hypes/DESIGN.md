# Hypes 설계

Hypes는 에이전트가 현재 사용자를 이해하기 위해 만든 수정 가능한 관계를 Personal Agent Context
service의 소유자별 D1 저장층에 보관한다. Codex, Claude와 ChatGPT는 같은 그래프를 읽고 고친다.
임베딩 모델, 별도 추론 서비스나 세션 상태는 사용하지 않는다. 로컬 SQLite 구현은 개발과 최초
이관 자료를 위한 별도 경계다.

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

## 의미 규칙

선호, 이해, 불확실성, 적용과 채택은 같은 관계 모델에서 에이전트가 필요한 만큼 구분한다. 제품은
인식론적 상태의 고정 열거형이나 등급 체계를 강제하지 않는다. Node의 label·attribute와 Edge의
predicate·qualifier로 현재 해석을 표현하고, 상호작용에서 이해가 달라지면 기존 관계를 교체하거나
삭제한다.

인식론적 조정은 감사 기록이 아니다. 프로젝트 파일과 공동 산출물은 사용자 지식 수준 추정에서
제외하며, 출처 연결·별도 자료 이력·독립된 출처 저장소를 요구하지 않는다. 원 대화와 자료는 기존
저장 경계에 남긴다.

## 쓰기

공개 입력 스키마에는 연산과 그 값의 중첩 필드를 직접 포함한다. 각 `oneOf` 분기는 `op`의
고정값과 기존 입력 제약을 유지하며 `$defs` 해석을 요구하지 않는다. 실행 시 연산 구분과 값
검증은 기존 모델이 맡는다.

`hypes_rewrite`는 `put_node`, `put_predicate`, `put_edge`, `delete`만 받는다. 새 객체는
`$concept` 같은 patch 내부 참조를 사용할 수 있고, 기존 영속 참조에 put하면 ID를 유지한 채 값
전체를 교체한다.

입력과 참조를 검사한 뒤 Node·Predicate를 저장하고 Edge를 삭제·교체한 다음 개체 삭제를 적용한다.
Node나 Predicate 삭제는 외래 키 cascade로 남아 있는 연결 Edge도 함께 삭제한다. 삭제된 Edge
참조와 수는 결과에 포함한다. 같은 patch에서 삭제할 개체를 Edge가 가리키면 전체 patch를 거부한다.

## 읽기

`hypes_read`는 `focus` 또는 `seed_refs`에서 시작하고 최대 두 단계까지 그래프를 확장한다. 둘 다
없으면 이름순 목차를 반환한다. 반환되는 모든 Edge의 source Node, target Node와 Predicate를 같은
결과에 포함하며, 전체 객체 수는 `limit`을 넘지 않는다.

원격 서비스는 모든 읽기와 쓰기를 인증된 `owner_id`로 제한하고, 쓰기는 D1 batch 안에서
원자적으로 적용한다. 로컬 구현은 시작 시 저장 경계와 스키마를 검사한다. 초기화 이후 로컬
`hypes_read`는 SQLite `mode=ro`와 `query_only`를 사용하는 별도 연결을 열며, 스키마 생성·WAL
전환·쓰기 잠금을 수행하지 않는다. 로컬 쓰기만 `BEGIN IMMEDIATE` 트랜잭션을 사용한다.

## MCP 경계

공개 도구는 `hypes_read`와 `hypes_rewrite` 두 개다. 이후에도 유용할 관계가 실제로 생기거나 기존
이해가 달라지면 에이전트가 별도의 저장 요청·미리보기·확인 없이 rewrite할 수 있다. 기존 관계를
적용했거나 작업을 완료했다는 사실만으로는 쓰지 않는다.

현재 MCP SDK의 공개 입력 스키마와 런타임 오류 처리는 제한된 adapter 하나에서 연결한다.

## 저장 경계

운영 정본은 Personal Agent Context service의 소유자별 D1 테이블과 FTS 투영이다. 로컬
개발·이관 구현의 기본 데이터 디렉터리는 `~/Library/Application Support/Hypes/`이고 파일 이름은
`hypes-ontology.sqlite3`다. 디렉터리는 `0700`, 데이터베이스와 SQLite 보조 파일은 `0600`으로
유지한다. 로컬 변경은 원격 정본으로 자동 전파되지 않는다. 어느 저장층에도 원 대화, 작업 기록,
프로젝트 자료, Sense 지침, Corpus 맥락, 자격 증명, 직접 식별자와 민감한 개인 특성을 저장하지
않는다.
