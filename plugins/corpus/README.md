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

OpenAI plugin은 Skill과 등록 app을 함께 배포하고, Claude plugin은 다음 상시 원격 MCP를 직접
선언합니다.

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
