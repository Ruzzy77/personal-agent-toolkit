# Design

Design은 제품 화면을 만들고 고치며, 결과를 검토하고 사용자 조사를 설계·종합하는
Personal Agent Toolkit 플러그인입니다. 특정 디자인 제품이나 한 공급자의 작업 방식에
맞추지 않고, 사용자의 목적과 현재 프로젝트의 콘텐츠·디자인 시스템·구현 상태를 기준으로
판단합니다.

## Skills

| Skill | 역할 |
| --- | --- |
| `design` | 화면 설계·구현·개편과 디자인 시스템, UX 문구, 개발 인계 |
| `design-review` | 사용성·시각 완성도 검토와 WCAG 2.2 AA 접근성 감사 |
| `design-research` | 사용자 조사 설계와 수집된 자료의 종합 |

디자인 시스템, UX 문구와 개발 인계는 독립된 결과가 필요할 수 있지만 화면 설계와 분리된
작업 체계로 취급하지 않습니다. 접근성은 일반 설계의 품질 조건으로 적용하고, 명시적인
감사에서는 확인 범위와 성공 기준을 따로 기록합니다. 조사 설계와 종합은 같은 제품 결정을
잇는 두 방식으로 한 Skill에서 구분합니다.

`design` Skill은 소유자 전용 디자인 라이브러리에서 패턴, 선택적 레시피와 예시 자산을 필요한
만큼만 읽을 수 있습니다. 개인 자산은 plugin이나 공개 저장소에 포함되지 않으며, 프로젝트의
브랜드·토큰·컴포넌트보다 우선하지 않습니다.

시각적으로 후보를 찾고 비교하거나 요청문을 준비할 때에는 소유자 전용
[Design Reference Library Site](https://personal-material-index.ruzzy.chatgpt.site)를 사용할 수
있습니다. Site와 MCP는 `services/design`이 소유하는 같은 비공개 D1·R2 자료를 읽습니다.

## 작업 원칙

- 화면의 종류, 주요 사용자와 과업, 기존 브랜드 자산, 콘텐츠와 제약을 먼저 확인합니다.
- 프로젝트의 기술, 토큰과 컴포넌트를 우선하고 시각적 이유만으로 새 체계로 교체하지 않습니다.
- 배치의 변주, 움직임과 정보 밀도는 고정된 점수나 유행별 처방이 아니라 현재 맥락에서 함께 조절합니다.
- 화면에 나타난 사실, 구현에서 확인한 값과 해석을 구분합니다.
- 설계나 개편은 실제 화면을 확인하고, 검토하지 않은 상태를 완료로 보고하지 않습니다.

이 플러그인은 특정 디자인 제품의 명령·캔버스·동기화 기능을 제공하거나 모방하지 않습니다.
[Anthropic `frontend-design`](https://github.com/anthropics/claude-plugins-official/tree/main/plugins/frontend-design)과
[Leonxlnx `taste-skill`](https://github.com/leonxlnx/taste-skill)은 비교 자료로 검토했으며,
시각적 판단을 구체화하는 취지는 반영하되 특정 기술 스택, 숫자 프리셋과 일률적인 금지
목록은 가져오지 않았습니다. 접근성 감사의 기본 기준은
[WCAG 2.2](https://www.w3.org/TR/WCAG22/)입니다.

현재 비교 기준은 Anthropic `frontend-design`의
[`44490cc`](https://github.com/anthropics/claude-plugins-official/commit/44490cccaf6d9f82fdeec9416fbf7c9bd72575dc)와
`taste-skill`의
[`ccbc156`](https://github.com/leonxlnx/taste-skill/commit/ccbc15639c97057cbfcf32ecebc38ef716e4bb37)입니다.
이후 변경도 그대로 병합하지 않고 Design의 현재 역할에 필요한 판단 원칙만 다시 검토합니다.

## 데이터와 연결

Claude에서는 Design plugin이 소유자 인증 원격 MCP를 연결합니다. ChatGPT와 Codex에서는 별도
Design 설치 항목을 만들지 않고 **Personal Agent Toolkit** 하나에 Design Skill과 도구가 함께
나타납니다. 레시피와 메타데이터는 D1, 템플릿·CSS·이미지는 비공개 R2에 저장됩니다. Site의
화면 도구는 탐색·비교·요청 준비만 수행하며, 작업 파일 권한은 현재 실행 환경과 프로젝트의
규칙을 따릅니다.

구성 요소와 데이터 정본은 [DESIGN.md](./DESIGN.md)에 있습니다.

## License

[Apache License 2.0](./LICENSE)
