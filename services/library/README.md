# Personal Agent Library service

Library의 발간호를 ChatGPT, Codex와 Claude에서 읽고 편집하기 위한 소유자 전용 Cloudflare
Worker입니다. 이 서비스가 D1 문서, R2 asset과 저장 규칙을 소유하며, 원격 MCP와
`sites/library`가 같은 정본을 사용합니다.

- `library_whoami`: Google 소유자 인증과 scope 확인
- `library_list_issues`: 발간호 목록 조회
- `library_read_issue`: 본문 텍스트 또는 편집용 원본 HTML 읽기

`library.write` 권한이 있는 연결에는 다음 도구가 함께 나타납니다.

- `library_update_issue`: 발간호 원본 HTML과 선택한 표지 경로·공개 참고자료를 함께 저장. `references`를 생략하면 기존 목록을 유지하고, 빈 배열을 보내면 모두 삭제
- `library_create_issue`: `{collection}:{YYYY-MM-DD}:{HH}` 식별자로 새 발간호 추가. `HH`는 두 자리 예약 발행 시각이며 정식 주소에도 같은 시각이 포함됨
- `library_upload_asset`: 이미지 생성으로 만든 표지나 삽화 업로드

쓰기 요청에는 읽을 때 받은 `version`을 함께 보내며, 현재 version과 다르면 409
`version_conflict`를 반환합니다. 요청을 처리하면서 schema를 만들지 않고 `migrations`의 D1
migration만 적용합니다.

수정 요청은 HTML, 표지 경로와 `references`를 한 번에 반영합니다. 저장 응답 뒤에는 `library_read_issue`로 발간호를 다시 읽어 본문과 메타데이터가 온라인 정본에 남았는지 확인합니다.

`wrangler.example.jsonc`를 `wrangler.jsonc`로 복사한 뒤 인증 Worker와 같은 Cloudflare 계정에
배포합니다. `AUTH_SERVICE`는 Personal Agent Auth의 비공개 `AuthService` entrypoint를
가리킵니다. `DB`와 `MEDIA`에는 Library 전용 D1·R2를 연결하고 `LIBRARY_SITE_TOKEN`은 Worker
secret으로 설정합니다.

```sh
npm install
npm run check
npx wrangler d1 migrations apply personal-agent-library --remote
npm run deploy
```

ChatGPT 연결에 `library.write` 권한이 없으면 읽기 도구만 노출됩니다.

## 기존 Sites 저장층 이전

기존 Site를 새 저장층으로 옮길 때에는 운영 전환 직전에 다음 네 환경 변수만 현재 shell에 두고
`npm run migrate:sites`를 한 번 실행합니다. 스크립트는 별도 export나 보고서를 남기지 않고
발간호 원문과 media를 복사한 뒤 각각 SHA-256으로 다시 대조합니다. 재실행할 때 같은 발간호는
그대로 통과하고 내용이 다르면 덮어쓰지 않습니다.

- `LIBRARY_SOURCE_URL`, `LIBRARY_SOURCE_TOKEN`: 기존 Site와 SIWC 우회 token
- `LIBRARY_DESTINATION_URL`, `LIBRARY_DESTINATION_TOKEN`: 새 service와 `LIBRARY_SITE_TOKEN`

복사 없이 현재 두 저장층만 다시 대조하려면 `npm run migrate:sites -- --verify-only`를 사용합니다.
