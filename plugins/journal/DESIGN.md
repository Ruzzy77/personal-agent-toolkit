# Journal 설계

## 목적과 경계

Journal은 현재 주의 간결한 진행 상태, 사용자가 확정한 해결 상태와 마감된 기간 기록을 관리한다.
원문 이메일·문서를 복제하지 않고 필요한 요약과 source reference만 저장한다. 프로젝트의 오래가는
사실과 결정은 Journal에 쌓지 않고 확인된 결과만 해당 Corpus Context로 반영한다.

## 구성과 실행

- `plugins/journal`: Skill, 클라이언트별 원격 연결과 제한된 로컬 모니터 launcher
- `services/journal`: 인증, MCP·HTTP API, 업무 규칙과 D1 저장
- `sites/journal`: 같은 service API를 사용하는 소유자 전용 UI

service가 유일한 데이터 권위다. Site는 자체 D1·R2를 갖지 않는다. launcher는 원격 MCP를 사용할
수 없는 모니터의 읽기·ingest 보조 수단이며 별도 정본을 만들지 않는다.

## 데이터와 변경

Journal D1은 현재 항목, append-only event, 확정된 주간 마감과 정정을 보관한다. 자동 ingest는
관찰된 진행을 갱신할 수 있지만 완료·보류·취소·재개를 추론하지 않는다. 마감 뒤 변경은 기존 기록을
덮지 않고 correction으로 남긴다.

## 공개 표면과 권한

MCP와 HTTP는 같은 service 함수를 호출한다. 공개 도구 목록과 version은 루트
[`products.json`](../../products.json)이 정본이다. owner OAuth scope는 읽기, ingest와 쓰기를
구분하고 Site-to-service token 및 모니터 token은 서로 다른 실행 경계에서 사용한다.

## 버전과 검증

plugin, service와 Site는 같은 제품 release version을 사용한다. 상태 전이, 주간 마감, 권한과 D1
migration을 집중 검증하고 단순 화면 문구나 상수 복제에는 회귀 테스트를 늘리지 않는다. 배포 완료는
service와 Site가 시작되고 새 클라이언트 세션에서 현재 MCP 표면이 확인된 상태다.
