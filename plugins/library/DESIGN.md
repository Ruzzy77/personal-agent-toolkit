# Library 설계

## 목적과 경계

Library는 Daily·Digest·Research 발간호와 표지·삽화를 읽고 편집하고 발행한다. MCP와 Site가 같은
발간호를 다루며, 저장 뒤 다시 읽었을 때 HTML, 참고자료, asset과 발행 정보가 일치해야 한다.

## 소스 구성

- `plugins/library`: 원격 MCP 연결과 편집·발행 Skill
- `services/library`: owner 인증, MCP, HTTP API, D1 문서와 R2 asset 정본
- `sites/library`: 읽기·직접 편집 UI와 service client

Site와 MCP는 서로를 중계하지 않고 같은 service의 업무 규칙과 저장층을 사용한다. 내부
Site-to-service token은 사용자 OAuth와 별도로 관리한다.

## 운영 전환 상태

운영 Site의 기존 D1·R2 자료는 service 저장층으로 복사·대조한 뒤 전환한다. 전환이 끝나기 전까지
운영 데이터 정본은 기존 Site에 남아 있으므로 새 service와 Site를 따로 먼저 공개하지 않는다.
새 Site는 Journal·Design과 같은 TypeScript·Vinext 기반과 공통 인증·service client를 쓰면서
현재 읽기 흐름과 직접 편집 경험을 보존한다.

## 데이터 이전

새 service schema는 migration 파일로 만들고 요청 중 runtime DDL을 실행하지 않는다. 기존 Site
데이터를 export해 service 저장층으로 import한 뒤 발간호 수, 식별자, 본문 hash와 asset을 대조한다.
전환 중 편집 충돌을 피하고, 확인 기간에는 기존 저장층을 읽기 전용 rollback 대상으로 유지한다.
확인이 끝난 뒤에만 Site binding과 중계 코드를 제거한다. 영구적인 이중 쓰기나 별도 migration
이력 서비스를 만들지 않는다.

쓰기에는 현재 `version`을 요구하고 달라졌으면 충돌을 반환한다. 이를 통해 두 편집 경로가 마지막
요청 순서만으로 서로를 덮지 않게 한다.

## 공개 표면과 권한

도구 목록, endpoint와 release version은 루트 [`products.json`](../../products.json)이 정본이다.
기존 MCP URL과 공개 발간호 URL은 지원 클라이언트 전환이 끝날 때까지 유지한다. owner OAuth는
`library.read`와 `library.write`를 구분하고, Site 내부 token은 사용자 OAuth를 대신하지 않는다.

## 버전과 검증

plugin, service와 Site는 같은 제품 release version을 사용한다. 핵심 검증은 권한, version 충돌,
migration 전후 데이터 동일성과 대표 발간호의 읽기·편집 흐름이다. 구현 상수를 그대로 반복하는
snapshot이나 모든 발간호의 중복 보관은 추가하지 않는다.
