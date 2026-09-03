# Design 설계

## 목적과 경계

Design은 제품 화면 설계·구현, 디자인 검토와 사용자 조사에 쓰는 Skill과 소유자의 개인 디자인
자산·템플릿 라이브러리를 함께 제공한다. 개인 자산은 공개 배포하지 않으며, 특정 디자인 도구를
대신하거나 사용자 프로젝트의 디자인 시스템보다 자체 취향을 우선하지 않는다.

## 구성과 데이터 정본

- `plugins/design`: Skill과 Claude용 원격 MCP 연결
- `services/design`: D1 메타데이터, R2 파일과 소유자 인증 MCP를 소유하는 정본
- `sites/design`: 같은 서비스를 읽는 소유자 전용 탐색·비교 화면
- `plugins/personal-agent-toolkit`: ChatGPT와 Codex에 Design Skill과 도구를 포함하는 통합 배포본

레시피, 패턴, 버전과 파일 메타데이터는 D1에 저장하고 템플릿·CSS·이미지 바이트는 비공개 R2에
저장한다. Site와 MCP는 같은 서비스만 사용한다. plugin과 공개 저장소에는 개인 자산 사본을 넣지
않는다.

## 공개 표면과 권한

`design.read`는 레시피와 자산 읽기, `design.write`는 레시피·파일 생성과 갱신에 사용한다. 쓰기는
revision을 확인하며 모든 원격 요청은 공통 소유자 OAuth 또는 Site 전용 비밀 토큰으로 제한한다.
Site의 화면 도구는 현재 화면의 탐색·비교·요청 준비만 수행한다. 작업 파일 권한은 Design이 아니라
현재 실행 환경과 대상 프로젝트가 정한다.

## 버전과 검증

plugin, service와 Site는 같은 제품 release version을 사용한다. 저장 계약과 대표적인 읽기·쓰기
흐름, 모든 MCP 도구의 output schema, Site build를 확인한다. 시각적 판단이 필요한 화면 변경은
실제 렌더링을 확인하되 자산마다 의미 없는 snapshot을 추가하지 않는다.
