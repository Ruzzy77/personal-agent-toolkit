# Corpus

Corpus는 원자료에서 추출한 지식을 원자료 위치와 독립된 원격 저장층에 보관하고,
Context·Source·Work를 여러 AI client에 같은 MCP로 연결합니다.

- **Space**: Context와 Connection의 통합 작업면
- **Context**: 출처 기반 재사용 지식
- **Source Connection**: 새 내용을 받아들이는 읽기 전용 원자료
- **Work Connection**: 사용자가 연결한 편집 폴더

## MCP 도구

| 도구 | 기능 |
| --- | --- |
| `corpus_space_list` | Space와 Connection 목록 |
| `corpus_space_get` | Space의 Context와 상태 |
| `corpus_context_items_revise` | 선택한 Context 항목을 현재 version과 대조해 교체 |
| `corpus_context_skill_revise` | Context Skill 전체를 현재 version과 대조해 교체 |
| `corpus_space_search` | 저장된 Source record 검색 |
| `corpus_source_refresh` | 지정한 문서의 로컬 재분석 요청 |
| `corpus_job_status` | Source·Work 작업 상태 확인 |
| `corpus_file_list` | Work 파일 목록·검색 |
| `corpus_file_read` | Work 파일 또는 Source unit 읽기 |
| `corpus_file_write` | Work 파일 생성·교체 |
| `corpus_file_delete` | Work 파일 삭제 |
| `corpus_file_select_current` | Current File 선택 |
| `corpus_file_restore` | 직전 교체본 복원 |

### Source 읽기

검색 결과의 `read_ref`를 `corpus_file_read`에 전달하고 일반 읽기에는 `source_view="text"`를
지정합니다. 본문은 한 번만 반환하며, 공통 `source`에는 해당 revision의 `captured_at`과 상태,
`spans`에는 이번 페이지와 원래 unit의 위치·구조·품질 정보가 담깁니다.

표의 해당 행·병합 범위·선언된 제목 셀이나 주석의 소유 문단이 필요하면
`include_structure_context=true`를 사용합니다. `has_more=true`이면 같은 참조와 옵션을 유지하고
`next_start_char`로 이어 읽습니다. `text`의 위치는 Unicode code point 기준입니다.

완전한 unit 본문·해시·anchor·geometry는 `source_view="full"`로 확인합니다. 생략할 때도 기존
`full` 응답을 유지하며, 이 형식의 페이지 위치는 UTF-16 기준입니다. 두 형식의 위치를 섞지 않습니다.
선택 unit 500개 또는 결과 객체 2 MiB를 넘는 요청은 `budget_exceeded`로 중단합니다. `full`도 이
제한을 적용하므로 큰 결과는 `text`와 좁은 이웃·구조 범위로 읽습니다. 본문 페이지는 1,000–200,000자,
이어 읽을 전체 선택 범위는 최대 2,097,152 code point입니다. Work 읽기 방식은 바뀌지 않습니다.

OpenAI에서는 Corpus Skill을 `Personal Agent Toolkit` 통합 plugin에 포함하고, Claude의 Corpus
plugin은 다음 상시 원격 MCP를 직접 선언합니다.

```text
https://personal-agent-context.hiyaq77.workers.dev/corpus/mcp
```

Finder의 Source와 Work 권한은 [`apps/sync`](../../apps/sync/README.md)에만 둡니다. Sync가
Document Files와 함께 사용하는 로컬 Corpus 구현, 최초 이관 CLI와 상세 운영 문서는
[`engines/corpus`](../../engines/corpus/README.md)에 있으며 plugin 배포 묶음에는 포함하지
않습니다.

제품의 데이터·동기화 경계는 [DESIGN.md](DESIGN.md)에 있습니다.

## 라이선스

[Apache License 2.0](LICENSE). [NOTICE](NOTICE)를 함께 따릅니다.
