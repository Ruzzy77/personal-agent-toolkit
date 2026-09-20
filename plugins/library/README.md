# Library

Library는 Daily·Digest·Research 발간호를 읽고 고치고 발행하는 소유자 인증형 개인
라이브러리입니다. 원격 MCP는 Library service의 D1 문서와 R2 표지·삽화를 사용합니다.

- Library 발간호 읽기·편집은 Toolkit의 `manage-library` Skill과 원격 MCP를 사용합니다.
- 원격 MCP: https://personal-library-mcp.hiyaq77.workers.dev/api/mcp
- 문서 정본: Library service D1
- 표지와 삽화: Library service R2

OpenAI에서는 Library Skill을 `Personal Agent Toolkit` 통합 plugin에 포함하고, Claude의 Library
plugin은 원격 MCP endpoint를 직접 선언합니다.

## 도구

- library_whoami: 현재 소유자 인증과 허용 권한 확인
- library_list_issues: 컬렉션별 최근 발간호 조회
