# Corpus

Corpus는 원자료에서 추출한 지식을 내구성 있게 보관하고 Context, Source와 Work를 연결하는 로컬 MCP 시스템입니다.

- **Space**: Context와 Connection의 통합 작업면
- **Context**: 출처 기반 재사용 지식
- **Source Connection**: 새 내용을 받아들이는 읽기 전용 원자료
- **Work Connection**: 사용자가 연결한 편집 폴더

Source Connection은 갱신 입력을 제공하고, Corpus record는 마지막으로 정상 추출된 본문과 구조를 원자료 위치와 별개로 유지합니다. Work Connection은 파일 생성·교체·삭제를 제공합니다.

## MCP 도구

| 도구 | 기능 |
|---|---|
| `corpus_space_list` | Space와 Connection 목록 |
| `corpus_space_get` | Space의 Context와 상태 |
| `corpus_context_items_revise` | 선택한 Context 항목의 종류·본문·상태를 현재 Context 버전과 대조해 일괄 교체 |
| `corpus_context_skill_revise` | Context Skill 전체를 최신 버전과 대조해 교체 |
| `corpus_space_search` | Source 검색 |
| `corpus_file_list` | Work 파일 목록·검색 |
| `corpus_file_read` | Work 파일 또는 Source unit 읽기 |
| `corpus_file_write` | 파일 생성·전체 교체·구간 교체 |
| `corpus_file_delete` | Work 파일 삭제 |
| `corpus_file_select_current` | Current File 선택 |
| `corpus_file_restore` | 직전 교체본 복원 |

Source 등록·색인, Context 생성·보관과 Work Connection 연결은 로컬 CLI에서 수행합니다. 사용자가 명시적으로 요청한 기존 Context 항목의 종류·본문·상태와 Context Skill 전체 교체는 Chat에서도 할 수 있습니다. stdio와 private tunnel은 같은 도구를 제공합니다.

## 설치

Python 3.11 이상, `uv`, 활성화된 Document Files 플러그인이 필요합니다.

```sh
uv sync --frozen
./launchers/corpus --help
./launchers/corpus-mcp
```

기본 데이터 위치는 `~/Library/Application Support/Corpus`입니다.

```sh
CORPUS_DATA_DIR=/absolute/private/path ./launchers/corpus-mcp
```

macOS에서는 설치된 Corpus plugin을 찾아 자동 갱신을 계속하는 사용자 LaunchAgent를 한 번 설치할 수 있습니다. 이 실행기는 특정 Agent-Workspace 절대 경로를 저장하지 않으므로 저장소 폴더를 Finder에서 옮겨도 설치된 plugin을 다시 찾습니다.

```sh
./launchers/install-maintenance install
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

`scan`은 파일 목록과 메타데이터를 갱신하고, `ingest`는 내구성 있는 record와 검색용 Source unit을 만듭니다. `sync`는 두 작업을 이어서 실행합니다. 지원 형식은 Markdown, text, HTML, PDF, DOCX, PPTX, XLSX, HWP와 HWPX입니다. 세부 연동 규격은 [EXTRACTION_ADAPTERS.md](docs/EXTRACTION_ADAPTERS.md)에 있습니다.

등록할 때 폴더 위치와 별개인 ID 및 파일시스템 정체성을 저장합니다. 같은 볼륨 안에서 Finder로 폴더 이름이나 위치를 바꾸면 다음 자동 갱신 또는 Source 접근 때 새 위치를 찾아 등록 경로를 고칩니다. 다른 볼륨으로 옮겼거나 운영체제가 정체성을 제공하지 못하면 기존 `rebind-root`를 사용합니다. 파일명과 공개 경로는 NFC로 비교·표시하지만 디스크의 실제 이름을 강제로 바꾸지 않습니다.

원자료가 삭제되거나 일시적으로 연결되지 않아도 마지막 정상 record는 검색하고 읽을 수 있습니다. 새 추출이 실패해도 정상 record를 실패 결과로 교체하지 않습니다. `source_state`는 원자료의 이용 가능성과 변경 여부를, `record_state`는 저장된 추출 결과의 이용 가능성과 추출기 현재성을 각각 나타냅니다. 따라서 원자료가 `unavailable`이어도 record가 `ready`일 수 있습니다.

문서 형식별 파싱, OCR, 구조 단위와 추출 범위 판정은 Document Files가 담당합니다. Corpus는 등록된 원본을 읽기 전용 임시 사본으로 캡처하고, Document Files의 검증된 결과에 revision·projection·Source unit ID와 anchor를 부여해 검색에 연결합니다. Corpus에는 PDF·Office·HWP/HWPX 파서나 해당 라이브러리를 포함하지 않습니다.

Document Files는 큰 PDF의 페이지 구간과 Office 계열 문서의 그림 구간을 한 추출 프로세스 안에서 이어 처리한 뒤 결과를 반환합니다. Corpus는 형식별 후속 처리나 별도 OCR을 예약하지 않습니다. `status`와 갱신 스크립트의 최종 JSON에는 부분 추출 문서의 형식별·문제별 집계가 포함되며, 실제 추출 실패, 처리 한도, 미지원 형식, 미해결 구조와 읽기 순서 미확인을 구분합니다. 분류별 수치는 같은 문서를 중복 집계할 수 있습니다.

검색으로 찾은 셀의 행과 제목 셀, 캡션이 필요하면 `corpus_file_read`에 `include_structure_context=true`를 지정합니다. 기본 조회는 해당 unit의 본문만 반환합니다.

HWP/HWPX 변환·렌더링용 `rhwp` 설치와 형식별 백엔드 관리는 Document Files에서 수행합니다.

자동 갱신은 파일당 250 MiB 제한을 유지합니다. 그보다 큰 로컬 파일은 inventory에서 확인한 문서 ID를 정확히 지정하고 한 파일씩 색인할 수 있습니다. 이 경로도 파일당·실행당 1 GiB를 넘지 않습니다.

```sh
./launchers/corpus ingest \
  --corpus thesis-sources \
  --document-id 'doc_...' \
  --max-files 1 \
  --max-bytes 1GiB \
  --max-file-bytes 1GiB
```

이 동작은 등록된 로컬 원본을 임시 사본으로 읽으며 원본과 Source 범위를 변경하지 않습니다. 추출이 끝나면 원본 바이트 사본은 필수 보관 대상이 아니며, 구조화된 record가 Corpus의 지속 자료가 됩니다.

### 자동 갱신과 변경 대기열

`corpus-maintenance`는 파일 변경을 잠시 모아 중복을 합친 뒤 갱신합니다. 대기열은 최대 2,048개이며 넘치면 항목을 계속 쌓지 않고 전체 대조 한 건으로 합칩니다. 성공한 대조 뒤 항목을 지우므로 장기 변경 이력처럼 커지지 않습니다. 파일 감시가 끊겨도 기본 15분마다 전체 대조하고, 프로세스 시작 시에도 한 번 대조합니다.

```sh
./launchers/corpus-maintenance --once
./launchers/corpus-maintenance
```

### record 보존과 정리

record는 `protected`, `managed`, `transient` 세 등급으로 관리합니다. Context가 현재 참조하는 record와 사용자가 `protected`로 지정한 record는 자동 삭제하지 않습니다. 그 밖의 원자료 분리 record는 마지막 사용 시점과 등급별 고정 기간에 따라 `active → archived → trash → purge` 순서로 정리합니다. 내용의 의미나 중요도를 모델이 추측해 삭제하지 않습니다.

```sh
./launchers/corpus retention status --corpus thesis-sources
./launchers/corpus retention set \
  --corpus thesis-sources \
  --document-id 'doc_...' \
  --class protected
./launchers/corpus retention restore \
  --corpus thesis-sources \
  --document-id 'doc_...'
```

더 이상 존재하지 않는 Source 등록은 로컬에서 해제할 수 있습니다.

```sh
./launchers/corpus corpus unregister \
  --id thesis-sources \
  --expected-root /absolute/path/to/sources \
  --confirm-unregister
```

현재 등록 경로가 `--expected-root`와 다르거나 Context·Work Connection이 남아 있으면 중단합니다. 단순히 원자료가 이동했거나 잠시 사라진 경우에는 등록을 해제하지 않습니다. 등록 해제는 새 갱신을 완전히 끊는 별도 작업이며, 남은 비공개 데이터는 명시적 이관·정리 절차의 대상으로 둡니다.

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

기존 Context 항목을 고칠 때에도 먼저 Space를 다시 열고 Context의 현재 정수 `version`과 대상
`item_id`를 읽습니다. `corpus_context_items_revise`에는 각 대상의 `kind`, `body_text`와
`status` 완전값을 한 번에 전달합니다. 요청은 한 transaction으로 적용되며 대상이 없거나 version이
바뀌면 어느 항목도 수정하지 않습니다. `status`는 기존 `attributes.status`를 교체하고, 다른 속성과
Source 연결은 그대로 둡니다. 이 도구는 항목을 추가·삭제하거나 Source 연결을 바꾸지 않습니다.

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

검색 결과의 `read_ref`를 `corpus_file_read`에 전달하면 저장된 Source unit을 읽을 수 있습니다. 각 결과의 `captured_at`, `source_state`, `record_state`로 추출 시점과 원자료·record 상태를 구분합니다.

Context는 미리 분석한 재사용 맥락이고, record는 캡처 시점의 추출 근거입니다. 최신 정보나 현재 원문 일치가 필요한 작업은 `source_state`와 `captured_at`을 확인하고 필요하면 갱신합니다. 원자료가 없어도 Corpus가 보관한 맥락과 record는 계속 활용할 수 있습니다.

## 저장 범위

Corpus runtime에는 Source 위치 등록, 내구성 있는 추출 record, 재구축 가능한 검색 투영, Context, Work Connection과 직전 교체본이 들어갑니다. Source와 Work 폴더는 사용자가 정한 위치에 남으며 같은 볼륨의 이동은 자동으로 따라갑니다. 사용법은 이 문서에, 제품의 구조와 경계는 [DESIGN.md](DESIGN.md)에, 외부 형식은 [docs](docs/)에 둡니다.

## 라이선스

[Apache License 2.0](LICENSE). [NOTICE](NOTICE)를 함께 따릅니다.
