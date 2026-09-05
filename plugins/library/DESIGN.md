# Library 설계

## 목적과 경계

Library는 Daily·Digest·Research 발간호와 표지·삽화를 읽고 편집하고 발행한다. MCP와 Site가 같은
발간호를 다루며, 저장 뒤 다시 읽었을 때 HTML, 참고자료, asset과 발행 정보가 일치해야 한다.

## 소스 구성

- `plugins/library`: 편집·발행 Skill과 클라이언트별 원격 연결
- `services/library`: owner 인증, MCP, HTTP API, D1 문서와 R2 asset 정본
- `sites/library`: 읽기·직접 편집 UI와 service client

Site와 MCP는 서로를 중계하지 않고 같은 service의 업무 규칙과 저장층을 사용한다. 내부
Site-to-service token은 사용자 OAuth와 별도로 관리한다.

## 운영 구조

발간호와 이미지의 운영 정본은 Library service의 D1·R2다. 운영 Site는 `LIBRARY_SERVICE_URL`과
비밀 `LIBRARY_SITE_TOKEN`으로 service를 호출하며 자체 D1·R2 binding을 두지 않는다. 개별
Library MCP와 통합 MCP는 같은 `LibraryService` 구현과 저장층을 사용한다.

Site는 Vite·React·Vinext와 공통 인증·service client를 사용한다. 소유자의 직접 입력은 자동
저장하고, WebMCP 수정안은 화면에서 검토한 뒤 별도로 저장한다. 원격 MCP 수정은 온라인 정본에
바로 저장한다. 이 읽기·편집 경험은 유지한다.

2026-09-05 운영 Site의 서비스 연결 설정과 데이터 binding 부재, 원격 MCP의 발간호 읽기를
확인했다. 과거 전체 데이터 이관의 대조 결과와 rollback 보관 종료 여부는 이번 조사에서
재확인하지 않았다.

## 저장 변경과 이전

service schema는 migration 파일로만 변경하고 요청 중 runtime DDL을 실행하지 않는다.
저장층을 이전할 때에는 기존 데이터를 export해 새 저장층으로 import한 뒤 발간호 수, 식별자,
본문 hash와 asset을 대조한다. 전환 중 편집 충돌을 피하고, 확인 기간에는 기존 저장층을 읽기
전용 rollback 대상으로 유지한다. 영구적인 이중 쓰기나 별도 migration 이력 서비스는 만들지 않는다.

발간호 수정에는 읽을 때 받은 `version`을 요구하고, 저장 쿼리에서도 현재 version을 대조해
충돌을 반환한다. 이를 통해 두 편집 경로가 마지막 요청 순서만으로 서로를 덮지 않게 한다.

## 공개 표면과 권한

도구 목록, endpoint와 release version은 루트 [`products.json`](../../products.json)이 정본이다.
기존 MCP URL과 공개 발간호 URL은 지원 클라이언트 전환이 끝날 때까지 유지한다. owner OAuth는
`library.read`와 `library.write`를 구분하고, Site 내부 token은 사용자 OAuth를 대신하지 않는다.

## 버전과 검증

plugin, service와 Site는 같은 제품 release version을 사용한다. 핵심 검증은 권한, version 충돌,
migration 전후 데이터 동일성과 대표 발간호의 읽기·편집 흐름이다. 구현 상수를 그대로 반복하는
snapshot이나 모든 발간호의 중복 보관은 추가하지 않는다.

## 첫 조사와 다음 구현 제안

다음 첫 구현은 편집 충돌 이후의 초안 복구와 이미지 경로 보호로 좁힌다. 아래는 2026-09-05
조사에서 확인한 문제와 보완 제안이며 보완안은 아직 구현하지 않았다.

- **미저장 초안 보존:** [Site 편집기](../../sites/library/public/library-editor.js)는 version 충돌 때
  새로고침을 안내하지만, 다시 열린 서버 본문이 초안의 기준 본문과 다르면 보관한 초안을 지운다.
  이 삭제 분기는 원본 함수를 메모리에서 실행해 확인했으며 운영 문서를 수정한 검증은 아니다.
  서버의 충돌 차단은 유지하되, 사용자가 새 서버 내용과 자신의 수정을 확인하고 복원·복사하거나
  명시적으로 버리기 전에는 초안을 삭제하지 않는 흐름을 마련한다.
- **이미지 경로 보호:** [이미지 저장](../../services/library/src/service.ts)은 같은 R2 경로에
  조건 없이 덮어쓰며 발간호 version과 연결되지 않는다. 따라서 문서 저장이 충돌로 거절돼도
  기존 호의 이미지는 먼저 바뀔 수 있다. 실제 사고가 확인된 것은 아니다. 기존 경로의 충돌을
  검사하고, 교체 이미지는 새 경로에 준비한 뒤 발간호 version을 확인하며 참조를 바꾸는 방식을
  검토한다. 공개 업로드 계약과 영향받는 클라이언트의 반영은 공통 운영·배포 범위와 맞춘다.

완료 기준은 먼저 저장된 발간호와 사용자의 미저장 수정이 함께 보존되고, 실패한 문서 수정 때문에
현재 발간호의 이미지가 바뀌지 않는 것이다. 기존 검사와 일회성 확인을 우선하며 새 영구 이력
저장소나 자산 보존·삭제 정책 변경은 포함하지 않는다.

목록은 최신 200개까지이며 WebMCP 검색은 현재 목록의 제목·날짜·식별자·분류만 다룬다.
Hype·Star·Tag와 형광펜은 브라우저에만 저장된다. 기존 글을 찾고 후속 작업에 활용하는 실제
사례에서 탐색 부담을 확인한 뒤 별도로 개선을 결정한다. 검색 확대, 표시·태그의 영구 저장과
발행 일정·편집 정책 변경은 이번 첫 구현에 섞지 않는다.
