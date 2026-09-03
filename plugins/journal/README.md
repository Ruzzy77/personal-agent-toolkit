# Journal

Journal은 여러 AI 클라이언트와 함께 쓰는 개인용 불릿저널입니다. 한 주의 항목을 계속 갱신하고,
완료·보류·취소는 사용자가 확정하며, 마감된 주간 기록에서 월·분기·연 단위 흐름을 읽습니다.

- 소유자 전용 Site: <https://personal-journal.ruzzy.chatgpt.site>
- 원격 MCP: `https://personal-agent-journal.hiyaq77.workers.dev/mcp`
- 저장: 소유자의 Cloudflare D1

Journal에는 진행 판단에 필요한 요약과 원본 참조만 저장합니다. 이메일과 문서 원문은 원래 서비스나 파일에 남습니다.

## 도구

- `journal_get_board`: 주간 진행 보드
- `journal_find_items`: 조건별 항목 찾기
- `journal_get_item_history`: 관련 주차와 상태 이력
- `journal_ingest_items`: 모니터링 결과 반영
- `journal_set_resolution`: 사용자가 확인한 완료·보류·취소·재개
- `journal_prepare_week_close`: 주간 마감 준비
- `journal_confirm_week_close`: Corpus 반영 결과 확인 뒤 주간 마감 확정
- `journal_add_correction`: 마감 뒤 정정 기록
- `journal_get_period`: 일·주·월·분기·연 조회
- `journal_save_period_summary`: 사용자가 다듬은 기간 요약의 새 버전 저장
- `journal_record_corpus_promotion`: 프로젝트 Corpus 반영 receipt

## 외부 모니터용 로컬 클라이언트

원격 MCP를 직접 호출할 수 없는 별도 모니터에서만 `launchers/journal`을 사용합니다. 인증된 MCP를
쓸 수 있는 자동화는 `manage-journal` Skill과 `journal_ingest_items`를 사용합니다.
`launchers/journal`은 읽기와 ingest 전용 자격 증명을 환경 변수 또는 macOS Keychain에서 읽으며,
토큰은 저장소와 자동화 프롬프트에 넣지 않습니다.

```sh
./plugins/journal/launchers/journal health
./plugins/journal/launchers/journal board --include-resolved
./plugins/journal/launchers/journal ingest --input /path/to/items.json
```

Keychain service 이름은 `personal-agent-journal-ingest`입니다. 운영 자격 증명은 저장소 밖에서 배포자가 설정합니다.

서비스·Site·plugin의 실행 경계는 [DESIGN.md](./DESIGN.md)에 있습니다.

## 라이선스

[Apache License 2.0](LICENSE). [NOTICE](NOTICE)를 함께 따릅니다.
