# Corpus

Corpus는 Context, Source와 Work를 연결하는 로컬 MCP 시스템입니다.

- **Space**: Context와 Connection의 통합 작업면
- **Context**: 출처 기반 재사용 지식
- **Source Connection**: 읽기 전용 원본
- **Work Connection**: 사용자가 연결한 편집 폴더

Source Connection은 읽기 전용 원본을 제공하고, Work Connection은 파일 생성·교체·삭제를 제공합니다.

## MCP 도구

| 도구 | 기능 |
|---|---|
| `corpus_space_list` | Space와 Connection 목록 |
| `corpus_space_get` | Space의 Context와 상태 |
| `corpus_context_skill_revise` | Context Skill 전체를 최신 버전과 대조해 교체 |
| `corpus_space_search` | Source 검색 |
| `corpus_file_list` | Work 파일 목록·검색 |
| `corpus_file_read` | Work 파일 또는 Source unit 읽기 |
| `corpus_file_write` | 파일 생성·전체 교체·구간 교체 |
| `corpus_file_delete` | Work 파일 삭제 |
| `corpus_file_select_current` | Current File 선택 |
| `corpus_file_restore` | 직전 교체본 복원 |

Source 등록·색인, Context item 수정과 Work Connection 연결은 로컬 CLI에서 수행합니다. 사용자가 명시적으로 요청한 Context Skill 전체 교체는 Chat에서도 할 수 있습니다. stdio와 private tunnel은 같은 도구를 제공합니다.

## 설치

Python 3.11 이상과 `uv`가 필요합니다.

```sh
uv sync --frozen
./launchers/corpus --help
./launchers/corpus-mcp
```

기본 데이터 위치는 `~/Library/Application Support/Corpus`입니다.

```sh
CORPUS_DATA_DIR=/absolute/private/path ./launchers/corpus-mcp
```

Loopback HTTP 전송은 다음 환경변수로 엽니다.

```sh
CORPUS_MCP_TRANSPORT=streamable-http \
CORPUS_MCP_HOST=127.0.0.1 \
CORPUS_MCP_PORT=8000 \
./launchers/corpus-mcp
```

## Source

```sh
./launchers/corpus corpus add \
  --id thesis-sources \
  --root /absolute/path/to/sources \
  --execution-policy local_only

./launchers/corpus sync --corpus thesis-sources
```

`scan`은 파일 목록과 메타데이터를 갱신하고, `ingest`는 검색용 Source unit을 만듭니다. `sync`는 두 작업을 이어서 실행합니다. 지원 형식은 Markdown, text, HTML, PDF, DOCX, PPTX, XLSX, HWP와 HWPX입니다. 세부 내용은 [EXTRACTION_ADAPTERS.md](docs/EXTRACTION_ADAPTERS.md)에 있습니다.

HWP/HWPX는 원문에 기록된 표·셀·병합 관계, 제목·목록 속성과 주석·개체의 위치를 함께 읽습니다.
DOCX는 본문·표·중첩 셀을 XML 순서대로 읽고, PPTX는 그룹 도형 안의 글자, 표, 저장된 차트·SmartArt 본문을 읽습니다.
Word 수식의 원본 요소 관계와 SmartArt에 저장된 점·연결 관계도 보존합니다.
PowerPoint 삽입 개체의 저장된 미리보기는 원본 개체와 유일하게 대응할 때만 그림으로 읽습니다.
검색으로 찾은 셀의 행과 제목 셀, 캡션이 필요하면 `corpus_file_read`에
`include_structure_context=true`를 지정합니다. 기본 조회는 해당 unit의 본문만 반환합니다.
macOS에서는 Word, PowerPoint와 HWP/HWPX에 삽입된 그림을 제한된 로컬 OCR로 보완합니다.
본문이 거의 없는 문서와 슬라이드를 먼저 처리하며, 남은 그림은 다음 갱신에서 이어 읽습니다.
OCR 결과에는 그림의 원본 위치와 인식 신뢰도를 남깁니다. 잘린 그림은 원본 대응과 보이는 영역을
확인할 수 있을 때만 읽으며, 불명확한 자르기와 미지원 그림 형식은 별도 경고로 남깁니다.
HWP의 BinData 레코드와 HWPX의 패키지 참조를 대조하며, 외부 연결 그림은 열지 않습니다.
시각 배치나 지원하지 않는 개체 내용은 부분 추출로 표시합니다.

PDF는 한 번에 최대 200쪽을 읽고, 남은 쪽은 다음 갱신에서 이어 처리합니다.
이어 처리에 실패하면 기존 색인을 보존합니다. `status`와 갱신 스크립트의 최종 JSON에는
부분 추출 문서의 형식별·문제별 집계가 포함됩니다. 실제 추출 실패, 처리 한도, 미지원 형식,
미해결 구조와 읽기 순서 미확인도 구분합니다. 분류별 수치는 같은 문서를 중복 집계할 수 있습니다.

배포용 HWP의 페이지 본문을 복구하려면 고정된 `rhwp` 실행 파일을 Corpus 캐시에 한 번 설치합니다.

```sh
python3 scripts/provision_rhwp.py
```

자동 갱신은 파일당 250 MiB 제한을 유지합니다. 그보다 큰 로컬 파일은 inventory에서 확인한 문서 ID를 정확히 지정하고 한 파일씩 색인할 수 있습니다. 이 경로도 파일당·실행당 1 GiB를 넘지 않습니다.

```sh
./launchers/corpus ingest \
  --corpus thesis-sources \
  --document-id 'doc_...' \
  --max-files 1 \
  --max-bytes 1GiB \
  --max-file-bytes 1GiB
```

이 동작은 등록된 로컬 원본을 임시 사본으로 읽으며 원본, 등록 경로와 Source 범위를 변경하지 않습니다.

더 이상 존재하지 않는 Source 등록은 로컬에서 해제할 수 있습니다.

```sh
./launchers/corpus corpus unregister \
  --id thesis-sources \
  --expected-root /absolute/path/to/sources \
  --confirm-unregister
```

현재 등록 경로가 `--expected-root`와 다르거나 Context·Work Connection이 남아 있으면 중단합니다. 등록만 해제하며 비공개 색인은 이후 정리를 위해 그대로 둡니다.

이미 다른 정본으로 옮겨진 보관 Context가 유일한 연결이라면, 그 Context의 현재 version을 지정해 Corpus 내부의 연결 기록과 함께 정리할 수 있습니다.

```sh
./launchers/corpus corpus unregister \
  --id thesis-sources \
  --expected-root /absolute/path/to/sources \
  --remove-archived-context retired-thesis-context \
  --expected-context-version 6 \
  --confirm-remove-linked-history \
  --confirm-unregister
```

이 동작은 삭제 전 `catalog.sqlite`와 `contexts.sqlite3`의 비공개 사본을 남깁니다. 지정한 Context가 보관 상태가 아니거나 다른 Context·Work Connection·Context Skill이 남아 있으면 중단합니다. 원래 Source 폴더, Codex·Claude의 세션 파일과 Corpus 비공개 색인은 삭제하지 않습니다.

## Context와 Work Connection

Context는 출처 기반 재사용 지식을 저장합니다.

```sh
./launchers/corpus context create \
  --id thesis \
  --payload-file /absolute/path/to/context.json
```

편집 폴더는 Context에 연결합니다.

```sh
./launchers/corpus workspace connect \
  --id thesis \
  --context thesis \
  --name "Thesis" \
  --root /absolute/path/to/thesis \
  --execution-policy external_host_allowed
```

`local_only` Connection은 로컬에서 사용합니다. `external_host_allowed` Work Connection은 Chat에서 읽고 쓸 수 있습니다.

```sh
./launchers/corpus space list
./launchers/corpus space show --id thesis
```

Context Skill은 private Corpus 저장소에 두며 선택한 Space와 함께 읽습니다. Chat에서 고칠 때에는
Space를 다시 열어 현재 `version`을 받은 뒤 이름, 설명과 지침 전체를 `corpus_context_skill_revise`에
전달합니다. Source Connection은 이 동작과 관계없이 읽기 전용으로 남습니다.

## 파일 편집

새 파일에는 `expected_version=absent`를 사용합니다.

```sh
./launchers/corpus space write \
  --id thesis \
  --path notes/new-section.md \
  --content-file /absolute/path/to/new-section.md \
  --content-encoding utf8 \
  --expected-version absent
```

기존 파일은 편집 직전에 읽고 받은 `version_token`으로 교체합니다.

```sh
./launchers/corpus space read --id thesis --path draft.md --max-chars 200000
./launchers/corpus space write \
  --id thesis \
  --path draft.md \
  --content-file /absolute/path/to/revised-draft.md \
  --content-encoding utf8 \
  --expected-version 'v1:...'
```

큰 문서의 일부는 한 번씩만 나타나는 marker 사이를 교체할 수 있습니다.

```sh
./launchers/corpus space write \
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
./launchers/corpus space select-current --id thesis --path draft.md
```

파일 교체 뒤 반환된 `recovery_id`는 해당 경로의 직전 교체본을 복원할 때 씁니다.

```sh
./launchers/corpus space restore \
  --id thesis \
  --recovery-id 'wrec_...' \
  --expected-version 'v1:...'
```

파일 교체 용량은 2 MiB입니다. 편집 표면은 root 내부의 변경되지 않은 일반 파일로 구성됩니다. 삭제는 사용자의 요청과 최신 `version_token`으로 수행합니다.

## 검색

검색 결과의 `read_ref`를 `corpus_file_read`에 전달하면 현재 Source unit을 읽을 수 있습니다. Connection 상태는 `ready`, `needs_refresh`, `partial`, `unavailable`로 표시됩니다.

Context는 기본 조회 자료입니다. 최신 정보와 원문 인용은 Source에서 조회합니다.

## 저장 범위

Corpus runtime에는 Source 등록과 색인, Context, Work Connection과 직전 교체본이 들어갑니다. Source와 Work 폴더는 원래 위치에 남습니다. 사용법은 이 문서에, 제품의 구조와 경계는 [DESIGN.md](DESIGN.md)에, 외부 형식은 [docs](docs/)에 둡니다.

## 라이선스

[Apache License 2.0](LICENSE). [NOTICE](NOTICE)를 함께 따릅니다.
