# Personal Agent Journal service

Journal의 D1 데이터, HTTP API와 remote MCP를 제공하는 Cloudflare Worker입니다. `sites/journal`의 화면과 Daily Monitoring은 같은 항목·이벤트·주간 마감 기록을 사용합니다.

## 로컬 검사

```sh
npm install
npm run check
```

시험은 다음 계약을 확인합니다.

- KST 월요일–일요일 주차와 기간 경계
- stable source identity와 ingest idempotency
- 주차별 인스턴스와 여러 주에 걸친 logical item identity
- 관찰 갱신과 사용자 resolution 분리
- 담당 주체와 분류의 독립 보존
- 자동화 토큰의 resolution 거부
- 조건별 검색과 여러 주에 걸친 항목 이력
- 주간 마감 준비 버전 충돌 거부, 진행·보류 항목 이월과 correction 허용
- 명시적인 Corpus 후보와 promotion receipt 중복 방지
- 기간별 주요 결과·장기 이월과 사용자 편집 요약 버전 보존
- HTTP, MCP, CORS, Host와 OAuth Service Binding 경계

## 운영 배포

`wrangler.example.jsonc`를 ignored `wrangler.jsonc`로 복사해 D1 ID, 고정 Journal resource URI, 인증 issuer와 Site origin을 설정합니다. 다음 값은 Worker secret으로만 넣습니다.

- `JOURNAL_SITE_TOKEN`: 소유자 전용 Site의 server-side proxy
- `JOURNAL_INGEST_TOKEN`: 읽기와 monitoring ingest만 허용하는 자동화 자격 증명

`JOURNAL_SITE_TOKEN`은 브라우저 JavaScript에 노출하지 않습니다. 일반 Chat과 MCP client는 Personal Agent Auth의 OAuth를 사용합니다.

```sh
npx wrangler d1 migrations apply personal-agent-journal --remote
npx wrangler deploy
```

## 백업과 복구

운영 백업은 소유자만 읽을 수 있는 절대 경로에 내보냅니다.

```sh
./scripts/backup.sh /absolute/private/path/journal-YYYY-MM-DD.sql
```

복구는 먼저 별도 local state나 새 D1에서 확인합니다. `restore.sh`의 기본값은 local입니다.

```sh
./scripts/restore.sh /absolute/private/path/journal-YYYY-MM-DD.sql DB --local
```

격리한 local state에서 검증하려면 `JOURNAL_D1_PERSIST_TO=/absolute/test/state`를 함께 지정합니다.

원격 복구는 명시적인 database 이름과 `JOURNAL_RESTORE_CONFIRM=restore:DATABASE`가 모두 있을 때만 실행됩니다. 운영 database에 직접 덮어쓰기보다 새 D1에 복구하고 행 수·주차·이벤트·receipt를 확인한 뒤 binding을 전환합니다.

## 데이터 경계

Journal은 원문 이메일이나 문서 bytes를 저장하지 않습니다. 항목 title, 짧은 summary, source
reference, lane, resolution, append-only event, week closure와 Corpus promotion receipt만
보관합니다. Corpus receipt의 `sourcePath`는 프로젝트 root 기준 상대 경로나 비경로 표식만
허용하며 로컬 절대 경로를 저장하지 않습니다. 닫힌 주는 correction event 외에는 수정하지
않습니다.

보존, 삭제, 장애 대응과 배포 전후 확인은 [OPERATIONS.md](./OPERATIONS.md)를 따릅니다.
