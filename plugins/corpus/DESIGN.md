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

`CorpusService`가 CLI와 MCP의 공통 진입점입니다. 각 서비스는 같은 private data root를 사용하지만, Source 원본과 Work 파일은 등록된 실제 폴더에서 매번 확인합니다.

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

검색은 입력 문구와 정확히 일치하는 FTS 후보를 먼저 반환하고, 결과가 없으면 모든 검색어가
들어 있는 후보를 한 번 더 찾습니다. 순위는 증거가 아니며, 정확한 내용은 선택한 unit을 다시
읽어 확인합니다. Source가 바뀌면 revision identity와 현재 projection을 비교해 오래된 결과를
제외합니다. 상세 색인 진단은 Chat에 펼치지 않고 Connection의 `source_state`로 계산합니다.

Corpus는 현재 파일과 현재 활성 projection을 색인의 단일 기준으로 삼습니다. 불완전한 scan에서도 확인한 파일은 갱신하고 `partial`로 표시합니다. 처리할 수 없는 파일은 coverage gap으로 남기지만, 다른 파일의 갱신을 막지 않습니다. snapshot, event history와 모델이 만든 claim의 semantic cache는 유지하지 않습니다. 재사용할 해석은 사용자가 선택한 Context에만 둡니다.

## Context

Context에는 제목, 목적, 범위, 연결 Source와 item이 들어갑니다. Item은 질문, 관계, 판단 또는 gap을 표현하며 Source unit이나 연결 provider record를 가리킬 수 있습니다.

Context는 Source 원문을 대체하지 않습니다. 변경 가능성이 있는 사실이나 정확한 인용이 필요하면 연결된 현재 Source를 다시 읽습니다. Context Skill은 사용자가 승인한 Context별 작업 지침이며 Source evidence와 구분합니다. Context Skill은 private Corpus 저장소에서 선택한 Space와 함께 읽으며, plugin이나 marketplace 배포본에 복사하지 않습니다.

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

교체 직전 실제 파일 identity와 digest를 다시 확인합니다. 임시 파일을 같은 디렉터리에 기록한 뒤 원자적으로 교환하고, Work 경로마다 직전 파일 하나를 private recovery로 보존합니다. 소유자, 권한과 ACL을 안전하게 복제할 수 없으면 중단합니다.

삭제도 최신 version token을 다시 확인하며, 사용자가 명시적으로 확인한 경우에만 수행합니다. 복원은 교체 recovery record와 현재 result version이 모두 일치할 때만 수행합니다.

## MCP 표면

기본 MCP 서버에는 Space/File 도구 아홉 개만 있습니다. 등록, scan, ingest, Context item 변경과 Work Connection 변경은 로컬 CLI가 맡습니다. Source revision과 inventory가 변해도 Context item을 읽을 수 있습니다. 출처 연결은 생성 당시의 식별자를 남기지만 과거 추출본을 보존하지 않습니다. 현재 근거가 필요하면 현재 Source를 다시 읽습니다. 이 분리는 Chat이 원본 범위나 로컬 연결을 임의로 넓히지 못하게 합니다.

stdio와 private tunnel은 같은 서버와 도구 schema를 사용합니다. 원격 배포만을 위한 별도 MCP 도구군, source 동기화 transaction이나 삭제 ticket 계층은 두지 않습니다. 도구 schema 확인을 위해 Work 폴더에 probe 파일을 만들지 않습니다.

응답은 로컬 절대 경로를 제거합니다. Source와 Work 내용은 untrusted content로 반환합니다. 도구 오류도 data root와 등록 root를 노출하지 않도록 정리합니다.

## 복잡도가 커지는 경로

파생 상태를 저장하면 원본과의 동기화, migration, rollback과 별도 검증이 뒤따릅니다. 같은 기능을 여러 CLI·MCP 이름으로 제공하면 각 표면의 schema와 회귀 사례도 함께 늘어납니다. 모든 분기를 테스트로 고정하면 작은 구현 변경도 큰 fixture와 검증 helper를 유지해야 합니다.

Corpus는 다음 기준으로 이 증가를 막습니다.

- 현재 등록 정보에서 계산할 수 있는 보기는 실행 시점에 계산
- 하나의 기능에는 하나의 정본 서비스 경로와 공개 도구 표면 사용
- 기존 데이터를 실제로 변환해야 할 때에만 schema migration 작성
- 사용자 승인 없이 모델 해석, 평가 결과나 중간 queue를 영구 저장하지 않음
- 테스트는 데이터 손실·접근 경계와 재현된 핵심 장애에 한정
- 별도 설계 문서는 장기간 유지할 외부 형식이나 protocol에만 사용

일상 변경에서는 수정한 파일과 직접 관련된 확인만 합니다. 전체 회귀, native helper build와 package 설치 확인은 사용자가 요청하거나 release 후보를 만들 때만 수행합니다. 검사 수나 coverage를 기능 완성도의 지표로 사용하지 않습니다.
