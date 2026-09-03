# Journal Site

Journal의 주간 보드와 기간 기록을 읽고 처리하는 소유자 전용 화면입니다. 화면은
`services/journal`의 API를 사용하며, Journal의 D1 데이터 정본을 따로 복제하지 않습니다.

## 인증과 서비스 경계

모든 페이지와 `/api/journal/*` handler는 ChatGPT 사용자 인증을 서버에서 확인합니다. 서버는
`JOURNAL_SERVICE_URL`과 비공개 `JOURNAL_SITE_TOKEN`으로 Journal Worker에 요청합니다. 운영 토큰은
Sites의 비공개 서버 환경에만 설정하고 저장소나 브라우저 JavaScript에는 기록하지 않습니다.

WebMCP는 현재 화면의 보드 읽기, 항목 검색·추가·처리와 기간 조회만 제공합니다. 원격 Journal
MCP는 페이지를 열지 않은 작업, 자동화 ingest, 주간 마감과 Corpus 반영 기록을 맡습니다.

## 로컬 확인

```sh
npm ci
npm test
npm run build
```

사이트를 배포한 뒤에는 로그인하지 않은 페이지와 API 요청이 거부되는지, 로그인한 소유자가 같은
주간 ID를 Site와 원격 MCP에서 읽는지 확인합니다.
