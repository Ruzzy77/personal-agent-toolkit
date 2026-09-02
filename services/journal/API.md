# Journal API and MCP Contract

모든 날짜·시간은 ISO 8601 문자열이며, 주 ID는 KST 기준 월요일 `YYYY-MM-DD`입니다. HTTP API는 성공 시 `{ "ok": true, "result": ... }`, 실패 시 `{ "ok": false, "error": { "code", "message" } }`를 반환합니다.

## HTTP API

| Method | Path | Scope | Purpose |
| --- | --- | --- | --- |
| `GET` | `/health` | public | 배포 상태 확인 |
| `GET` | `/.well-known/oauth-protected-resource` | public | MCP OAuth 리소스 메타데이터 |
| `GET` | `/api/v1/board?week=YYYY-MM-DD&include_resolved=false` | `journal.read` | 주간 보드 조회 |
| `POST` | `/api/v1/items:ingest` | `journal.ingest` or `journal.write` | 변화 관찰을 멱등 반영 |
| `PATCH` | `/api/v1/items/{id}/resolution` | owner `journal.write` | 완료·보류·취소·재개 확인 |
| `POST` | `/api/v1/weeks/{id}:close` | owner `journal.close` | 주간 마감, 진행·보류 항목 이월 및 Corpus 후보 고정 |
| `POST` | `/api/v1/weeks/{id}/corrections` | owner `journal.write` | 마감 주 정정 사건 추가 |
| `GET` | `/api/v1/period?kind=week&anchor=YYYY-MM-DD` | `journal.read` | 일·주·월·분기·연간 집계 |
| `POST` | `/api/v1/corpus-promotions` | `journal.ingest` or `journal.write` | Corpus 반영 영수증 기록 |

쓰기 요청에는 호출자가 만든 `idempotencyKey`가 필요합니다. 같은 키를 다시 보내면 새 사건을 만들지 않습니다. 항목 처리 결과 변경은 `expectedVersion`으로 낙관적 동시성 검사를 할 수 있습니다.

항목 응답의 `id`는 해당 주차의 조작 가능한 인스턴스이고, `logicalItemId`는 여러 주에 걸친 같은 일을 연결합니다. 주간 마감에서는 `active`와 `held` 항목을 다음 주 인스턴스로 만들며 `summary.rolloverCount`와 `summary.rolloverTitles`에 결과를 남깁니다.

## MCP tools

- `journal_get_board`
- `journal_ingest_items`
- `journal_set_resolution`
- `journal_close_week`
- `journal_add_correction`
- `journal_get_period`
- `journal_record_corpus_promotion`

도구는 HTTP API와 같은 서비스 메서드를 사용합니다. 읽기 도구는 `readOnlyHint`, 상태 변경 도구는 명시적인 비읽기 annotation을 갖습니다.

## Ingest item

```json
{
  "idempotencyKey": "gmail:message-id:2026-09-02T04:00:00Z",
  "sourceKind": "gmail",
  "sourceKey": "thread-or-task-stable-key",
  "sourceRef": "gmail:message-id",
  "sourceVersion": "message-id-or-updated-at",
  "weekId": "2026-08-31",
  "projectKey": "industrial-ai",
  "title": "협약변경 공문",
  "summary": "연구지원팀 회신 대기",
  "lane": "waiting",
  "dueAt": null,
  "durableOutcome": null,
  "corpusTargetSpace": null
}
```

`sourceKind + sourceKey`가 항목의 외부 식별자입니다. 새 `idempotencyKey`로 관찰이 갱신되어도 Journal 항목 ID는 유지됩니다.
