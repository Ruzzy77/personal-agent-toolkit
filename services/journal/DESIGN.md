# Journal Service Design

Journal은 Personal Agent Toolkit 안에서 Sense, Corpus, Hypes와 나란히 동작하는 개인 업무 기록 제품입니다. 일간 브리핑을 매일 새 문서로 저장하지 않고, 월요일부터 일요일까지 하나의 주간 상태를 계속 갱신합니다. 마감된 주는 그대로 보존하고 이후 정정은 별도 사건으로만 덧붙입니다.

## 책임 경계

- **Journal**: 업무 항목, 현재 분류, 사용자 확인 상태, 출처 참조, 사건 이력, 주간 마감, 기간별 조회를 보관합니다.
- **Corpus**: 프로젝트에 오래 남아야 하는 결과와 현재 맥락을 각 프로젝트의 기존 문서와 색인에 반영합니다. Journal 전체를 복제한 종합 Space는 만들지 않습니다.
- **Daily Monitoring**: 연결된 자료에서 변화만 찾아 Journal에 관찰 결과를 멱등적으로 반영합니다. 원문 이메일이나 문서 본문은 Journal에 복사하지 않습니다.
- **Sites**: 소유자 전용 화면과 열린 페이지 범위의 WebMCP 작업을 제공합니다. 서비스 비밀값은 브라우저로 보내지 않고 서버 경유 요청에만 사용합니다.

## 상태 모델

분류와 처리 결과를 분리합니다.

- `lane`: `today`, `direct`, `waiting`, `attention`
- `resolution`: `active`, `held`, `completed`, `canceled`
- `responsibility`: `user`, `counterparty`, `system`

자동 수집은 제목, 현재 상태, 분류와 출처 참조를 갱신할 수 있습니다. `held`, `completed`, `canceled` 판정은 소유자 확인 또는 명시적인 정본 근거가 있을 때만 바뀝니다. 첫 구현에서는 Sites와 OAuth MCP에서 실행한 처리를 소유자 확인으로 보고, 수집 토큰은 처리 결과를 바꾸지 못하게 합니다.

## 시간과 보존

- 기준 시간대는 `Asia/Seoul`입니다.
- 주 ID는 해당 주 월요일의 `YYYY-MM-DD`입니다.
- 열린 주는 수정 가능하며, 마감된 주의 항목은 수정하지 않습니다.
- 같은 원본 업무는 주차마다 별도 항목 인스턴스를 가지되 `logical_item_id`를 공유합니다. 따라서 지난주의 최종 상태를 보존하면서 다음 주에서 계속 처리할 수 있습니다.
- 주간 마감 시 `active`와 `held` 항목은 다음 주로 이월하고 모두 `active`로 다시 시작합니다.
  완료·취소 항목은 마감 주에만 남깁니다. 이월 인스턴스는 지난주의 확정 결과와 Corpus 후보를
  복사하지 않습니다.
- 모든 중요한 변경은 `journal_events`에 멱등 키와 함께 추가합니다.
- 마감 시점의 요약과 Corpus 반영 후보는 `week_closures`에 고정합니다.
- 주간 마감은 준비와 확정을 분리합니다. 준비 버전 이후 항목이 바뀌면 확정을 거부하고, 모든 Corpus 후보가 적용 또는 명시적으로 건너뛴 영수증을 가져야 주를 닫습니다.
- 사용자가 고친 월·분기·연 요약은 이전 본문을 덮어쓰지 않고 `period_summary_versions`에 추가합니다. 각 버전은 작성 당시 기간의 Event ID를 보존해 원 기록으로 되짚을 수 있습니다.

## 데이터 최소화

항목에는 짧은 제목, 현재 상태, 프로젝트 키, 출처 종류와 참조만 둡니다. 이메일 본문, 파일 내용,
대화 원문, 사용자 자격 증명은 저장하지 않습니다. Corpus 반영 후보는 `durable_outcome`과
`corpus_target_space`가 명시된 항목만 대상으로 하며, Journal의 자동 요약을 곧바로 Corpus
색인에 넣지 않습니다. 반영 receipt에는 프로젝트 root 기준 상대 경로나 비경로 표식만 저장하고
로컬 절대 경로는 거부합니다.

## 접근 경로

```text
Daily Monitoring ──ingest token──▶ Journal Worker + D1
Chat / Codex ──OAuth──▶ Journal MCP
Private Journal Site ──server-held site token──▶ Journal API
Open Journal page ──WebMCP──▶ visible Site actions ──▶ Journal API
Weekly close ──candidate + receipt──▶ project Work/Source update ──▶ Corpus refresh
```

인증 Worker는 OAuth 토큰을 검증하고 서비스별 권한을 분리합니다. Site 토큰은 소유자 전용 Site의 서버 경로에서만 사용하고, 수집 토큰은 관찰 반영에만 사용합니다.

## 1차 검증 기준

같은 주의 출처 항목을 다시 수집해도 동일한 주간 항목 ID가 유지되어야 합니다. 주간 마감 뒤에는 같은 논리 항목 ID를 가진 새 주간 인스턴스가 생기고 지난주 인스턴스는 바뀌지 않아야 합니다. 해당 주간 ID를 MCP와 Site에서 읽고, Site 또는 OAuth MCP에서 완료·보류·취소하면 같은 레코드와 사건 이력에 한 번만 반영되어야 합니다.
