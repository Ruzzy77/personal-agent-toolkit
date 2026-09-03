# Personal Agent Toolkit 설계

이 문서는 제품 사이의 실행 경계와 통일 방향을 정하는 저장소 정본이다. 현재 제품 목록, 공개
MCP 표면과 release version은 [`products.json`](./products.json)에 둔다. 제품별 의미와 동작은
각 `plugins/<product>/DESIGN.md`가 맡는다.

## 설계 목표

- 같은 종류의 제품은 같은 흐름으로 배포하고 운영한다.
- 상태가 있는 원격 제품은 서비스가 데이터와 업무 규칙을 소유한다.
- Site와 MCP는 서로를 중계하지 않고 같은 제품 서비스를 사용한다.
- plugin은 연결, Skill과 정적 UI 자산만 배포하며 운영 데이터나 개인 자산을 소유하지 않는다.
- OpenAI에서는 여섯 원격 제품을 등록 app과 설치 항목 하나로 묶고, Claude에서는 제품별 MCP
  endpoint를 직접 연결한다.
- 공통 코드는 실제로 반복되는 인증·계약·오류 처리부터 합치고, 제품 차이를 감추는 추상화는
  만들지 않는다.

## 제품 지도

| 제품 | 구성 | 데이터 정본 |
| --- | --- | --- |
| Sense | product plugin + shared context service + local engine | context service |
| Corpus | product plugin + context service + Sync + local engine | context service와 연결된 로컬 파일 |
| Hypes | product plugin + shared context service + local engine | context service |
| Journal | product plugin + service + Site | Journal service D1 |
| Library | product plugin + service + Site | Library service D1·R2 |
| Design | product plugin + service + Site | Design service D1·R2 |
| Document Files | local plugin + remote analyzer | 호출자가 소유한 문서 바이트; analyzer는 비저장 |

`services/remote-context`가 Sense·Corpus·Hypes를 함께 호스팅하고 OpenAI용 여섯 제품 도구를 한
표면에 등록하는 것은 배포 단위의 선택이다. 각 제품의 계약, 저장 접근과 업무 로직은 모듈 경계를
유지하며 한 제품의 변경이 다른 제품의 공개 표면을 암묵적으로 바꾸지 않게 한다.

## 기준 구조

상태가 있는 원격 제품은 다음 흐름을 기준으로 한다.

```text
OpenAI 통합 plugin ── registered app ── 통합 MCP ── product module ──┐
Claude 제품 plugin ─────────────────── 제품 MCP ── product module ──┼── D1 / R2
Site ───────────────────────────────────── HTTP ── product service ──┘
```

- **plugin**: manifest, Skill, 아이콘과 연결 정보만 배포한다. OpenAI에서는
  `plugins/personal-agent-toolkit`이 Sense, Corpus, Hypes, Journal, Library와 Design의 Skill과 통합
  등록 app을 하나의 설치 단위로 제공한다. `Personal Agent Toolkit`은 설치 항목의 이름이며 제품,
  Skill과 도구는 기존의 짧은 이름을 유지한다. Claude에서는 제품별 plugin을 사용한다.
- **service**: 인증, 입력 검증, 업무 규칙, 동시성 제어와 저장을 소유한다.
- **Site**: 소유자 전용 읽기·편집 UI를 제공하고 제품 service API를 사용한다. Site 자체에 제품
  데이터 binding을 두지 않는다.
- **공통 runtime**: [`packages/remote-runtime`](./packages/remote-runtime)이 owner 인증 계약,
  bearer 처리와 MCP 성공·오류 응답처럼 둘 이상의 제품에서 같은 의미로 반복되는 코드만 제공한다.
- **공통 contract**: Zod schema에서 입력 타입을 추론하고 MCP와 HTTP가 같은 업무 입력을 사용한다.
- **MCP 출력 contract**: 모든 공개 도구는 객체 루트 `outputSchema`를 제공하고 이에 맞는
  `structuredContent`와 JSON text fallback을 함께 반환한다. schema는 클라이언트가 사용할 주요
  필드만 고정하고 제품 내부 응답 전체를 불필요하게 폐쇄하지 않는다.

Design은 Library와 같은 서비스 소유 구조를 쓴다. 개인 레시피와 템플릿 메타데이터는 D1, 파일은
R2에 두고 Site와 MCP가 같은 `services/design`을 사용한다. 공개 저장소, plugin 묶음과 Site source에
개인 자산 사본을 넣지 않는다.

Document Files의 파일 조작 표면은 로컬 plugin으로 유지하되 분석 계약은 실행 위치와 분리한다.
`AnalysisJob v1`과 byte stream을 local backend 또는 `services/document-analyzer`에 전달하고 같은
`AnalysisResult v1`을 받는다. Sync가 Connection의 `local`, `remote`, `approval_required` 정책과
전송 한도를 적용한다. 원격 analyzer는 승인된 임시 바이트를 메모리에서 처리할 뿐 원문이나 결과를
저장하지 않는다. 네이티브 앱 렌더링·편집과 원격에서 보존하지 못하는 세부 구조는 로컬 backend가
계속 맡는다.

## 데이터와 변경

서비스가 있는 제품의 D1·R2 binding은 서비스에 둔다. Site에는 제품 상태 저장 binding을 두지
않고 인증된 service 요청만 보낸다. 쓰기 API는 현재 version을 대조해 충돌을 명시적으로 반환하며
마지막 요청이 앞선 편집을 조용히 덮어쓰지 않게 한다.

D1 schema는 versioned migration 파일로만 변경한다. 요청을 처리하면서 `CREATE`나 `ALTER`를
실행하지 않는다. 운영 이전은 기존 데이터 export, 새 저장층 import, 레코드·asset 대조, 전환과
rollback 가능 기간까지만 다룬다. 별도 영구 이력 저장소를 만들지 않고 Git의 migration 기록과
D1의 복구 기능을 사용한다.

Library와 Design은 service가 D1·R2와 version 충돌 검사를 소유하고 Site와 MCP가 같은 service를
사용한다. Site용 내부 token은 사용자 OAuth와 분리한다. 개인 Design 자산의 현재 정본은
`services/design`의 운영 D1·R2이며 저장소의 예전 복사본은 현재 tree와 배포 묶음에서 제거한다.
기존 공개 Git 이력의 정리는 별도의 명시적 결정으로 다룬다.

## 인증과 권한

원격 제품은 공통 소유자 인증에서 principal을 만들고 제품별 scope로 읽기와 쓰기를 제한한다.
Site는 확인된 사용자 ID와 이메일을 모두 요구한다. 내부 Site-to-service token은 사용자 OAuth와
구분하고 저장소에 넣지 않는다. 서비스 오류는 안정적인 code와 안전한 message만 공개하며 내부
예외와 자격 증명을 응답에 포함하지 않는다.

통합 toolkit 리소스의 기존 설치 grant는 제품 추가만으로 끊지 않는다. 새 grant는 Design scope를
명시적으로 사용하고, Design 추가 전에 모든 toolkit 읽기 또는 쓰기 scope를 받은 기존 소유자
grant는 `/mcp` 안에서만 같은 수준의 Design 권한으로 이어받는다. 제품별 Design 리소스에는 이
호환 규칙을 적용하지 않는다.

Sync 장치 인증은 제품 MCP OAuth와 분리한다. 원격 문서 분석은 Corpus Sync endpoint가 현재 장치와
Connection 정책을 확인한 뒤 비공개 Service Binding으로만 전달한다. analyzer는 owner·device 식별자를
접근 확인에 사용하되 문서와 함께 보관하지 않는다.

## 버전과 배포

plugin base version은 `products.json`과 client manifest에 같이 반영한다. Codex manifest의 packaging
revision만 바꾸는 경우에는 base version과 service version을 올리지 않는다. 같은 제품으로 배포하는
service와 Site package는 같은 release version을 사용한다. `engines/`의 개발·이관 package는 공개
plugin과 독립적으로 바뀐다.

OpenAI 통합 plugin의 base version은 제품별 release version과 별도로 `products.json`의 OpenAI 배포
항목에서 관리한다. 제품 Skill이나 통합 app 구성이 바뀌면 packaging revision을 갱신하며 제품 계약이
함께 바뀐 경우에만 해당 제품의 base version도 올린다.

base version을 바꾼 변경은 소스와 원격 배포를 반영하고 지원 클라이언트의 새 세션에서 현재 Skill,
도구 이름과 입력 구조가 보이는지 확인해야 완료다. 문구 정리처럼 공개 동작을 바꾸지 않는 변경은
불필요하게 version을 올리지 않는다.

OpenAI 통합 plugin은 `.app.json`에 등록 app 하나를 두고 Codex manifest가 이를 참조한다. 통합 MCP는
여섯 제품의 공개 도구를 제품 모듈에서 직접 등록하고 각 제품의 데이터 저장소와 권한을 그대로 쓴다.
직접 `mcpServers`를 함께 선언하거나 제품별 app을 별도 설치하지 않는다. Claude의 제품별 `.mcp.json`은
각 endpoint 연결을 소유한다. Document Files만 로컬 MCP plugin으로 별도 설치하며 원격 analyzer는
사용자에게 두 번째 MCP로 노출하지 않는다.

## 검증 원칙

저장소 검사는 `products.json`과 manifest·package version·실제 공개 도구가 어긋나는 경우처럼
사용자에게 다른 제품이 노출될 수 있는 드리프트만 막는다. 업무 규칙, 권한, migration, byte identity와
손실 위험이 있는 경계는 테스트하되 구현 상수를 그대로 되읽거나 디렉터리 존재를 여러 층에서
반복 확인하는 회귀 테스트는 만들지 않는다. 중간 조사 기록과 생성된 보고서는 저장소에 누적하지
않는다.

## 개선 순서

1. `products.json`과 이 문서로 제품·배포·저장 경계를 유지한다.
2. 반복되는 인증·MCP·오류 처리와 schema contract만 작은 공통 package로 합친다.
3. Site와 MCP가 제품 service 하나를 사용하도록 유지하고 개인 데이터를 plugin에서 분리한다.
4. Document Files의 local·remote backend가 같은 분석 계약을 유지하며 원격 지원 형식을 점진적으로
   넓힌다.
5. 큰 모듈은 확인된 경계부터 나누고 배포와 실제 클라이언트 노출을 확인한다.
