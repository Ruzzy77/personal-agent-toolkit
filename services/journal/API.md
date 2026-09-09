# Journal API and MCP Contract

모든 날짜·시간은 ISO 8601 문자열이며, 주 ID는 KST 기준 월요일 `YYYY-MM-DD`입니다. HTTP API는 성공 시 `{ "ok": true, "result": ... }`, 실패 시 `{ "ok": false, "error": { "code", "message" } }`를 반환합니다.

## HTTP API

| Method | Path | Scope | Purpose |
| --- | --- | --- | --- |
| `GET` | `/health` | public | 배포 상태 확인 |
| `GET` | `/.well-known/oauth-protected-resource` | public | MCP OAuth 리소스 메타데이터 |
| `GET` | `/api/v1/board?week=YYYY-MM-DD&include_resolved=false` | `journal.read` | 주간 보드 조회 |
| `GET` | `/api/v1/items?...` | `journal.read` | 날짜·문구·프로젝트·분류·처리 상태로 항목 검색 |
| `GET` | `/api/v1/items/{id}` | `journal.read` | 항목, 관련 주차, 사건 이력과 정정 조회 |
| `POST` | `/api/v1/items:ingest` | `journal.ingest` or `journal.write` | 변화 관찰을 멱등 반영 |
| `PATCH` | `/api/v1/items/{id}/resolution` | owner `journal.write` | 완료·보류·취소·재개 확인 |
| `POST` | `/api/v1/weeks/{id}:prepare-close` | owner `journal.close` | 주간 요약, 이월, Corpus 후보와 준비 버전 계산 |
| `POST` | `/api/v1/weeks/{id}:confirm-close` | owner `journal.close` | 준비 버전을 대조하고 주간 마감 확정 |
| `POST` | `/api/v1/weeks/{id}/corrections` | owner `journal.write` | 마감 주 정정 사건 추가 |
| `GET` | `/api/v1/period?kind=week&anchor=YYYY-MM-DD` | `journal.read` | 일·주·월·분기·연간 집계 |
| `POST` | `/api/v1/period-summaries` | owner `journal.write` | 사용자 편집 기간 요약의 새 버전 저장 |
| `POST` | `/api/v1/corpus-promotions` | owner `journal.write` | Corpus 반영 영수증 기록 |

쓰기 요청에는 호출자가 만든 `idempotencyKey`가 필요합니다. 같은 키를 다시 보내면 새 사건을 만들지 않습니다. 항목 처리 결과 변경은 `expectedVersion`으로 낙관적 동시성 검사를 할 수 있습니다.

현재 주의 board·week 검색·주간 집계에는 이전 주의 최신 진행 중·보류 항목도 포함됩니다. 읽기는 저장을 바꾸지 않으며 응답 항목의 `weekId`는 원본 주를 유지할 수 있습니다. 이전 항목을 현재 주에서 처리하면 새 인스턴스를 반환하므로 쓰기 응답의 `item.id`를 사용합니다. 과거 원본과 사건은 보존되며 최신 후속 상태가 있는 원본에 대한 쓰기는 충돌로 거절합니다. 자동 수집은 새 주에서도 사용자 resolution을 유지합니다.

항목 응답의 `id`는 해당 주차의 조작 가능한 인스턴스이고, `logicalItemId`는 여러 주에 걸친 같은 일을 연결합니다. `responsibility`는 `user`, `counterparty`, `system` 가운데 하나입니다. 주간 마감에서는 `active`와 `held` 항목을 다음 주 인스턴스로 만들며 `summary.rolloverCount`와 `summary.rolloverTitles`에 결과를 남깁니다.

마감은 준비와 확정을 분리합니다. 준비 응답의 `preparationVersion`은 주간 항목 버전과 다음 주 이월 상태를 포함합니다. 확정 전에 항목이 달라지면 `close_preparation_stale`로 거부합니다. 미처리·실패 Corpus 후보가 있어도 소유자가 명시적으로 요청하면 마감할 수 있으며, 마감이 영수증 상태를 바꾸지는 않습니다. 이전 `/api/v1/weeks/{id}:close` 경로는 호환 별칭으로만 남고 같은 준비 버전을 요구합니다.

마감 주의 보드에는 `closure.summary`, 고정된 `closure.corpusCandidates`와 후속 `closure.corrections`가 포함됩니다. 각 후보의 `reflectionStatus`는 일치하는 영수증에서 계산한 `pending`, `failed`, `applied`, `skipped`이며 저장된 마감 snapshot을 바꾸지 않습니다. 열린 주의 `closure`는 null입니다. 실패 상세와 개별 시도는 항목 이력에서 읽습니다.

반영 영수증은 마감 전후에 추가할 수 있습니다. 마감 뒤에는 `itemId + targetSpace + contentHash`가 해당 `week_closures`의 고정 후보와 일치해야 하며, 새 후보·itemId 없는 영수증은 받지 않습니다. 열린 주의 기존 item-less 계약은 유지합니다. 저장 시 열린 주의 항목 version 또는 마감 주의 고정 후보를 D1에서 다시 대조하므로 마감과 영수증의 경합도 같은 조건을 따릅니다. 실패 시도는 보존하고 완료는 후보별 한 건만 허용합니다. correction은 고정 후보나 완료 영수증을 바꾸지 않으므로 반영 전 함께 읽어 의미를 판단해야 합니다. 영수증은 외부 Corpus 쓰기 잠금이 아닙니다.

## MCP tools

- `journal_get_board`
- `journal_find_items`
- `journal_get_item_history`
- `journal_ingest_items`
- `journal_set_resolution`
- `journal_prepare_week_close`
- `journal_confirm_week_close`
- `journal_add_correction`
- `journal_get_period`
- `journal_save_period_summary`
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
  "responsibility": "counterparty",
  "dueAt": null,
  "durableOutcome": null,
  "corpusTargetSpace": null
}
```

`sourceKind + sourceKey`가 항목의 외부 식별자입니다. 새 `idempotencyKey`로 관찰이 갱신되어도 Journal 항목 ID는 유지됩니다.
