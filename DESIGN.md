# Personal Agent Toolkit 설계

이 문서는 제품 사이의 실행 경계와 통일 방향을 정하는 저장소 정본이다. 현재 제품 목록, 공개
MCP 표면과 release version은 [`products.json`](./products.json)에 둔다. 제품별 의미와 동작은
각 `plugins/<product>/DESIGN.md`가 맡는다.

## 설계 목표

- 같은 종류의 제품은 같은 흐름으로 배포하고 운영한다.
- 상태가 있는 원격 제품은 서비스가 데이터와 업무 규칙을 소유한다.
- Site와 MCP는 서로를 중계하지 않고 같은 제품 서비스를 사용한다.
- plugin은 클라이언트 연결, Skill과 정적 자산을 배포하며 운영 데이터를 소유하지 않는다.
- 공통 코드는 실제로 반복되는 인증·계약·오류 처리부터 합치고, 제품 차이를 감추는 추상화는
  만들지 않는다.

## 현재 제품 지도와 목표

| 제품 | 현재 구성 | 현재 데이터 정본 | 통일 목표 |
| --- | --- | --- | --- |
| Sense | remote plugin + shared context service + local engine | context service | 공통 remote runtime을 쓰는 독립 제품 모듈 |
| Corpus | remote plugin + context service + Sync + local engine | context service와 연결된 로컬 파일 | 분리된 원격 모듈과 local engine 유지 |
| Hypes | remote plugin + shared context service + local engine | context service | 공통 remote runtime을 쓰는 독립 제품 모듈 |
| Document Files | local plugin | 호출자가 소유한 로컬 파일 | 로컬 문서 처리 예외 유지 |
| Design | skill-only plugin + Site | `sites/design/designs` | 정적 자산 제품 예외 유지 |
| Journal | remote plugin + service + Site | Journal service D1 | 기준 구조 유지 |
| Library | remote plugin + service + Site | 운영 Site D1·R2(현재), Library service D1·R2(전환 후) | 기준 구조로 전환 중 |

`services/remote-context`가 Sense·Corpus·Hypes를 함께 호스팅하는 것은 배포 단위의 선택이다. 각
제품의 계약, 저장 접근과 업무 로직은 모듈 경계를 유지하며, 한 제품의 변경이 다른 제품의 공개
표면을 암묵적으로 바꾸지 않게 한다.

## 기준 구조

상태가 있는 원격 제품은 다음 흐름을 기준으로 한다.

```text
plugin ── MCP ──┐
                ├── product service ── D1 / R2
Site ── HTTP ───┘
```

- **plugin**: manifest, Skill, 아이콘과 필요한 연결 정보만 배포한다.
- **service**: 인증, 입력 검증, 업무 규칙, 동시성 제어와 저장을 소유한다.
- **Site**: 읽기·편집 UI와 화면 안 WebMCP를 제공하고 서비스 API를 사용한다.
- **공통 runtime**: [`packages/remote-runtime`](./packages/remote-runtime)이 owner 인증 계약,
  bearer 처리와 MCP 성공·오류 응답처럼 둘 이상의 제품에서 같은 의미로 반복되는 코드만
  제공한다.
- **공통 contract**: Zod schema에서 입력 타입을 추론하고 MCP와 HTTP가 같은 업무 입력을
  사용한다. 호환 기간에는 기존 응답과 URL을 adapter로 유지한다.

Design은 사용자 상태가 없는 정적 자료 제품이므로 서비스를 만들지 않는다. Document Files는
운영 문서를 업로드하는 원격 저장소가 아니라 호출자가 허용한 로컬 문서를 처리하므로 local MCP를
유지한다. Sense·Corpus·Hypes의 로컬 Python 구현은 `engines/`에 두고 원격 plugin 배포와 분리한다.

## 데이터와 변경

서비스가 있는 제품의 D1·R2 binding은 서비스에 둔다. Site에는 제품 상태 저장 binding을 두지
않고, 인증된 서비스 요청만 보낸다. 쓰기 API는 현재 version을 대조해 충돌을 명시적으로 반환하며
마지막 요청이 앞선 편집을 조용히 덮어쓰지 않게 한다.

D1 schema는 versioned migration 파일로만 변경한다. 요청을 처리하면서 `CREATE`나 `ALTER`를
실행하지 않는다. 운영 이전은 기존 데이터 export, 새 저장층 import, 레코드·asset 대조, 전환과
rollback 가능 기간까지만 다룬다. 별도 영구 이력 저장소를 만들지 않고 Git의 migration 기록과
D1의 복구 기능을 사용한다.

Library의 다음 release 소스는 service가 D1·R2와 version 충돌 검사를 소유하고 Site와 MCP가 같은
service를 사용하는 기준 구조로 바뀌었다. 운영 전환 전 정본은 기존 Site 저장층이다. 전환에서는
기존 Site의 발간호와 asset을 원문 그대로 복사·대조한 뒤 service와 새 Site를 차례로 배포한다.
기존 MCP URL과 발간호 URL은 유지한다. 기존 Site 저장층은 전환 확인 기간에만 rollback 대상으로
남기고 새 쓰기를 받지 않는다.

## 인증과 권한

원격 제품은 공통 소유자 인증에서 principal을 만들고 제품별 scope로 읽기와 쓰기를 제한한다.
Site는 확인된 사용자 ID와 이메일을 모두 요구한다. 내부 Site-to-service token은 사용자 OAuth와
구분하고 저장소에 넣지 않는다. 서비스 오류는 안정적인 code와 안전한 message만 공개하며 내부
예외와 자격 증명을 응답에 포함하지 않는다.

## 버전과 배포

plugin base version은 `products.json`, Claude manifest와 Codex manifest에 같이 반영한다. 로컬
MCP plugin은 해당 Python package도 맞추고, 한 제품으로 함께 배포되는 service와 Site package도
같은 version을 사용한다. `engines/`의 개발·이관 package는 공개 plugin과 독립적으로 바뀐다.
공유 context service는 자체 host version과 Sense·Corpus·Hypes의 공개 surface version을 분리한다.

base version을 바꾼 변경은 소스와 원격 배포를 반영하고, 지원 클라이언트의 새 세션에서 현재 Skill,
도구 이름과 입력 구조가 보이는지 확인해야 완료다. 문구 정리처럼 공개 동작을 바꾸지 않는 변경은
불필요하게 version을 올리지 않는다.

## 검증 원칙

저장소 검사는 `products.json`과 manifest·package version·실제 공개 도구가 어긋나는 경우처럼
사용자에게 다른 제품이 노출될 수 있는 드리프트만 막는다. 업무 규칙, 권한, migration과 손실 위험이
있는 경계는 테스트하되, 구현 상수를 그대로 되읽거나 디렉터리 존재를 여러 층에서 반복 확인하는
회귀 테스트는 만들지 않는다. 중간 조사 기록과 생성된 보고서는 저장소에 누적하지 않는다.

## 개선 순서

1. 제품 registry와 이 설계 문서를 기준으로 현재 구조를 명시한다.
2. 원격 서비스의 반복된 인증·MCP·오류 처리와 schema contract를 작은 공통 package로 합친다.
3. Library 저장 정본을 service로 안전하게 이전한다.
4. Library Site를 Journal·Design과 같은 TypeScript·Vinext 기반으로 옮기고 공통 Site 연결을 쓴다.
5. 큰 모듈은 위 경계가 확인된 부분부터 나누고, 배포와 실제 클라이언트 노출을 확인한다.
