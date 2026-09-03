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
작업도 현재 Connection 정책을 다시 확인한 뒤 수행합니다. 로컬 개발·이관용 Corpus 구현은 같은
데이터 계약을 사용하지만 클라이언트별 Context 복사본이나 별도 색인 세대를 만들지 않습니다.

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

- `source_state`: `unknown`, `available`, `changed`, `partially_available`, `unavailable`
- `record_state`: `empty`, `ready`, `partial`, `extractor_outdated`, `archived`, `unavailable`
- `captured_at`: record가 원자료에서 캡처된 시점

검색은 입력 문구와 같은 FTS 후보를 먼저 반환하고, 결과가 없으면 모든 검색어가 들어 있는 후보를 한 번 더 찾습니다. 순위는 후보 정렬에만 쓰며 내용은 선택한 unit에서 읽습니다. 원자료가 없어도 active record는 검색·조회할 수 있습니다. 최신 정보나 현재 원문 인용에는 `source_state`, `captured_at`과 필요 시 갱신 결과를 함께 확인합니다.

형식별 본문·구조·그림 관측과 문서 내부 위치는 Document Files가 생성합니다. Corpus는 unit type, geometry, confidence, OCR 여부와 품질 표지를 검증한 뒤 revision과 projection에 연결합니다. 빈 구조 unit은 읽을 수 있지만 텍스트 검색에서는 제외합니다. 구조 맥락 조회는 선택한 unit과 같은 projection 안에서만 확장합니다. 추출된 원문과 에이전트의 의미 해석을 구분하고 원문에 없는 제목·번호나 배치를 Source unit에 덧붙이지 않습니다.

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

Context Skill은 사용자가 승인한 Context별 작업 지침이며 Source 자료와 구분합니다. 선택한 Space와 함께 private Corpus 저장소에서 읽고 plugin 배포본에 복사하지 않습니다. 기존 Context 항목이나 Skill을 Chat에서 바꿀 때는 현재 Context version과 완전한 교체값을 요구하며 한 transaction으로 적용합니다.

Context가 현재 참조하는 document record는 자동 정리에서 보호합니다. 원자료가 바뀌어도 오래된 출처를 새 원문으로 자동 연결하지 않으며, Context 출처를 고치려면 현재 자료 검토와 version 확인이 필요합니다.

## record 보존과 정리

검색 노출과 물리 보존은 분리합니다. document record에는 다음 보존 등급이 있습니다.

- `protected`: 자동 보관·삭제하지 않음
- `managed`: 일반적인 장기 재사용 자료
- `transient`: 짧게 쓰는 임시 자료

원자료에서 분리된 record만 고정된 기간과 마지막 사용자 조회 시점에 따라 `active → archived → trash → purge`로 이동합니다. 기본값은 managed가 분리 30일 후 보관, 보관 180일 후 휴지통, 휴지통 30일 후 완전 삭제이며 transient는 각각 7일·30일·7일입니다. Context가 참조하거나 `protected`로 지정한 record는 이 전환에서 제외하고, 잘못 보관된 보호 record는 active로 복원합니다.

이 정책은 파일 내용의 의미·중요도·품질을 모델이 판단하지 않습니다. 수동 복원과 보존 등급 변경을 제공하며, purge만 revision·projection·unit과 검색 투영을 실제로 삭제합니다.

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

원격 MCP 서버는 Space·File 조회/편집, version 보호된 Context 항목·Context Skill 교체,
정확한 Source 문서 갱신 요청과 Sync 작업 상태 확인을 제공합니다. Source 등록·해제, 보존 정책과
Connection 변경은 로컬 구성이 맡습니다. Source Connection은 원자료를 수정하지 않으며,
갱신 요청도 새 추출 record를 만드는 작업으로만 해석합니다.

Codex, Claude, 웹 ChatGPT와 claude.ai는 소유자 인증형 원격 MCP에서 같은 데이터와 도구 schema를
사용합니다. Finder 권한은 outbound-only Sync 앱에 남습니다. 이관에 사용한 private tunnel과
gateway는 교차 클라이언트 검증 뒤 제거됐으며 현재 실행 경계가 아닙니다.
중첩 입력은 각 필드가 도구 schema에 직접 나타나며 클라이언트의 `$ref` 해석에 의존하지 않습니다.

외부 응답에서는 로컬 절대 경로와 내부 registry ID를 제거합니다. Source와 Work 내용은 untrusted content로 반환합니다. 도구 오류도 data root와 등록 root를 노출하지 않도록 정리합니다.
