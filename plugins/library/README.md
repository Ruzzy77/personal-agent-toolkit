# Library

Library는 Daily·Digest·Research 발간호를 읽고 고치고 발행하는 소유자 인증형 개인
라이브러리입니다. 원격 MCP와 Library 사이트는 Library service의 D1 문서와 R2 표지·삽화를
함께 사용합니다.

- 소유자 전용 Site: https://personal-edition-library.ruzzy.chatgpt.site
- 원격 MCP: https://personal-library-mcp.hiyaq77.workers.dev/api/mcp
- 문서 정본: Library service D1
- 표지와 삽화: Library service R2

## 도구

- library_whoami: 현재 소유자 인증과 허용 권한 확인
- library_list_issues: 컬렉션별 최근 발간호 조회
- library_read_issue: 본문 텍스트 또는 편집용 전체 HTML 읽기
- library_update_issue: 전체 HTML과 선택한 표지·공개 참고자료 저장
- library_create_issue: 예약 시각을 포함한 새 발간호 등록
- library_upload_asset: 새 표지와 삽화 업로드

읽기에는 library.read, 저장·발행·업로드에는 library.write 권한이 필요합니다. 저장한
뒤에는 같은 발간호를 다시 읽어 온라인 정본의 HTML, 참고자료, 표지와 발행 정보를 확인합니다.

본문과 시각물의 편집·발행 방법은 manage-library Skill이 Corpus의 library-editorial
Context Skill을 현재 작업에 연결합니다. Library 사이트에서는 소유자가 읽기 화면을 직접
고칠 수 있고, ChatGPT나 Codex가 WebMCP로 화면에 반영한 수정안은 사용자가 검토한 뒤에만
저장됩니다.

저장 구조와 운영 전환 경계는 [DESIGN.md](./DESIGN.md)에 있습니다.

## 라이선스

[Apache License 2.0](LICENSE). [NOTICE](NOTICE)를 함께 따릅니다.
