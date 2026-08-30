# Corpus 설계

## 제품 경계

Corpus는 다음 세 가지를 맡습니다.

1. 등록한 Source의 현재 파일 목록과 추출 본문을 비공개로 색인합니다.
2. 반복해서 사용할 Context를 원문 위치와 연결합니다.
3. 사용자가 연결한 Work 폴더의 파일을 읽고 충돌 없이 교체하거나 삭제합니다.

원본 문서의 정본성, 문서 형식별 고급 편집과 에이전트의 최종 해석은 Corpus가 맡지 않습니다. 원본은 계속 등록 폴더에 있고, Corpus의 검색 결과와 Context는 답을 만들기 위한 보조 자료입니다.

## 런타임 구성

```text
CLI / MCP
    │
CorpusService
    ├── ContextService
    ├── WorkspaceService
    ├── SpaceService
    └── Source scan / extraction / search
```

`CorpusService`가 CLI와 MCP의 공통 진입점입니다. 각 서비스는 같은 private data root를 사용하고 Source 원본과 Work 파일은 등록 폴더에서 읽습니다.

### Private storage

- `catalog.sqlite`: Source 등록
- `corpora/<id>/corpus.sqlite`: 현재 파일 목록, revision, extraction projection과 Source unit
- `contexts.sqlite3`: Context, item과 출처 연결
- `workspaces.sqlite3`: Work Connection, Current File과 recovery 기록
- `workspace-runtime/<id>`: 교체용 staging과 recovery copy

Space는 별도 데이터베이스에 저장하지 않습니다. Context, Source 등록과 Work Connection을 읽어 동적으로 만듭니다.

## Space projection

활성 Context 하나가 같은 ID의 Space가 됩니다. Context에 연결되지 않은 Source도 독립 Space로 보일 수 있습니다. 같은 실제 root를 사용하는 Source와 Work 등록은 하나의 Connection으로 합칩니다.

Connection의 공개 속성은 다음과 같습니다.

- `roles`: `source`, `work`
- `access_scope`: `local_only`, `remote_allowed`
- `permission`: `read_only`, `read_write`
- `index_mode`: `indexed`, `not_indexed`
- `source_state`: `ready`, `needs_refresh`, `partial`, `unavailable`
- `connection_state`, `current_file`, `generation`

`external_mcp` 보기에서는 `remote_allowed` Connection만 남습니다. 로컬 root와 내부 registry ID는 공개 응답에 포함하지 않습니다. 별도 canonical Space registry나 migration receipt는 사용하지 않습니다.

## Source index

`scan`은 파일을 열지 않고 메타데이터와 residency를 기록합니다. `ingest`는 bounded capture로 선택한 파일을 읽고 형식별 adapter가 Source unit을 만듭니다. 각 unit은 revision과 extraction projection에 연결됩니다.

검색은 입력 문구와 같은 FTS 후보를 먼저 반환하고, 결과가 없으면 모든 검색어가
들어 있는 후보를 한 번 더 찾습니다. 순위는 후보 정렬에만 쓰며, 내용은 선택한 unit에서 읽습니다. Source가 바뀌면 revision identity와 현재 projection을 비교해 오래된 결과를
제외합니다. 상세 색인 진단은 Chat에 펼치지 않고 Connection의 `source_state`로 계산합니다.

Corpus는 현재 파일과 현재 활성 projection을 색인의 단일 기준으로 삼습니다. 불완전한 scan에서도 확인한 파일은 갱신하고 `partial`로 표시합니다. 처리할 수 없는 파일은 coverage gap으로 남기지만, 다른 파일의 갱신을 막지 않습니다. snapshot, event history와 모델이 만든 claim의 semantic cache는 유지하지 않습니다. 재사용할 해석은 사용자가 선택한 Context에만 둡니다.

HWP/HWPX의 표와 주석 관계는 기존 unit의 `structure_path`에 원문 위치로 기록합니다.
빈 구조 unit은 읽을 수 있지만 텍스트 검색에서는 제외합니다. 구조 맥락 조회는 선택한 unit과
같은 projection 안에서만 확장하며 기존 응답 제한을 적용합니다. 추출된 원문, 원문에 선언된
문단 속성과 에이전트의 의미 해석을 구분하고, 제목·머리글·번호를 추측해 원문에 덧붙이지 않습니다.

## Context

Context에는 제목, 목적, 범위, 연결 Source와 item이 들어갑니다. Item은 질문, 관계, 판단 또는 gap을 표현하며 Source unit이나 연결 provider record를 가리킬 수 있습니다.

Context는 Source 원문을 대체하지 않습니다. 변경 가능성이 있는 사실이나 원문 인용이 필요하면 연결된 현재 Source를 다시 읽습니다. Context Skill은 사용자가 승인한 Context별 작업 지침이며 Source 자료와 구분합니다. Context Skill은 private Corpus 저장소에서 선택한 Space와 함께 읽으며, plugin이나 marketplace 배포본에 복사하지 않습니다.

## Work 파일

Work Connection은 사용자가 명시적으로 연결한 폴더만 다룹니다. 경로는 root 기준 상대 경로로 정규화하며, symlink와 root 밖 이동을 허용하지 않습니다. 연결 이후에는 canonical root 경로와 디렉터리 inode로 root 교체를 감지합니다. 운영체제가 재마운트하면서 바꿀 수 있는 장치 번호는 Work Connection이나 Source revision의 영구 identity로 사용하지 않으며, 각 파일 작업에서는 현재 descriptor의 identity를 고정합니다.

### 읽기

- 실제 파일을 열어 내용 digest가 포함된 version token 계산
- UTF-8은 문자 위치로 페이지 읽기 지원
- 바이너리는 base64로 제한된 크기만 반환

### 쓰기

새 파일은 `expected_version=absent`를 요구합니다. 기존 파일은 두 방식 중 하나로 교체합니다.

- 전체 교체: 편집 직전의 version token 사용
- 구간 교체: 현재 파일에서 한 번씩만 나타나는 두 marker 사이를 교체

교체 직전 파일 identity와 digest를 대조합니다. 임시 파일을 같은 디렉터리에 기록한 뒤 원자적으로 교환하고, Work 경로마다 직전 파일 하나를 private recovery로 보존합니다. 소유자, 권한과 ACL을 보존하지 못하면 중단합니다.

삭제도 최신 version token을 다시 확인하며, 사용자가 명시적으로 확인한 경우에만 수행합니다. 복원은 교체 recovery record와 현재 result version이 모두 일치할 때만 수행합니다.

## MCP 표면

기본 MCP 서버에는 Space/File 도구 아홉 개와 Context Skill 교체 도구 하나가 있습니다. Context Skill은 사용자의 명시적 요청, 현재 `version`과 전체 교체안이 모두 있을 때에만 저장하며, 충돌하면 기존 Skill을 보존합니다. Source Connection은 계속 읽기 전용입니다. 등록·등록 해제, scan, ingest, Context item 변경과 Work Connection 변경은 로컬 CLI가 맡습니다. 등록 해제는 현재 경로의 명시적 확인을 요구하며 Context나 Work Connection이 남아 있으면 중단합니다. 다른 정본으로 옮겨진 보관 Context가 유일한 연결이면 Context ID와 version을 지정해 Corpus 내부 연결 기록까지 정리할 수 있으며, 이때 등록부와 Context 데이터베이스의 비공개 사본을 먼저 남깁니다. 비공개 색인과 원래 제공자의 세션 파일은 자동으로 지우지 않습니다. Source revision과 inventory가 변해도 Context item을 읽을 수 있습니다. 출처 연결은 생성 당시의 식별자를 남기지만 과거 추출본을 보존하지 않습니다. 현재 자료가 필요하면 현재 Source를 다시 읽습니다. 이 분리는 Chat이 원본 범위나 로컬 연결을 임의로 넓히지 못하게 합니다.

stdio와 private tunnel은 같은 서버와 도구 schema를 사용합니다. 원격 배포만을 위한 별도 MCP 도구군, source 동기화 transaction이나 삭제 ticket 계층은 두지 않습니다. 도구 schema 확인을 위해 Work 폴더에 probe 파일을 만들지 않습니다.

응답은 로컬 절대 경로를 제거합니다. Source와 Work 내용은 untrusted content로 반환합니다. 도구 오류도 data root와 등록 root를 노출하지 않도록 정리합니다.
