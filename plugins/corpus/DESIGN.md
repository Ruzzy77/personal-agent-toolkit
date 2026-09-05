# Corpus 설계

## 제품 목적과 경계

Corpus는 원자료를 매번 다시 찾거나 읽지 않아도 작업에 필요한 맥락과 추출 근거를 재사용할 수 있게 하는 내구성 있는 지식 저장층입니다.

1. 등록한 Source에서 본문과 구조를 추출해 지속 가능한 record로 보관합니다.
2. 원자료와 사용자 판단을 미리 정리한 Context를 여러 클라이언트가 함께 읽게 합니다.
3. 사용자가 연결한 Work 폴더의 파일을 읽고 충돌 없이 교체하거나 삭제합니다.

원자료는 새 내용을 받아들이는 입력이지만 Corpus record의 생존 조건은 아닙니다. 원자료가 이동·삭제되거나 일시적으로 연결되지 않아도 마지막 정상 record와 Context는 계속 사용할 수 있습니다. 다만 Corpus는 원본 파일 백업 제품이 아니므로 원본 바이트, 서식의 완전한 재현과 현재 원문 일치는 보장하지 않습니다. 문서 형식별 고급 편집과 에이전트의 최종 해석도 Corpus의 책임이 아닙니다.

## 런타임 구성

```text
AI clients ── owner-authenticated remote MCP ── durable Context / Source records
                         │
                         └── Sync job broker
                                  │ outbound connection
Local CLI / Finder ── Personal Agent Sync ── capture / Work authority
                                  │
                                  └── Document Files analysis
```

원격 `CorpusService`가 Codex, Claude, 웹 ChatGPT와 claude.ai에 같은 Context, Source record와 확정
revision을 제공합니다. 로컬 Personal Agent Sync만 Finder 위치와 권한을 가지며, 원격에서 받은
작업도 현재 Connection 정책을 다시 확인한 뒤 수행합니다. [`engines/corpus`](../../engines/corpus/README.md)의
로컬 개발·이관 구현은 같은 데이터 계약을 사용하지만 클라이언트별 Context 복사본이나 별도 색인
세대를 만들지 않습니다.

### 원격 정본과 로컬 권한 저장소

원격 Context·Connection metadata는 소유자별 D1에, 각 Source의 내구성 있는 record와 검색 투영은
SQLite-backed Durable Object에 둡니다. 자세한 배치와 원자적 업로드 계약은
[`services/remote-context`](../../services/remote-context/DESIGN.md)가 맡습니다.

다음 파일은 Finder 권한, 로컬 실행과 최초 이관을 위한 비공개 로컬 저장소입니다.

- `catalog.sqlite`: Source의 논리 ID, 현재 위치 힌트와 파일시스템 정체성
- `corpora/<id>/corpus.sqlite`: 문서·revision·extraction projection·Source unit과 제한된 변경 대기열
- `contexts.sqlite3`: Context, item과 출처 연결
- `workspaces.sqlite3`: Work Connection, Current File과 recovery 기록
- `workspace-runtime/<id>`: 교체용 staging과 recovery copy

Space는 별도 데이터베이스나 고정된 색인 세대로 저장하지 않습니다. Context, Source 등록과 Work Connection의 현재 정본을 읽어 동적으로 만듭니다.

## 정체성과 경로 이동

Corpus의 논리 정체성과 물리 위치는 분리합니다.

- Source 등록은 불변 `location_id`와 현재 `source_root`를 함께 가집니다.
- 문서는 경로 해시가 아닌 불변 UUID `document_id`를 가집니다.
- revision은 캡처한 내용과 당시 관측값을 식별합니다.
- extraction projection은 추출기와 설정을 식별하지만 그 식별자를 자동 재분석 조건으로 사용하지 않습니다.
- 검색 FTS는 위 record에서 다시 만들 수 있는 파생 투영입니다.

`source_root`와 문서의 절대 경로는 현재 파일에 접근하기 위한 운영 정보일 뿐, 지식 record의 ID나 출처 anchor가 아닙니다. 같은 볼륨에서 Finder로 Source 또는 Work 폴더를 옮기면 저장된 device·inode를 macOS 파일시스템에 질의해 현재 경로를 찾고 등록값을 원자적으로 고칩니다. 복사·복원이나 새 checkout으로 정체성이 바뀌면 Sync의 명시적 rebind가 Sync 상태와 로컬 Corpus의 Source·Work 등록을 함께 갱신합니다.

경로 비교와 외부 표시는 NFC로 정규화합니다. 실제 디스크 이름은 바꾸지 않으며, 같은 위치에서 NFC로 같아지는 서로 다른 이름이 관측되면 임의로 합치지 않고 충돌로 중단합니다. Codex·Claude provider record에는 세션 식별에 필요하지 않은 `cwd`와 `workspace` 절대 경로를 저장하지 않습니다. 세션 Source의 범위 선택자는 현재 경로와 디렉터리 정체성을 분리해 저장하므로, 선택한 작업 폴더가 같은 볼륨에서 이동해도 다음 갱신에서 현재 경로를 복구합니다.

## Source record와 검색

`scan`은 파일을 열지 않고 메타데이터와 residency를 기록합니다. `ingest`는 제한된 임시 capture로 선택한 파일을 읽고 형식별 adapter가 Source unit을 만듭니다. 성공한 unit은 revision과 active extraction projection에 연결되어 내구성 있는 record가 됩니다.

새 원자료 추출이 실패하면 실패 시도와 문제만 기록하고 기존의 마지막 정상 projection을 유지합니다. 파일이 사라지거나 내용이 달라져도 해당 record를 검색 결과에서 즉시 버리지 않습니다. 결과에는 다음 상태와 시간이 함께 나갑니다.

- `source_state`: `unknown`, `available`, `changed`, `partially_available`, `unavailable`. Connection 요약에서는 현재 Source 목록을 기준으로 하며, 삭제 후 보존 중인 record는 현재 원자료의 가용성을 낮추지 않습니다.
- `record_state`: `empty`, `ready`, `partial`, `extractor_outdated`, `archived`, `unavailable`. 삭제된 항목도 정상 projection이 보존되어 있으면 record 요약에 포함하지만, projection 없이 삭제된 과거 항목은 현재 record를 `partial`로 만들지 않습니다.
- `captured_at`: record가 원자료에서 캡처된 시점

검색은 입력 문구와 같은 FTS 후보를 먼저 반환하고, 결과가 없으면 모든 검색어가 들어 있는 후보를 한 번 더 찾습니다. 순위는 후보 정렬에만 쓰며 내용은 선택한 unit에서 읽습니다. 원자료가 없어도 active record는 검색·조회할 수 있습니다. 최신 정보나 현재 원문 인용에는 `source_state`, `captured_at`과 필요 시 갱신 결과를 함께 확인합니다.

형식별 본문·구조·그림 관측과 문서 내부 위치는 Document Files가 생성합니다. Corpus는 unit type, geometry, confidence와 품질 표지를 검증한 뒤 revision과 projection에 연결합니다. 빈 구조 unit은 읽을 수 있지만 텍스트 검색에서는 제외합니다. 구조 맥락 조회는 선택한 unit과 같은 projection 안에서만 확장합니다. 추출된 원문과 에이전트의 의미 해석을 구분하고 원문에 없는 제목·번호나 배치를 Source unit에 덧붙이지 않습니다.

Corpus는 `document-files process --describe`에서 adapter identity와 capability를 읽습니다. identity는 새 projection의 재현 근거와 중복 판정에 사용하며, 기존 projection을 오래된 결과로 분류하는 조건은 아닙니다. `extractor_outdated`는 현재 실행기가 해당 형식을 더 이상 지원하지 않는 경우를 위한 호환 상태로만 남깁니다. 내용이 같은 기존 문서의 자동 재분석은 Sync가 형식별 `reanalysis_generation`의 명시적 증가를 확인한 경우에만 수행합니다. 입력은 private staging 파일의 읽기 전용 descriptor로 전달하며 원본 경로, Source ID와 권한 정보는 전달하지 않습니다. 처리 후 원본 바이트 사본은 지우고 구조화된 record를 남깁니다. Document Files를 사용할 수 없으면 자체 parser, OCR이나 renderer로 대체하지 않습니다.

## 자동 갱신과 변경 대기열

파일 감시 이벤트는 곧바로 장기 이력이 되지 않습니다. 같은 경로의 이벤트를 잠시 모아 하나로 합치고, private `source_change_queue`에 최대 2,048개만 둡니다. 한도를 넘거나 root 수준 변화가 생기면 전체 대조 항목 하나로 축약합니다. 성공한 전체 scan과 필요한 extraction이 끝난 뒤 대기열을 비웁니다. 실패 항목은 재시도를 위해 오류와 횟수만 갱신하며, 완료된 이벤트 기록을 누적하지 않습니다.

운영 구성에서는 Personal Agent Sync가 시작 시와 기본 15분 간격으로 전체 대조합니다. 따라서 파일
감시가 누락되거나 Source root 자체가 이동해도 다음 대조에서 복구합니다. Sync에 연결하지 않은
로컬 Corpus 단독 시험에서는 maintenance worker가 같은 안전망을 맡습니다. 어느 경우에도 로컬
worker lock을 얻은 한 프로세스만 갱신하며, 별도 전역 색인 세대나 클라이언트별 동기화 상태는
두지 않습니다.

원격 MCP는 정확한 `document_id`에 대해 `source.refresh` 작업을 보낼 수 있습니다. Sync 앱은
파일 내용이 이전 revision과 같더라도 Document Files를 다시 실행하므로 추출기 변경을 반영할 수
있습니다. 새 projection을 먼저 확정하고 활성 포인터를 전환한 뒤, Context가 보호하지 않는 이전
projection만 정리합니다. 이 정리는 명시적 재분석과 실제 파일 내용 변경에 함께 적용하므로,
참조되지 않는 과거 추출 결과가 파일 저장 횟수만큼 누적되지 않습니다. 분석이 오래 걸리면 원격
작업 상태로 완료 여부를 확인합니다.

로컬 단독 시험의 worker 상태는 `maintenance-state.json` 단일 스냅샷으로 원자적으로 교체합니다.
Sync의 운영 상태도 회전하는 현재 상태와 제한된 대기열만 유지합니다. 변경 이벤트와 보존 전환의
상세 목록을 장기 실행 기록으로 남기지 않습니다.

## Context

Context는 Source 원문을 매번 다시 읽지 않아도 쓸 수 있는, 미리 분석·정리된 재사용 맥락입니다. 제목, 목적, 범위, 연결 Source와 질문·관계·판단·gap item을 저장합니다. Context item 본문은 durable representation이고 Source unit 또는 provider record 연결은 근거와 갱신 판단을 위한 provenance입니다.

파일 Source의 추출 record는 원자료 없이도 읽을 수 있습니다. Codex·Claude 세션 같은 provider 자료는 원문 전체를 중복 보관하지 않고, 선택해 저장한 Context item을 내구성 있는 지식으로 삼습니다. provider 원문을 정확히 다시 읽는 기능은 제공자 자료가 남아 있을 때만 가능하지만, 그 부재가 Context item을 없애지는 않습니다.

Context Skill은 사용자가 승인한 Context별 작업 지침이며 Source 자료와 구분합니다. 선택한 Space와 함께 private Corpus 저장소에서 읽고 plugin 배포본에 복사하지 않습니다. 사용자가 명시적으로 요청한 기존 Context 항목의 종류·본문·상태는 현재 Context version과 대조해 한 transaction으로 수정하며, 다른 속성과 출처 연결은 보존합니다. 승인 Context Skill은 해당 Skill의 현재 version과 전체 교체값을 대조해 바꿉니다.

원격 Context의 생성·보관, 항목 생성·삭제, 상태 이외의 속성 및 출처 연결 수정은 현재 공개 MCP 범위 밖입니다. 로컬 Context 명령은 개발·이관 저장소만 바꾸며 원격 정본에 반영되지 않습니다. 전체 metadata import는 개별 Context 수정 수단으로 사용하지 않습니다.

Context가 현재 참조하는 document record는 자동 정리에서 보호합니다. 원자료 변경이나 Source 갱신으로 오래된 출처를 새 원문에 자동 연결하지 않습니다.

### Context 출처 읽기

2026-09-05 격리 조사에서 `corpus_context_sources`에 정확한 document·revision·projection·unit
연결이 있어도 `corpus_space_get`이 이를 반환하지 않는 것을 재현했습니다. 본문만으로 재검색하면
Context가 참조한 보존본 대신 현재 검색 결과를 읽게 될 수 있습니다. 기존 Cloudflare 환경과
fixture로 확인했으며 운영 Context나 Source를 수정하지 않았습니다.

기존 `read_ref`는 보존된 unit을 정확히 읽습니다. 새 revision이나 같은 revision의 새 projection을
커밋해도 과거 참조가 현재본으로 바뀌지 않았습니다. 과거 revision은 `stale_source_revision`이지만,
현재 revision의 비활성 projection은 `current_source`로 반환됩니다. 이 상태는 최신 추출본이라는
뜻이 아닙니다. `captured_at`도 읽은 revision의 저장 값이며 같은 revision 재수집에서 갱신될 수
있으므로 판단 당시의 불변 시각이나 추출 시각으로 해석하지 않습니다.

이 변경은 저장층이나 검색 방식을 바꾸지 않고 저장된 출처를 선택적으로 읽는 데 한정합니다.
아래 계약의 구현·검사를 마치고 Corpus 0.23.0의 원격 서비스에 반영했습니다.

1. **선택 조회:** `corpus_space_get`에 `include_sources`(기본 false), `source_limit`(기본 20,
   최대 100), `source_offset`(기본 0, 최대 200,000)을 추가합니다. 생략하면 기존 Context 응답을
   유지합니다. 출처를 요청한 경우 현재 Context 페이지의 각 item에 `sources`를 추가하고,
   `links`, `offset`, `limit`, `returned_count`, `has_more`, `next_offset`을 반환합니다. 출처는
   `source_ref_id` 순서로 읽으며 item 하나의 다음 출처는 `context_limit=1`과 해당 Context
   offset으로 좁혀 이어 읽습니다. 전체 출처나 Source 본문을 기본 응답에 붙이지 않습니다.
2. **정확한 연결:** 허용된 연결의 출처에는 저장된 document·revision·projection·unit 식별자와
   `link_role`, 선택한 `connection_id`와 기존 형식의 `read_ref`를 제공합니다. 같은 Space에서
   원격 읽기가 허용된
   indexed Source Connection과 같은 corpus가 연결되고 출처 행에 unit 식별자가 있을 때만 읽기
   참조를 만듭니다. 이 목록 응답은 DO 안의 실제 unit 존재 여부를 대조하지 않습니다.
   복수의 동등한 연결은 기존 `connection_id` 정렬의 첫 연결을 명시하며 읽기 실패 시 다른 연결로
   조용히 바꾸지 않습니다. 저장된 출처 표시는 실제 unit 재읽기나 정체성 대조를 대신하지 않습니다.
3. **권한과 부재:** 허용된 연결이나 unit 식별자가 없는 출처, provider-only 출처를 현재 파일이나 다른
   제공자의 원문으로 추정 연결하지 않습니다. 허용된 Connection이 없는 행과 provider-only 행은
   `source_ref_id`·`link_role`·`read_ref:null`·부재 사유만 반환하며 출처 개수·페이지에는 포함합니다.
   권한은 있으나 unit 식별자가 없는 파일 출처는 다른 식별자를 유지하고 읽기 참조만 null로 둡니다.
   식별자는 남았지만 실제 unit이 없으면 참조가 반환될 수 있으며 실제 Source 읽기가 누락을 드러냅니다. 임의 JSON인
   `source_span`과 내부 corpus·snapshot 식별자는 이번 공개 응답에 추가하지 않으며 저장값은
   보존합니다. 정확한 구조는 unit을 실제로 읽어 확인합니다. 읽을 때의 소유자·Space·
   Connection·scope 검사와 원자료 오프라인 상태에서도 보존 record를 읽는 계약은 유지합니다.
4. **시점과 응답 한계:** Source 읽기의 본문형 `source`와 상세 unit에 저장된 projection의
   활성 여부를 `projection_state=active_for_revision|superseded`로 별도 표시합니다. 활성 여부는
   해당 revision 안의 상태이며 현재 document revision 여부와 구분합니다. Context 출처에는
   Connection의 현재 시각·상태를 복사하지 않습니다. 출처를 포함한 Context 응답은 2 MiB를
   넘기지 않으며 초과 시 명시적으로 페이지 축소를 안내합니다. 출처를 조용히 잘라내지 않습니다.

구현은 `services/remote-context/src/corpus.ts`, `corpus-shard.ts`, `schemas.ts`, 공유 도구 설명과
기존 `investigate-corpus` Skill에 한정합니다. source link 수정·생성, Context 자동 갱신, provider 원문
복제와 보존 정책 변경은 포함하지 않습니다. 기존 검사와 일회성 확인으로 출처 페이지의 누락·중복,
전체 응답 경계, 과거 revision·projection의 정확한 재읽기, 허용 철회 뒤 거부와 오프라인 읽기를
확인했습니다. 기존 typecheck·테스트 20개가 통과했고, 100개 item과 10,000개 출처 조건에서
페이지 상한·예산 초과 거부도 확인했습니다. 출처 쿼리는 D1 제한에 맞춰 5개 item·20개 바인딩·
최대 505행씩 처리하며 새 영구 테스트는 남기지 않았습니다.

원격 반영 뒤 Codex와 Claude Code의 새 비지속 세션에서 기존 Context의 첫 출처를 실제로
읽었습니다. 저장된 네 식별자가 반환된 Source·span과 일치했고, 현재 revision의 비활성
projection을 새 추출본으로 바꾸지 않고 읽었습니다. 출처 한 문단이 Context 설명의 일부만
뒷받침한다는 점도 구분했습니다. 운영 Context·Source는 변경하지 않았습니다. 개인 ChatGPT는
처음에 attributes의 경로를 현재 Work 읽기로 대체했으며, 정확한 입력을 지정한 뒤 출처 연결의
읽기에 성공했습니다. 자동으로 적절한 경로를 선택하는지는 이 두 번째 성공과 구분합니다.

공유 Worker와 OpenAI 등록 app의 입력·설명, Codex 통합 plugin과 Claude Code의 Corpus를
갱신했습니다. Claude Desktop은 기존 설치의 업데이트 확인·반영으로 0.23.0을 적용하고 재시작했으며,
현재 Skill의 출처·추출본 안내와 웹의 같은 버전을 확인했습니다. 기존 Cowork 작업에서도 새 Skill을
실제로 읽었지만 도구 검색은 이전 입력 세 개를 유지했습니다. 기존 커넥터의 도구 목록 갱신 뒤에도
같은 작업에서는 새 입력이 보이지 않아 Source 호출 전에 중단했습니다. 새 Cowork 세션의 입력·
실제 읽기 확인은 남아 있으며, 이 결과를 원격 서버의 구형 배포나 인증 장애로 단정하지 않습니다.
새 Skill은 구형 서버가 받지 않는 출처 입력을 사용할 수 있으므로 서버를 먼저 반영하고 관련 클라이언트를
갱신합니다. 되돌릴 때에는 새 입력을 보내는 Skill·등록 schema부터 이전 상태로 맞추며, 기존
압축 저장 형식을 읽는 Worker와 저장 binding을 유지합니다. 이 변경에는 migration이나
Source 재분석이 필요하지 않습니다.

## record 보존과 정리

검색 노출과 물리 보존은 분리합니다. document record에는 다음 보존 등급이 있습니다.

- `protected`: 자동 보관·삭제하지 않음
- `managed`: 일반적인 장기 재사용 자료
- `transient`: 짧게 쓰는 임시 자료

원자료에서 분리된 record만 고정된 기간과 마지막 사용자 조회 시점에 따라 `active → archived → trash → purge`로 이동합니다. 기본값은 managed가 분리 30일 후 보관, 보관 180일 후 휴지통, 휴지통 30일 후 완전 삭제이며 transient는 각각 7일·30일·7일입니다. Context가 참조하거나 `protected`로 지정한 record는 이 전환에서 제외하고, 잘못 보관된 보호 record는 active로 복원합니다.

이 정책은 파일 내용의 의미·중요도·품질을 모델이 판단하지 않습니다. 수동 복원과 보존 등급 변경을 제공하며, purge만 revision·projection·unit과 검색 투영을 실제로 삭제합니다.

### 복구 검토와 다음 확인

중단된 업로드의 staging·재개와 현재 원본의 정확한 `source.refresh`는 유지합니다. 재분석은 과거
projection의 식별자·본문 복원을 보장하지 않으며, Sync DB의 마지막 hash·식별자·완료 응답은 분석
본문의 백업이 아닙니다. `migrate-local`도 owner 전체 metadata와 Sense·Hypes를 함께 다루므로
개별 shard 복구에 재사용하지 않습니다.

2026-09-05 조사 시 [Cloudflare PITR](https://developers.cloudflare.com/durable-objects/api/sqlite-storage-api/#pitr-point-in-time-recovery-api)는
SQLite DO 하나의 SQL·KV 전체를 최근 30일 안의 bookmark로 복구할 수 있지만 로컬 실행에서는
지원되지 않습니다. 현행 CorpusShard에는 bookmark·복구 호출 경로가 없습니다. 일회성 확인은
기존 CorpusShard와 migration을 재사용한 **원격 합성 DO 한 개와 격리 D1**으로 제한했습니다.
운영 저장소·인증·Sync binding과 공개 HTTP·MCP 경로를 연결하지 않고, 대상이 고정된 일회성
비공개 호출부에서만 복구를 예약합니다. 원격 접근은 배포된 Worker를 향한
[service binding](https://developers.cloudflare.com/workers/local-development/#using-remote-resources-with-durable-objects-and-workflows)을
사용하며 상시 복구 서비스를 먼저 추가하지 않습니다.

1. 합성 Source A와 이를 가리키는 Context를 만든 뒤 쓰기를 멈추고 DO·D1 bookmark와 식별자·
   hash를 확보합니다. Source를 B로 변경한 다음 DO를 A로 복구하고 재시작합니다. 저장 행을 먼저
   대조한 뒤 기존 Context 출처 읽기로 문서·revision·projection·unit, 본문과 추출본 상태를 확인합니다.
2. D1이 B를 참조하는 조건에서 DO만 A로 복구하여 새로 생긴 출처 누락을 검출합니다. 참조를 지우거나
   최신본으로 바꾸지 않고, 별도로 보존한 undo bookmark로 B를 되살려 다시 대조합니다. 정상 복구·
   누락 검출·undo·읽기를 확인한 뒤 일회성 호출부와 모든 격리 자원을 정리합니다.
3. 운영 복구 절차에는 같은 owner·corpus를 사용하는 모든 Space의 Context·출처, 현재 Connection
   권한·generation, Sync 대기열·완료 응답의 대조를 포함합니다. 기존 누락과 복구로 새로 생긴 누락을
   구분하고, 대조 전에는 해당 corpus의 쓰기·유지보수를 재개하지 않습니다.

격리 환경에서 A 복구와 두 번의 undo 뒤 DO·D1의 저장 행 hash가 각 기준 상태와 일치했습니다.
복구한 Context의 저장 참조를 실제로 읽어 문서·revision·projection·unit 식별자, 본문 hash와
`captured_at`도 대조했습니다. B가 현재일 때 A의 과거 revision은 `active_for_revision`과
`stale_source_revision`을 함께 반환했습니다. 재시작 연결 중단은 별도 checkpoint로 확인했으며
복구 요청을 자동 재시도하지 않았습니다.

D1이 B를 참조하는 채 DO만 A로 복구하면 D1 FK는 정상이지만 정확히 B 출처 하나가 누락됐습니다.
Context에는 기존 식별자와 읽기 참조가 남았고 실제 Source 읽기는 `source=null`과 정확한
`missing_unit_ids`를 반환했습니다. 참조를 삭제·변경하지 않은 채 undo로 B를 복원한 뒤 현재 B와
과거 A를 모두 다시 읽었습니다. 임시 클래스의 삭제 migration 뒤 namespace 부재를 확인하고,
같은 임시 Worker와 새 D1도 삭제해 원격 자원이 남지 않음을 확인했습니다.

이 확인은 합성 SQL record의 복구이며 운영 자료·KV·provider 이력·Sync 재시도 복구를 입증하지
않습니다. 운영 복구에는 위의 교차 저장소·권한·대기열 대조가 별도로 필요합니다. D1 외래 키 검사는 DO의 unit 누락을
검출하지 못합니다. [D1 전체 복구](https://developers.cloudflare.com/d1/reference/time-travel/)는
같은 저장소의 다른 제품·Corpus와 현재 권한까지 되돌릴 수 있어 기본안에서 제외합니다. 같은 시각의
bookmark도 두 저장소의 공동 transaction을 보장하지 않습니다. 실제 장애에서 이후 변경을 포기해야
하거나 PITR 기간 밖의 이력·원문 바이트까지 보존하려면 사용자의 복구 범위 선택을 먼저 받습니다.

## Work 파일

Work Connection은 사용자가 명시적으로 연결한 폴더만 다룹니다. 경로는 root 기준 상대 경로로 정규화하며 symlink와 root 밖 이동을 허용하지 않습니다. 연결 이후에는 디렉터리 inode로 root 교체를 감지하고, 같은 볼륨에서 위치만 바뀐 경우 자동으로 새 경로를 등록합니다. 각 파일 작업에서는 현재 descriptor의 identity를 고정합니다.

### 읽기

- 실제 파일을 열어 내용 digest가 포함된 version token 계산
- UTF-8은 문자 위치로 페이지 읽기 지원
- 바이너리는 base64로 제한된 크기만 반환

### 쓰기

새 파일은 `expected_version=absent`를 요구합니다. 기존 파일은 편집 직전의 version token 또는 한 번씩만 나타나는 두 marker로 교체합니다. 파일 identity와 digest를 다시 확인하고 같은 디렉터리의 임시 파일로 원자 교환합니다. Work 경로마다 직전 파일 하나를 private recovery로 보존하며 소유자, 권한과 ACL을 보존하지 못하면 중단합니다.

삭제도 최신 version token과 사용자의 명시적 확인을 요구합니다. 복원은 recovery record와 현재 result version이 모두 일치할 때만 수행합니다. Source로도 등록된 Work 폴더의 변경은 제한된 변경 대기열에 넣고 정상 갱신 후 지웁니다.

## MCP 표면과 클라이언트 일관성

원격 MCP 서버는 Space·File 조회/편집, version 보호된 기존 Context 항목의 종류·본문·상태 수정과
승인 Context Skill 전체 교체, 정확한 Source 문서 갱신 요청과 Sync 작업 상태 확인을 제공합니다.
Source 등록·해제, 보존 정책과 Connection 변경은 로컬 구성이 맡습니다. Sync는 로컬 Finder 권한과
Connection 정책을 확인해 Source 갱신을 수행합니다. 이 갱신은 추출 record를 바꾸는 작업이며,
원자료나 Context 속성·출처 연결을 수정하지 않습니다.

Codex, Claude, 웹 ChatGPT와 claude.ai는 소유자 인증형 원격 MCP에서 같은 데이터와 도구 schema를
사용합니다. Finder 권한은 outbound-only Sync 앱에 남습니다. 이관에 사용한 private tunnel과
gateway는 교차 클라이언트 검증 뒤 제거됐으며 현재 실행 경계가 아닙니다.
중첩 입력은 각 필드가 도구 schema에 직접 나타나며 클라이언트의 `$ref` 해석에 의존하지 않습니다.

외부 응답에서는 로컬 절대 경로와 내부 registry ID를 제거합니다. Source와 Work 내용은 untrusted content로 반환합니다. 도구 오류도 data root와 등록 root를 노출하지 않도록 정리합니다.

## Source 읽기 응답

Toolkit 개선 계획 02에서 채택한 원격 Source 읽기 계약입니다. 기존 unit envelope를 축약하거나
도구를 새로 나누는 대신 `corpus_file_read`에 선택 입력 `source_view: "text" | "full"`을 둡니다.
이 절은 구현 계약을 설명하며, 배포 완료는 루트의 클라이언트 확인 기준을 따릅니다.

### 입력과 호환성

- 일반 읽기는 `source_view="text"`를 사용합니다. 입력을 생략하면 `full`로 처리하여 기존
  본문·`units`·해시·anchor와 UTF-16 페이지 의미를 유지합니다.
- `include_structure_context`는 실제 관계 확장 옵션이며 응답 형식 선택을 대신하지 않습니다.
  두 형식에서 같은 unit을 선택하고, `text`만 페이지에 해당하는 부분을 반환합니다.
- `source_view` 또는 `include_structure_context=true`에는 `read_ref`가 필요합니다.
  Work 요청에서는 입력 오류로 처리합니다. Work의 `encoding`, `max_bytes`와 파일 작업은 유지합니다.
- 공개 도구 이름, `read_ref`의 정체성, Source·Work 권한과 저장 데이터는 바꾸지 않습니다.
  로컬 개발·이관용 Corpus MCP에 별도 구현을 만들지 않습니다.

### 본문과 출처

`text` 결과는 `source_kind="indexed_source"`, `source_view="text"`로 구분합니다.

| 구성 | 내용 |
| --- | --- |
| `source` | Space·Connection, 문서·revision·projection 식별자, 상대 경로, `captured_at`, Source·의존 상태, 추출 완전성·보증 상태·coverage·projection 문제와 trust lineage |
| `untrusted_content` | 이번 페이지의 원문 한 벌. 다른 필드에 같은 본문을 반복하지 않음 |
| `spans` | unit 식별자·`read_ref`·ordinal·유형, 반환 본문의 `text_range`, 원래 unit 내부의 `unit_range`·`unit_chars`, 기존 `structure_path` 한 벌 |
| span 품질 정보 | 추출 방식·문제, OCR 여부, confidence와 quality flags |
| span 선택 근거 | `selection_reasons`: `seed`, `neighbor`, `structure_context` 가운데 해당하는 값 |
| 페이지 | `start_char`, `returned_chars`, `has_more`, `next_start_char`, `offset_unit` |
| 선택과 누락 | `selection`의 요청 unit·이웃·구조 옵션·전체 선택 수·관련 unit 수·추가 unit 수, `missing_unit_ids` |

`count`는 이번 페이지의 span 수이고 `selection.selected_unit_count`는 전체 선택 unit 수입니다.
범위의 `start`는 포함하고 `end`는 포함하지 않습니다. `text_range`는 반환 본문 안의 위치이며
`unit_range`는 원래 unit 안의 위치입니다. 좌표·표·병합 셀·중첩 컨테이너는 평탄화하지 않습니다.
완전한 unit의 해시를 잘린 본문에 붙이지 않으며, 전체 본문·anchor·해시·geometry는 `full`로 읽습니다.

`captured_at`은 읽은 unit의 revision에서 가져옵니다. 읽기 시각이나 Connection·최신 revision의
요약 시각으로 대체하지 않습니다. 부분 추출, 원자료 부재와 과거 revision의 상태를 그대로 표시하며,
읽기 성공을 현재 원문과의 일치로 해석하지 않습니다. 본문과 구조는 `untrusted_source_derived`입니다.

참조한 unit이 없으면 `source=null`, 빈 본문·span과 누락 식별자를 반환합니다. 정상적인 빈 구조
unit과 구분하며 비슷한 파일이나 새 revision으로 대체하지 않습니다.

### 페이지와 크기 제한

같은 projection의 선택 unit을 ordinal 순서로 놓고 `\n\n`으로 연결합니다. 구분자는 span에
포함하지 않습니다. 페이지 밖 unit의 본문과 구조는 보내지 않습니다. 빈 구조 unit은 해당 위치에
길이 0의 span을 두며 마지막 빈 unit은 마지막 페이지에 포함합니다. 본문이 없는 선택도 구조와
종료 상태를 반환합니다.

`text` 위치는 `offset_unit="unicode_code_point"`, `full` 위치는 `"utf16_code_unit"`입니다.
같은 참조·형식·이웃·구조 옵션을 유지하면서 반환된 `next_start_char`로 이어 읽습니다.
서로 다른 형식의 위치를 교환하지 않습니다. Work의 위치 의미는 바꾸지 않습니다.

본문 한 페이지는 기본 30,000자, 최소 1,000자, 최대 200,000자입니다. 선택은 최대 500 unit,
직렬화 결과 객체는 최대 2 MiB입니다. `text`의 전체 연결 범위는 구분자를 포함해 최대
2,097,152 code point로 제한하여 모든 페이지의 시작 위치가 입력 상한 안에 있도록 합니다.
한도를 넘으면 `budget_exceeded`로 중단하고 더 작은 이웃·구조 범위나 unit별 읽기를 안내합니다.
크기를 맞추려고 경고·출처·구조를 조용히 버리지 않습니다.

shard는 식별자·구조·길이를 먼저 고르고 페이지에 필요한 본문만 가져옵니다. 일반 본문은 SQLite의
code point 단위 부분 읽기를 사용합니다. NUL이 포함된 본문은 TEXT 함수의 조기 종료를 피하도록
64 KiB UTF-8 블록으로 읽으며 NUL·BOM과 블록에 걸친 다중 바이트 문자를 보존합니다.
큰 metadata는 읽기 전 크기를 검사하고, 구조 복원 뒤와 서비스의 `read_ref` 추가 뒤에도 결과 객체의
바이트 한도를 검사합니다. 긴 단일 unit은 전체 envelope를 전송하지 않고 페이지로 읽습니다.

`full`도 수량·바이트 한도를 적용합니다. 이는 과거의 무제한 성공 응답과 달라지는 보호 동작입니다.
MCP의 `structuredContent`와 JSON text fallback은 유지합니다. 2 MiB는 이중 표현 전 결과 객체의
한도이며 실제 전송 크기나 모델 입력 토큰 수가 아닙니다.

### 구조 맥락

`include_structure_context=true`는 같은 projection의 명시된 관계만 확장합니다. 표 셀에서는 같은
표의 해당 행·겹치는 병합 행, 선언된 제목 셀과 필요한 표 컨테이너·캡션을 읽습니다. 각주·개체는
명시된 참조와 소유 문단을 따릅니다. 첫 행을 제목으로 추정하거나 의미상 관련된 내용을 검색하지 않습니다.

section·section stream·Office part·page와 표·개체 식별자·상위 컨테이너를 비교하여 다른 자료가
섞이지 않게 합니다. 일반 JSON과 `compact-path-v1:`의 같은 논리 필드를 조회하고, 후보 구조를
비교한 뒤 중복을 제거합니다. 이웃 선택은 기존 ordinal 범위를 유지하며 구조 관계로 재해석하지 않습니다.

`selection.structure_context_related_unit_count`는 요청 unit 외에 관계가 확인된 수,
`structure_context_added_unit_count`는 요청·이웃 선택에 새로 더한 수입니다. 확장이 없어도 옵션은
검사한 것으로 해석하며, span의 선택 근거로 실제 확장된 위치를 구분합니다. `has_more=true`인
페이지를 완전한 표나 행으로 안내하지 않습니다.

### 구현과 회귀 확인

- `services/remote-context/src/corpus-shard.ts`: 구조 선택, 수량·크기 사전 검사, revision 출처와 본문 페이지.
- `services/remote-context/src/corpus-read.ts`: 관계 비교와 공통 응답 예산.
- `services/remote-context/src/corpus.ts`: Source·Work 분기와 Space·Connection·`read_ref` 결합.
- `schemas.ts`, `mcp.ts`와 `skills/investigate-corpus/SKILL.md`: 공개 입력과 읽기 안내.

기존 `services/remote-context/test/context.test.ts`의 표적 회귀 항목은 공개 읽기·MCP 이중 표현과
Work 입력 비변경, Unicode·빈 구조·긴 본문의 페이지 재결합, 행·제목 셀·소유 관계와 두 저장 표현의
동일 결과, 수량·크기·누락·과거 수집 시각·추출 경고를 확인합니다. 원본 문서와 개인 자료를 시험
자산으로 복사하지 않습니다. 기존 anchor·구조 압축과 Work 예산 시험은 유지합니다.

### 배포와 되돌리기

Corpus 제품 base version과 MCP surface version, Claude manifest와 `products.json`을 맞추고,
OpenAI 통합 plugin은 제품 정본에서 재생성해 packaging revision을 갱신합니다. Corpus 전용 Codex
manifest를 만들거나 공통 service package version을 Corpus version으로 통일하지 않습니다.
소스·원격 저장소 반영 후 Worker, 등록 app과 각 클라이언트를 루트 release 절차에 따라 갱신합니다.
변경하지 않은 문서 Skill 다섯 개를 별도로 재설계하거나 Source를 재분석하지 않습니다.

데이터 migration과 파서·재분석 세대·권한 변경은 없습니다. 배포 전 현재 압축 저장 표현을 읽는
직전 Worker와 plugin 묶음을 복구 대상으로 확인합니다. 되돌릴 때에는 새 입력을 보내도록 바꾼
Skill·도구 안내부터 복귀하고 Worker를 되돌린 뒤 클라이언트의 schema와 새 세션을 확인합니다.
구형 저장 reader로 돌아가거나 자료를 다시 이관하지 않습니다.
