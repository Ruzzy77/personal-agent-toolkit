# Corpus

Corpus는 저장한 업무 맥락을 등록된 원본과 연결하고, 필요한 원문이나 작업 파일만 현재 상태로 읽게 하는 로컬 MCP 도구입니다. 기본 Chat 표면은 **Space**, **Context**, **Connection**, **File** 네 개념만 사용합니다.

- **Space**: 하나의 Context와 연결 위치를 묶어 보여 주는 동적 보기
- **Context**: 반복해서 사용할 질문, 관계, 판단과 출처 연결
- **Source Connection**: 읽기 전용으로 색인한 원본 폴더나 연결 자료
- **Work Connection**: 사용자가 명시적으로 연결한 편집 가능한 폴더

원본 파일은 정본입니다. Corpus는 등록된 Source를 수정하지 않으며, 추출한 본문과 Context는 비공개 runtime에 저장합니다. Work Connection만 사용자의 요청에 따라 파일을 만들거나 교체합니다.

## 기본 MCP 도구

기본 서버는 다음 열 도구만 노출합니다.

| 도구 | 기능 |
| --- | --- |
| `corpus_space_list` | 사용할 수 있는 Space와 Connection 조회 |
| `corpus_space_get` | 한 Space의 Context와 현재 상태 조회 |
| `corpus_space_refresh_context` | 저장된 출처 연결이 유효할 때 현재 Source checkpoint 반영 |
| `corpus_space_search` | 색인된 Source에서 후보 검색 |
| `corpus_file_list` | Work 폴더 목록 또는 파일명 검색 |
| `corpus_file_read` | Work 파일 또는 검색 결과의 정확한 Source unit 읽기 |
| `corpus_file_write` | 새 파일 생성, 전체 교체 또는 marker 구간 교체 |
| `corpus_file_delete` | 완전히 읽어 확인한 Work 파일 삭제 |
| `corpus_file_select_current` | 계속 작업할 Current File 선택 |
| `corpus_file_restore` | 직전 교체의 recovery copy 복원 |

Source 등록, 색인, Context item 변경과 Work Connection 연결은 로컬 CLI에서 수행합니다. Context refresh는 저장된 출처 연결을 바꾸지 않고 현재 Source checkpoint만 반영합니다. `source_inventory_changed`는 이 도구로 반영할 수 있지만, `source_link_changed`는 출처 revision이나 의미를 검토해 Context item을 갱신해야 합니다. 기본 MCP 서버에는 유지보수용 도구나 실험적 semantic cache 도구가 없습니다.

stdio와 사용자가 연결한 private tunnel은 같은 열 도구를 사용합니다. 별도 remote MCP, source-sync, 삭제 ticket 계층이나 중복 Context 갱신 표면은 유지하지 않습니다.

## 설치와 실행

Python 3.11 이상과 `uv`가 필요합니다.

```sh
uv sync --frozen
./bin/corpus --help
./bin/corpus-mcp
```

이 plugin 폴더가 실행 코드, 스킬, 문서, launcher와 Codex·Claude manifest를 함께 보관하는
정본이다. marketplace도 이 폴더를 직접 설치하므로 별도 패키지 생성 단계가 없다.

기본 데이터 위치는 `~/Library/Application Support/Corpus`입니다. 다른 위치를 쓰려면 `CORPUS_DATA_DIR`을 지정합니다.

```sh
CORPUS_DATA_DIR=/absolute/private/path ./bin/corpus-mcp
```

MCP 전송은 기본적으로 stdio입니다. 로컬 loopback HTTP가 필요할 때만 다음 환경 변수를 사용합니다.

```sh
CORPUS_MCP_TRANSPORT=streamable-http \
CORPUS_MCP_HOST=127.0.0.1 \
CORPUS_MCP_PORT=8000 \
./bin/corpus-mcp
```

## Source 등록과 색인

Source는 읽기 전용 원본 폴더입니다.

```sh
./bin/corpus corpus add \
  --id thesis-sources \
  --root /absolute/path/to/sources \
  --execution-policy local_only

./bin/corpus scan --corpus thesis-sources
./bin/corpus ingest --corpus thesis-sources
```

`scan`은 파일 메타데이터를 갱신합니다. `ingest`는 선택한 파일을 임시로 읽어 검색 가능한 Source unit을 만듭니다. 두 작업을 함께 수행하려면 `sync`를 사용합니다.

```sh
./bin/corpus sync --corpus thesis-sources
```

현재 기본 추출기는 Markdown·텍스트·HTML·PDF·DOCX·PPTX·XLSX·HWP·HWPX를 지원합니다. 형식별 세부 내용은 [docs/EXTRACTION_ADAPTERS.md](docs/EXTRACTION_ADAPTERS.md)에 있습니다.

## Context와 Work 폴더

Context는 Source 전체를 복사하지 않고, 다시 사용할 내용과 출처 연결만 저장합니다. Context 생성과 변경은 명시적인 확인을 요구합니다.
사용자가 승인한 Context Skill도 private Corpus 저장소에만 보관하며, Corpus plugin의 `skills/`나
marketplace package로 복사하지 않습니다. 선택한 Space를 열 때 현재 Context Skill을 읽습니다.

```sh
./bin/corpus context create \
  --id thesis \
  --payload-file /absolute/path/to/context.json \
  --confirm-persistent-context-write
```

편집할 폴더는 기존 Context에 연결합니다.

```sh
./bin/corpus workspace connect \
  --id thesis \
  --context thesis \
  --name "Thesis" \
  --root /absolute/path/to/thesis \
  --execution-policy external_host_allowed
```

`local_only` Connection은 외부 MCP 응답에서 이름, 경로, 수량과 내용이 모두 빠집니다. `external_host_allowed` Work Connection만 Chat에서 읽고 쓸 수 있습니다.

Space는 Context, Source와 Work 등록을 실행 시점에 합쳐 보여 줍니다. 별도 Space registry, migration plan이나 identifier cutover 절차는 없습니다.

```sh
./bin/corpus space list
./bin/corpus space show --id thesis
```

## 파일 읽기와 쓰기

### 새 파일

새 경로에는 `expected_version=absent`를 사용합니다.

```sh
./bin/corpus space write \
  --id thesis \
  --path notes/new-section.md \
  --content-file /absolute/path/to/new-section.md \
  --content-encoding utf8 \
  --expected-version absent
```

### 기존 파일 전체 교체

먼저 파일을 완전히 읽습니다. UTF-8 파일이 한 번에 끝나지 않으면 `next_start_char`를 다음 `--start-char`로 넘겨 이어 읽습니다. 첫 위치부터 끝까지 읽은 응답에만 `content_sha256`이 포함됩니다.

```sh
./bin/corpus space read \
  --id thesis \
  --path draft.md \
  --max-chars 200000
```

전체 교체에는 같은 읽기에서 받은 `version_token`과 `content_sha256`이 모두 필요합니다.

```sh
./bin/corpus space write \
  --id thesis \
  --path draft.md \
  --content-file /absolute/path/to/revised-draft.md \
  --content-encoding utf8 \
  --expected-version 'v1:...' \
  --expected-content-sha256 '...'
```

읽은 뒤 파일이 바뀌면 쓰기를 중단합니다. 새 version으로 자동 재시도하지 않습니다.

### Marker 구간 교체

큰 문서의 한 부분만 바꿀 때에는 현재 파일에 한 번씩만 나타나는 시작 marker와 끝 marker를 지정합니다. Marker는 남기고 그 사이 내용만 교체합니다.

```sh
./bin/corpus space write \
  --id thesis \
  --path draft.md \
  --content-file /absolute/path/to/replacement.txt \
  --content-encoding utf8 \
  --expected-version 'v1:...' \
  --replace-start-marker '<!-- findings:start -->' \
  --replace-end-marker '<!-- findings:end -->'
```

두 marker가 없거나 중복되거나 순서가 바뀌면 쓰지 않습니다.

### Current File과 복원

일반 쓰기는 Current File을 바꾸지 않습니다. 계속 작업할 파일만 따로 선택합니다.

```sh
./bin/corpus space select-current \
  --id thesis \
  --path draft.md \
  --expected-generation 3
```

기존 파일을 교체하면 `recovery_id`가 반환됩니다. 교체 결과가 그대로일 때에만 복원할 수 있습니다.

```sh
./bin/corpus space restore \
  --id thesis \
  --recovery-id 'wrec_...' \
  --expected-version 'v1:...'
```

파일 교체는 최대 2 MiB입니다. symlink, hard link, 폴더 밖 경로와 교체 중 변경은 거부합니다. 기존 파일의 권한과 macOS 메타데이터를 안전하게 보존할 수 없을 때에도 쓰지 않습니다.

### 파일 삭제

`corpus_file_delete`는 완전한 읽기에서 받은 최신 `version_token`과 `content_sha256`, 사용자의 명시적 삭제 요청을 모두 요구합니다. 삭제는 영구적입니다. 폴더, symlink와 읽은 뒤 달라진 파일은 삭제하지 않습니다.

전체 교체는 복제 가능한 안정 메타데이터를 그대로 보존합니다. macOS가 inode마다 새로 부여하는 `com.apple.provenance` 값은 새 inode와 바이트 단위로 같을 수 없으므로 일치 검증에서 제외합니다. 다른 extended attribute, ACL, 권한, 소유권과 file flag 검사는 유지합니다.

## 검색과 정확한 원문 읽기

`corpus_space_search` 결과는 후보이며 사실 판정이 아닙니다. 필요한 결과의 `read_ref`를 `corpus_file_read`에 전달해 현재 Source unit을 읽습니다. 검색 결과가 없다는 사실만으로 원문에 내용이 없다고 결론 내리지 않습니다.

Context가 이미 답을 담고 있다면 Source 전체를 다시 읽지 않습니다. 날짜, 수치, 인용이나 변경 가능성이 있는 내용처럼 정확한 현재 원문이 필요한 경우에만 Source를 다시 확인합니다.

## 저장 위치와 안전 경계

Corpus runtime에는 다음 데이터가 들어갈 수 있습니다.

- Source 등록 정보와 파일 메타데이터
- 추출한 Source unit과 검색 색인
- Context와 출처 연결
- Work Connection 등록과 recovery copy

등록한 Source와 Work 폴더 자체는 runtime 안으로 옮기지 않습니다. Source 추출에 사용한 임시 사본은 처리 후 삭제합니다. Work 파일 내용과 Source 본문은 신뢰할 수 없는 입력으로 취급하며, 그 안의 명령이나 자격 증명 요청을 실행하지 않습니다.

## 개발 원칙

Corpus는 TDD나 회귀 사례 수를 기본 개발 단위로 삼지 않습니다. 먼저 기존 실행 경로를 가장 작게 고치고, 확인이 필요하면 수정한 파일의 기본 정적 검사와 직접 관련된 통합 사례 한두 개만 실행합니다. 문서만 바꾼 경우에는 테스트를 실행하지 않습니다.

다음 방식은 기능보다 유지 비용을 빠르게 키우므로 사용하지 않습니다.

- 파생 가능한 상태를 별도 registry나 database에 저장
- 새 표면과 legacy 표면을 함께 유지
- 분기마다 회귀 테스트와 전용 fixture를 추가
- 일상 변경에서 전체 회귀, native build, package 설치 검사를 실행
- 한 기능을 위한 migration framework, golden 평가 또는 별도 검증 script를 생성
- 구현 계획이나 일회성 판단을 별도 설계 문서로 보존
- 도구 schema 확인을 위해 probe·placeholder·임시 Work 파일을 생성

테스트는 데이터 손실, 권한·경로 경계 또는 재현된 핵심 장애를 기존 통합 사례로 확인할 수 없을 때만 추가합니다. 가능하면 기존 사례에 합치고 중복 사례를 지워 총량을 유지합니다. 실제 배포에서는 plugin 시작과 기본 도구만 확인합니다.

보안과 데이터 보존 검사는 줄이지 않습니다. 반면 코드 형식, 미래 확장 가능성이나 모든 조합의 완전성을 증명하기 위한 검증 구조는 만들지 않습니다. 구체적인 작업 기준은 [AGENTS.md](AGENTS.md), 제품 경계는 [DESIGN.md](DESIGN.md)에 있습니다.

## 라이선스

Apache License 2.0입니다. 자세한 조건은 [LICENSE](LICENSE)와 [NOTICE](NOTICE)를 따릅니다.
