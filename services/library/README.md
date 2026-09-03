# Personal Agent Library service

Library의 발간호를 ChatGPT, Codex와 Claude에서 읽고 편집하기 위한 소유자 전용 Cloudflare Worker입니다. `sites/library`의 문서 정본은 Sites가 관리하며, 이 Worker는 Google OAuth를 검증한 뒤 같은 온라인 원본에 요청을 전달합니다.

- `library_whoami`: Google 소유자 인증과 scope 확인
- `library_list_issues`: 발간호 목록 조회
- `library_read_issue`: 본문 텍스트 또는 편집용 원본 HTML 읽기

`library.write` 권한이 있는 연결에는 다음 도구가 함께 나타납니다.

- `library_update_issue`: 발간호 원본 HTML과 선택한 표지 경로·공개 참고자료를 함께 저장. `references`를 생략하면 기존 목록을 유지하고, 빈 배열을 보내면 모두 삭제
- `library_create_issue`: `{collection}:{YYYY-MM-DD}:{HH}` 식별자로 새 발간호 추가. `HH`는 두 자리 예약 발행 시각이며 정식 주소에도 같은 시각이 포함됨
- `library_upload_asset`: 이미지 생성으로 만든 표지나 삽화 업로드

문서와 이미지 요청은 Sites의 D1과 R2로 전달됩니다. 별도의 MCP 문서 저장소, 편집 잠금, 버전 충돌 검사와 서버 복원 기능은 두지 않습니다.

수정 요청은 HTML, 표지 경로와 `references`를 한 번에 반영합니다. 저장 응답 뒤에는 `library_read_issue`로 발간호를 다시 읽어 본문과 메타데이터가 온라인 정본에 남았는지 확인합니다.

`wrangler.example.jsonc`를 `wrangler.jsonc`로 복사한 뒤 인증 Worker와 같은 Cloudflare 계정에 배포합니다. `AUTH_SERVICE`는 Personal Agent Auth의 비공개 `AuthService` entrypoint를 가리킵니다. 배포 환경에는 Sites 우회 토큰과 두 Worker가 공유하는 bridge secret을 각각 secret으로 설정합니다.

```sh
npm install
npm run check
npm run deploy
```

ChatGPT 연결에 `library.write` 권한이 없으면 읽기 도구만 노출됩니다.
