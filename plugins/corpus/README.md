# Corpus

Corpus는 업무 맥락을 원본 Source와 연결하고, 필요한 원문과 Work 파일을 현재 상태로 읽는 로컬 MCP 도구입니다.

- **Space**: Context와 Connection을 합친 작업 보기
- **Context**: 다시 사용할 질문, 관계, 판단과 출처 연결
- **Source Connection**: 읽기 전용 원본
- **Work Connection**: 사용자가 연결한 편집 폴더

Source는 바꾸지 않습니다. Work Connection만 파일 생성·교체·삭제를 허용합니다.

## MCP 도구

| 도구 | 기능 |
|---|---|
| `corpus_space_list` | Space와 Connection 목록 |
| `corpus_space_get` | Space의 Context와 상태 |
| `corpus_space_search` | Source 검색 |
| `corpus_file_list` | Work 파일 목록·검색 |
| `corpus_file_read` | Work 파일 또는 Source unit 읽기 |
| `corpus_file_write` | 파일 생성·전체 교체·구간 교체 |
| `corpus_file_delete` | Work 파일 삭제 |
| `corpus_file_select_current` | Current File 선택 |
| `corpus_file_restore` | 직전 교체본 복원 |

Source 등록·색인, Context 수정과 Work Connection 연결은 로컬 CLI에서 수행합니다. stdio와 private tunnel은 같은 도구를 제공합니다.

## 설치

Python 3.11 이상과 `uv`가 필요합니다.

```sh
uv sync --frozen
./bin/corpus --help
./bin/corpus-mcp
```

기본 데이터 위치는 `~/Library/Application Support/Corpus`입니다.

```sh
CORPUS_DATA_DIR=/absolute/private/path ./bin/corpus-mcp
```

Loopback HTTP 전송은 다음 환경변수로 엽니다.

```sh
CORPUS_MCP_TRANSPORT=streamable-http \
CORPUS_MCP_HOST=127.0.0.1 \
CORPUS_MCP_PORT=8000 \
./bin/corpus-mcp
```

## Source

```sh
./bin/corpus corpus add \
  --id thesis-sources \
  --root /absolute/path/to/sources \
  --execution-policy local_only

./bin/corpus sync --corpus thesis-sources
```

`scan`은 파일 목록과 메타데이터를 갱신하고, `ingest`는 검색용 Source unit을 만듭니다. `sync`는 두 작업을 이어서 실행합니다. 지원 형식은 Markdown, text, HTML, PDF, DOCX, PPTX, XLSX, HWP와 HWPX입니다. 세부 내용은 [EXTRACTION_ADAPTERS.md](docs/EXTRACTION_ADAPTERS.md)에 있습니다.

## Context와 Work Connection

Context는 원문을 복사하지 않고 다시 쓸 내용과 출처 연결을 저장합니다.

```sh
./bin/corpus context create \
  --id thesis \
  --payload-file /absolute/path/to/context.json
```

편집 폴더는 Context에 연결합니다.

```sh
./bin/corpus workspace connect \
  --id thesis \
  --context thesis \
  --name "Thesis" \
  --root /absolute/path/to/thesis \
  --execution-policy external_host_allowed
```

`local_only` Connection은 외부 MCP에 노출되지 않습니다. `external_host_allowed` Work Connection은 Chat에서 읽고 쓸 수 있습니다.

```sh
./bin/corpus space list
./bin/corpus space show --id thesis
```

Context Skill은 private Corpus 저장소에 두며 선택한 Space와 함께 읽습니다.

## 파일 편집

새 파일에는 `expected_version=absent`를 사용합니다.

```sh
./bin/corpus space write \
  --id thesis \
  --path notes/new-section.md \
  --content-file /absolute/path/to/new-section.md \
  --content-encoding utf8 \
  --expected-version absent
```

기존 파일은 편집 직전에 읽고 받은 `version_token`으로 교체합니다.

```sh
./bin/corpus space read --id thesis --path draft.md --max-chars 200000
./bin/corpus space write \
  --id thesis \
  --path draft.md \
  --content-file /absolute/path/to/revised-draft.md \
  --content-encoding utf8 \
  --expected-version 'v1:...'
```

큰 문서의 일부는 한 번씩만 나타나는 marker 사이를 교체할 수 있습니다.

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

계속 작업할 파일은 별도로 선택합니다.

```sh
./bin/corpus space select-current --id thesis --path draft.md
```

파일 교체 뒤 반환된 `recovery_id`는 해당 경로의 직전 교체본을 복원할 때 씁니다.

```sh
./bin/corpus space restore \
  --id thesis \
  --recovery-id 'wrec_...' \
  --expected-version 'v1:...'
```

파일 교체 한도는 2 MiB입니다. symlink, hard link, root 밖 경로와 읽은 뒤 달라진 파일은 다루지 않습니다. 삭제에는 최신 `version_token`과 사용자의 삭제 요청이 필요합니다.

## 검색

검색 결과의 `read_ref`를 `corpus_file_read`에 전달하면 현재 Source unit을 읽을 수 있습니다. Connection 상태는 `ready`, `needs_refresh`, `partial`, `unavailable`로 표시됩니다.

Context가 답을 담고 있으면 원문 전체를 다시 읽지 않습니다. 변경된 내용이나 원문 인용이 필요할 때 Source를 다시 읽습니다.

## 저장 범위

Corpus runtime에는 Source 등록과 색인, Context, Work Connection과 직전 교체본이 들어갑니다. Source와 Work 폴더는 원래 위치에 남습니다. 상세 설계는 [DESIGN.md](DESIGN.md), 개발 기준은 [AGENTS.md](AGENTS.md)에 있습니다.

## 라이선스

[Apache License 2.0](LICENSE). [NOTICE](NOTICE)를 함께 따릅니다.
