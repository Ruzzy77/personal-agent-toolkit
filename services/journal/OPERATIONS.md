# Journal 운영 절차

## 백업

배포 migration, 대량 정정과 삭제 전에 원격 D1을 소유자 전용 절대 경로로 내보냅니다.

```sh
./scripts/backup.sh /absolute/private/path/journal-YYYY-MM-DDTHHMM.sql
```

백업 파일 권한이 `600`인지, 파일이 비어 있지 않은지 확인합니다. 백업에는 항목 제목과 요약이 포함되므로 동기화 공유 폴더나 저장소에 두지 않습니다.

## 복구

1. 백업을 새 local state 또는 새 D1에 복구합니다.
2. `weeks`, `items`, `journal_events`, `week_closures`, `corpus_promotion_receipts`, `period_summary_versions`의 행 수를 원본 백업과 대조합니다.
3. 최근 열린 주, 마감 주 하나, 이월 항목 하나와 Corpus 영수증 하나를 API로 다시 읽습니다.
4. 검증한 새 D1로 binding을 전환합니다. 기존 운영 D1에 바로 덮어쓰지 않습니다.

```sh
JOURNAL_D1_PERSIST_TO=/absolute/test/state \
  ./scripts/restore.sh /absolute/private/path/journal-backup.sql DB --local
```

원격 복구가 불가피할 때에만 명시적인 확인값을 사용합니다.

```sh
JOURNAL_RESTORE_CONFIRM=restore:DATABASE \
  ./scripts/restore.sh /absolute/private/path/journal-backup.sql DATABASE --remote
```

## 장애 처리

- **읽기 장애**: Site는 마지막 응답을 정상 데이터처럼 저장하지 않습니다. Worker health, 인증 Worker와 D1 상태를 확인한 뒤 다시 읽습니다.
- **ingest 일부 실패**: 같은 payload와 idempotency key로 재시도합니다. 새 키를 만들지 않습니다.
- **상태 변경 충돌**: 현재 항목을 다시 읽고 새 `expectedVersion`으로 사용자의 선택을 다시 적용합니다.
- **마감 충돌**: 새 마감 준비를 만들고 변경된 요약을 다시 확인합니다.
- **Corpus 반영 실패**: 주를 열린 상태로 두고 성공한 반영만 영수증으로 기록합니다. 실패 영수증은 마감 허가로 취급하지 않습니다.
- **배포 회귀**: 마지막 정상 Worker version으로 되돌린 뒤 새 배포에서 migration을 되감지 않습니다. schema 복구가 필요하면 검증한 백업에서 새 D1을 만듭니다.

## 보존과 삭제

첫 운영에서는 자동 만료를 적용하지 않습니다. 마감 Week와 Event는 기간별 기록의 근거이므로 소유자가 삭제 범위를 확정할 때까지 보존합니다.

삭제는 다음 순서로 수행합니다.

1. 삭제 직전 백업을 만들고 권한과 크기를 확인합니다.
2. 삭제할 주차와 연결된 다음 주 이월 항목, Corpus 반영 영수증을 먼저 조회합니다.
3. 소유자가 주차, 기간 또는 전체 계정 가운데 범위를 다시 확인합니다.
4. 외래 키 순서에 따라 period summary, receipt, event, closure, item, week를 삭제합니다. 부분 삭제가 실패하면 백업에서 새 D1으로 복구합니다.
5. 삭제 뒤 기간 조회와 대표 주차를 다시 읽고, Site와 MCP에서 사라졌는지 확인합니다.

전체 삭제는 운영 Worker와 Site를 먼저 읽기 전용으로 전환한 뒤 수행합니다. 인증 secret과 Keychain 자격 증명도 함께 폐기하되 백업 파일은 별도 보존 결정을 따릅니다.

## 로그와 비밀값

- 요청 body, Authorization header, Site token, ingest token과 원문 Source 내용을 로그에 남기지 않습니다.
- 오류 응답에는 코드, 일반 설명과 충돌 버전만 포함합니다.
- 운영 token은 Worker secret과 macOS Keychain에만 두고 저장소, 자동화 prompt와 백업 파일명에 넣지 않습니다.
- observability에서 예상하지 못한 제목·요약·Source 참조가 보이면 해당 로그를 삭제하고 기록 지점을 수정합니다.

## 배포 확인

1. 테스트와 typecheck를 통과합니다.
2. 원격 백업을 만듭니다.
3. migration을 적용하고 Worker를 배포합니다.
4. health, 현재 주, 항목 검색과 이력을 읽습니다.
5. 소유자 토큰과 자동화 토큰의 허용·거부 범위를 각각 확인합니다.
6. Site에서 같은 항목을 읽고 상태를 왕복 변경한 뒤 사건이 한 번만 생겼는지 확인합니다.
