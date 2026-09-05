# Hypes 설계

Hypes는 에이전트가 현재 사용자를 이해하기 위해 만든 수정 가능한 관계를 Personal Agent Context
service의 소유자별 D1 저장층에 보관한다. Codex, Claude와 ChatGPT는 같은 그래프를 읽고 고친다.
임베딩 모델, 별도 추론 서비스나 세션 상태는 사용하지 않는다. 로컬 SQLite 구현은 개발과 최초
이관 자료를 위한 [`engines/hypes`](../../engines/hypes/README.md)의 별도 경계다.

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
같은 patch에서 이미 삭제한 Edge를 중복 집계하지 않으며, 연결을 옮겨 살아남은 Edge는 삭제 결과에
포함하지 않는다.

## 읽기

`hypes_read`는 `focus` 또는 `seed_refs`에서 시작하고 최대 두 단계까지 그래프를 확장한다. 둘 다
없으면 이름순 목차를 반환한다. 반환되는 모든 Edge의 source Node, target Node와 Predicate를 같은
결과에 포함하며, 전체 객체 수는 `limit`을 넘지 않는다.

`focus`는 Unicode 문자·숫자로 나누고 중복을 제거한 뒤 최대 16개 검색어를 사용한다. 한글만 있는
질의와 한글·영문 혼합 질의를 같은 경로로 처리한다. 각 검색어를 인용한 FTS 접두 검색에서 AND를
먼저 적용하고 결과가 없으면 OR로 재시도한다. 별도 형태소 분석이나 의미 검색은 하지 않는다.

원격 서비스는 모든 읽기와 쓰기를 인증된 `owner_id`로 제한하고, 쓰기는 D1 batch 안에서
원자적으로 적용한다. 로컬 구현은 시작 시 저장 경계와 스키마를 검사한다. 초기화 이후 로컬
`hypes_read`는 SQLite `mode=ro`와 `query_only`를 사용하는 별도 연결을 열며, 스키마 생성·WAL
전환·쓰기 잠금을 수행하지 않는다. 로컬 쓰기만 `BEGIN IMMEDIATE` 트랜잭션을 사용한다.

## MCP 경계

공개 도구는 `hypes_read`와 `hypes_rewrite` 두 개다. 이후에도 유용할 관계가 실제로 생기거나 기존
이해가 달라지면 에이전트가 별도의 저장 요청·미리보기·확인 없이 rewrite할 수 있다. 기존 관계를
적용했거나 작업을 완료했다는 사실만으로는 쓰지 않는다.

현재 MCP SDK의 공개 입력 스키마와 런타임 오류 처리는 제한된 adapter 하나에서 연결한다.

## 수정 충돌 보호

여러 작업이 같은 관계를 읽고 고칠 때 나중 저장이 먼저 바뀐 값을 덮어쓰지 않도록 읽은 버전을
대조한다. 개체를 삭제할 때도 읽은 뒤 새로 연결된 Edge를 함께 지우지 않도록 같은 조건을 적용한다.
이 보호는 patch를 한 번에 저장하는 batch 원자성과 별개의 계약이다.

객체별 조건부 변경은 무관한 편집끼리 충돌하지 않는 장점이 있지만, Edge의 양 끝과 Predicate,
개체 삭제에 따라 사라질 연결까지 조건에 포함해야 한다. 현재 서비스는 관계 일부를 반환할 때도
소유자의 전체 그래프를 읽는다. 이 구조에서는 **소유자 그래프 단위의 낙관적 동시성 제어**가 입력과
저장 규칙이 더 단순하고 암묵적인 cascade 변경도 빠뜨리지 않는다. 무관한 동시 편집으로 재조회가
실제로 잦아질 때 객체 단위로 좁히는 방식을 다시 검토한다.

원격 읽기·수정 계약은 다음과 같다.

1. 읽기는 그래프, 검색 후보와 불투명한 `version`을 하나의 D1 읽기 batch에서 얻는다. 빈 그래프도
   쓰기 없이 버전을 반환한다. 반환량·목차·검색·확장 계약은 유지한다.
2. rewrite에는 `expected_version`을 필수로 받는다. 읽기 이후 어느 관계든 바뀌면 patch 전체를
   `graph_conflict`로 거부한다. 호출자는 필요한 관계를 다시 읽고 변경 내용을 재구성하며, 버전만
   바꾸어 같은 patch를 재전송하지 않는다. 새 관계만 만드는 patch에도 같은 조건을 적용한다.
3. 소유자별 버전 메타데이터와 변경 trigger를 둔다. 값의 저장·삭제·cascade와 최초 이관이
   모두 버전을 갱신한다. 기존 개인 관계와 FTS 본문은 이전하거나 다시 작성하지 않는다.
4. 버전 대조와 관계 저장은 같은 쓰기 batch에 둔다. 대조 이후의 경합도 전체 롤백으로 처리하고,
   성공 응답에는 그 저장이 끝난 시점의 버전을 반환한다. 이관의 전량 교체도 서버가 읽은 현재
   버전과 같은 transaction에서 대조하여 그 사이의 편집을 덮어쓰지 않는다.
5. 새 입력을 사용하지 않는 오래된 쓰기 요청은 허용하지 않는다. 기존 읽기는 계속 사용할 수 있으며,
   쓰기는 현재 입력과 Skill을 사용하는 클라이언트로 갱신해야 한다. 개발·최초 이관용 로컬 engine의
   함수 입력을 상시 원격 MCP의 수정 계약으로 대신하지 않는다.

### 저장과 배포

`hypes_graph_versions`는 소유자별 현재 버전 하나만 보관하며 관계의 사본이나 수정 이력을 만들지
않는다. 버전은 `hypes-graph-v1:` 접두사와 128-bit 임의 식별자로 반환한다. 읽은 적도 저장한 적도
없는 빈 그래프는 모두 0인 식별자를 반환하되 버전 행을 만들지 않는다. 기존 그래프에는 migration이
버전만 부여하며, 이후 관계 테이블의 trigger가 동일 값 저장을 포함한 각 저장 연산에서 갱신한다.

쓰기 batch는 필요하면 빈 버전 행을 만들고, `expected_version`과 일치하지 않을 때 NOT NULL
제약을 위반하도록 하는 조건부 갱신을 모든 관계 변경 앞에 둔다. 제약 실패는 `graph_conflict`이며,
관계·FTS·버전 메타데이터가 함께 롤백된다. 완료 버전은 같은 batch의 마지막 조회에서 얻는다.

Hypes 0.10.0 반영 묶음에서 `0002_hypes_graph_versions.sql`의 추가 migration을 먼저 적용한 뒤
원격 입력·Skill·plugin을 갱신한다. 기존 관계와 FTS를 export·복원 가능한 상태로 확인하고, migration
뒤에는 내용이 그대로인지 대조한다. 되돌릴 때 버전 검사를 우회하는 옛 writer를 복구 대상으로 삼지
않는다. 읽기 경로를 복구해야 한다면 버전 보호를 유지하거나 쓰기를 일시 중단하는 수정 배포를 한다.

## 저장 경계

운영 정본은 Personal Agent Context service의 소유자별 D1 테이블과 FTS 투영이다. 로컬
개발·이관 구현의 기본 데이터 디렉터리는 `~/Library/Application Support/Hypes/`이고 파일 이름은
`hypes-ontology.sqlite3`다. 디렉터리는 `0700`, 데이터베이스와 SQLite 보조 파일은 `0600`으로
유지한다. 로컬 변경은 원격 정본으로 자동 전파되지 않는다. 어느 저장층에도 원 대화, 작업 기록,
프로젝트 자료, Sense 지침, Corpus 맥락, 자격 증명, 직접 식별자와 민감한 개인 특성을 저장하지
않는다.
