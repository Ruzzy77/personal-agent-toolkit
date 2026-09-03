# Sense

Sense는 여러 AI가 소유자 인증을 거쳐 공유하는 비공개 작업 프로필이다. 프로필은 범용 지침을 저장하며, 각 항목에는
그 지침과 이어지는 작업 방법을 Section Skill로 둘 수 있다. 프로젝트 정보는 각 프로젝트가,
출처 기반 지식은 Corpus가, 사용자 모델은 Hypes가 관리한다.

현재 요청과 자료가 우선한다. Sense는 의사결정에 범용 지침을 제공한다. 단순 조회와 단일 작업은
현재 입력으로 수행한다.

## 도구

| 도구 | 역할 |
| --- | --- |
| `sense_read` | 색인 또는 관련 항목과 연결 Skill 조회 |
| `sense_overview` | 일반 프로필과 연결 Skill 표시 |
| `sense_revise` | 최종 항목 교체를 한 트랜잭션으로 저장함 |
| `sense_skill_revise` | 일반 Section Skill 전체를 최신 버전과 대조해 교체 |

`sense_read`의 색인은 Section Skill의 이름과 설명만 보여 준다. 관련 항목을 열면 항목 본문과
Skill 전체 지침을 함께 반환한다. Section Skill은 사용자가 반영한 `SKILL.md`이며, 자료나 사실의
출처로 사용하지 않는다.

Sense 개정은 관련 항목의 `section_sha256`와 최종 항목 전체를 한 번에 전달한다. 항목 변경
충돌은 현재 내용을 보존하며, 동일한 최종 상태는 무변경 결과를 반환한다. 개정은 현재 프로필의
원자적 교체로 이루어진다.

에이전트가 문안을 작성했거나 여러 항목을 함께 바꿀 때에는 Chat에서 최종 문안을 먼저 보여 준다.
일반 Section Skill도 현재 `version`과 전체 교체안을 사용해 Chat에서 수정할 수 있다. 민감 항목은
색인과 overview에 본문을 드러내지 않으며 원격 변경을 거부한다.

## 원격 저장과 연결

OpenAI plugin은 Skill과 등록 app을 함께 배포하고, Claude plugin은 다음 상시 원격 MCP를 직접
선언한다.

```text
https://personal-agent-context.hiyaq77.workers.dev/sense/mcp
```

Codex, Claude Code, Claude Desktop/Cowork, claude.ai와 ChatGPT는 같은 소유자 프로필과 확정
version을 사용한다. 로컬 MCP server나 공개 터널은 필요하지 않다. 민감 section 본문은 명시적인
section 조회에서만 읽으며 원격 수정 대상에는 포함하지 않는다.

## 로컬 개발·이관 자료

로컬 SQLite 구현과 최초 이관 CLI는 [`engines/sense`](../../engines/sense/README.md)에 있습니다.
설치 plugin에는 이를 포함하지 않으며, 로컬 명령의 성공을 원격 정본 변경으로 보지 않습니다.
