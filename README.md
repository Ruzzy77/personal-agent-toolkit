# Personal Agent Toolkit

![Personal Agent Toolkit](./assets/personal-agent-toolkit-banner.png)

Sense, Corpus, Document Files와 Hypes를 로컬에서 운영하고, 선택한 원격 서비스를 소유자 인증에 연결하는 개인용 에이전트 도구 모음입니다.

- **Sense**: 여러 작업에 적용할 사용자 통제형 작업 프로필과 연결된 범용 Skill
- **Corpus**: 업무 맥락과 원본 Source, 편집 가능한 Work 폴더의 연결
- **Document Files**: PDF, Office와 HWP/HWPX를 포함한 문서 파일의 추출·변환·렌더링·편집
- **Hypes**: 에이전트가 유지하는 수정 가능한 사용자 관계 모델
- **Personal Agent Auth**: 여러 원격 서비스가 함께 쓰는 소유자 운영형 OAuth 구성

제품 코드와 manifest, asset, lockfile만 배포하며 사용자 자료와 runtime database는 포함하지 않습니다.

## 선택 기준

- 중요한 선택에 장기적인 목적·책임·판단 방향이나 연결된 범용 작업 방법이 필요하면 Sense를 사용합니다.
- 이어지는 업무의 파일, 이메일, 이전 작업이나 원문이 필요하면 Corpus를 사용합니다.
- 저장된 사용자 관계가 현재 해석·설명·선택을 바꿀 때에는 Hypes를 사용합니다.

단순 조회, 형식 변환과 한 단계 실행에는 관련 없는 개인 맥락을 불러오지 않습니다.

## 요구사항

- macOS
- [uv](https://docs.astral.sh/uv/)
- Python 3.11 이상
- Codex, Claude Code 또는 plugin을 지원하는 로컬 Claude Cowork

각 launcher는 committed lockfile에서 독립 Python 환경을 구성합니다.

선택 기능인 Personal Agent Auth의 로컬 시험에는 Node.js 24 이상이 필요합니다. Cloudflare와 Google 설정은 실제 원격 배포 단계에서만 필요합니다.

## 설치

### Codex

```sh
codex plugin marketplace add Ruzzy77/personal-agent-toolkit
codex plugin add sense@personal-agent-toolkit
codex plugin add corpus@personal-agent-toolkit
codex plugin add document-files@personal-agent-toolkit
codex plugin add hypes@personal-agent-toolkit
```

로컬 checkout에서는 저장소 루트에서 다음 명령을 사용합니다.

```sh
codex plugin marketplace add .
```

### Claude Code

```sh
claude plugin marketplace add Ruzzy77/personal-agent-toolkit
claude plugin install sense@personal-agent-toolkit --scope user
claude plugin install corpus@personal-agent-toolkit --scope user
claude plugin install document-files@personal-agent-toolkit --scope user
claude plugin install hypes@personal-agent-toolkit --scope user
```

로컬 checkout은 저장소 루트에서 `claude plugin marketplace add .`로 등록합니다.

### Claude Cowork

`Customize → Plugins → Add marketplace`에서 다음 marketplace를 추가합니다.

```text
Ruzzy77/personal-agent-toolkit
```

설치나 갱신 뒤에는 새 세션을 시작합니다.

## ChatGPT 연결

로컬 plugin만 쓸 때에는 gateway가 필요하지 않습니다. 개인 ChatGPT에 연결하려면 [gateway guide](./gateway/GUIDE.md)에 따라 제품별 OpenAI Secure MCP Tunnel과 developer connection을 만듭니다.

Gateway는 선택한 plugin의 MCP를 고정 loopback 경로로 전달합니다.

| 제품 | 경로 |
|---|---|
| Sense | `/sense/mcp` |
| Corpus | `/corpus/mcp` |
| Hypes | `/hypes/mcp` |

Gateway는 제품을 합치거나 데이터를 옮기지 않습니다. 한 LaunchAgent가 선택한 제품과 tunnel을 실행합니다.

Cloudflare에 원격 MCP를 배포할 때에는 [`auth`](./auth/README.md)를 이용해 Google 소유자 인증을 연결할 수 있습니다. 인증 Worker와 같은 Cloudflare 계정에 둔 MCP Worker는 비공개 Service Binding으로 토큰을 검사합니다. Sites 같은 별도 호스팅 서비스는 이 비공개 연결을 직접 쓴다고 가정하지 않습니다. 그런 화면은 그대로 두고, MCP endpoint만 같은 Cloudflare 계정의 Worker로 분리하는 구성이 현재 기본안입니다.

## 버전 갱신

plugin base version을 하나라도 바꾸면 소스 변경, 원격 저장소 반영과 각 실행 환경의 갱신을 한 번의 절차로 마칩니다. 로컬 checkout으로 등록한 marketplace는 해당 디렉터리의 현재 내용을 사용하며, 원격 배포 검증에는 GitHub marketplace를 사용합니다.

### Codex

```sh
codex plugin marketplace upgrade personal-agent-toolkit
codex plugin add sense@personal-agent-toolkit
codex plugin add corpus@personal-agent-toolkit
codex plugin add document-files@personal-agent-toolkit
codex plugin add hypes@personal-agent-toolkit
codex plugin list --json
```

변경한 plugin이 활성화되어 있고 새 버전과 공개 MCP 도구가 보이는지 확인한 뒤 새 작업을 시작합니다.

### Claude Code

```sh
claude plugin marketplace update personal-agent-toolkit
claude plugin update sense@personal-agent-toolkit --scope user
claude plugin update corpus@personal-agent-toolkit --scope user
claude plugin update document-files@personal-agent-toolkit --scope user
claude plugin update hypes@personal-agent-toolkit --scope user
claude plugin list
```

갱신 뒤 Claude Code를 다시 시작하고 새 세션에서 각 plugin의 버전과 공개 MCP 도구를 확인합니다.

### Claude Desktop

`Customize → Plugins`에서 `personal-agent-toolkit` marketplace를 갱신한 뒤 Sense, Corpus, Document Files와 Hypes를 모두 업데이트합니다. 업데이트 항목이 나타나지 않거나 이전 버전이 남아 있으면 해당 plugin을 설치 해제한 뒤 같은 marketplace에서 다시 설치합니다. Claude Desktop을 다시 시작하고 새 Cowork 세션에서 네 plugin의 Skill과 로컬 MCP 도구가 보이는지 확인합니다. 일반 Chat에 Skill만 나타나는 상태를 로컬 MCP 연결 확인으로 간주하지 않습니다.

### ChatGPT 웹

[gateway guide](./gateway/GUIDE.md)의 버전 갱신 절차에 따라 LaunchAgent를 다시 설치해 새 Codex plugin 경로를 반영하고 gateway 상태를 확인합니다. 이어 `플러그인 → 설치됨 → Sense·Corpus·Hypes → 관리`에서 각 plugin을 `새로 고침`합니다. 각 plugin의 액션 목록이 현재 MCP 도구와 일치하고 권한이 `모든 액션 허용`인지 확인합니다. 새로 고침으로 현재 액션이 반영되지 않으면 해당 developer plugin을 다시 연결합니다.

Document Files는 로컬 문서 처리 plugin이며 ChatGPT 웹에 별도 developer plugin으로 노출하지 않습니다. Corpus가 로컬 Source를 갱신할 때 설치된 Document Files를 읽기 전용 처리 경계로 사용합니다.

### 완료 기준

변경한 버전과 lockfile을 포함한 소스가 원격 저장소에 반영되고, Codex·Claude Code·Claude Desktop에서 네 로컬 plugin의 새 버전이나 현재 도구 목록을 확인해야 갱신이 완료됩니다. ChatGPT 웹에서는 Sense·Corpus·Hypes의 현재 액션과 권한을 별도로 확인합니다. Sense, Corpus와 Hypes의 사용자 데이터는 설치 경로 밖의 각 Application Support 폴더에 유지합니다.

## Sense 시작

예시 프로필을 편집한 뒤 가져옵니다.

```sh
cp examples/sense-profile.example.json /tmp/my-sense-profile.json
./plugins/sense/launchers/sense import-profile --input /tmp/my-sense-profile.json
./plugins/sense/launchers/sense read --view full
./plugins/sense/launchers/sense status
```

Sense는 현재 프로필 하나만 유지합니다. 일반 수정은 관련 section을 교체하며, 민감 항목과 영구 삭제는 로컬 명령에서 처리합니다.
각 section에는 검토한 `SKILL.md`를 하나까지 연결할 수 있습니다. 색인에는 Skill의 이름과 설명이
나타나고, 해당 section을 읽으면 전체 작업 방법을 함께 불러옵니다. 일반 Section Skill은 사용자가
명시적으로 요청하면 현재 버전과 전체 교체안을 대조해 Chat에서 수정할 수 있습니다. 민감 Skill의
반영과 Skill 제거는 Sense의 로컬 명령에서 처리합니다.

## Corpus 시작

읽을 Source를 등록합니다.

```sh
./plugins/corpus/launchers/corpus corpus add \
  --id my-work \
  --root /absolute/path/to/my-work \
  --execution-policy local_only
```

Chat과 로컬 작업이 함께 편집할 폴더는 Context에 Work Connection으로 연결합니다.

```sh
./plugins/corpus/launchers/corpus workspace connect \
  --id my-drafts \
  --context ACTIVE_CONTEXT_ID \
  --name "My drafts" \
  --root /absolute/path/to/my-drafts \
  --execution-policy external_host_allowed
```

Source는 읽기 전용이며 Work Connection만 파일 편집을 허용합니다. 로컬 파일이 정본이고, 교체에는 직전 읽기에서 받은 version token을 사용합니다. 사용자가 명시적으로 요청하면 기존 Context 항목의 종류·본문·상태를 현재 Context version과 대조해 한 번에 수정할 수 있습니다. 나머지 속성과 Source 연결은 보존하며 항목 추가·삭제는 로컬 작업으로 남깁니다. Context Skill은 Source와 분리되어 있으며, 사용자가 요청한 전체 교체안을 현재 Skill 버전과 대조해 Chat에서 수정할 수 있습니다.

## Hypes 시작

Hypes는 Node, Predicate와 Edge로 사용자 관계를 표현합니다. 공개 MCP 도구는 다음 두 개입니다.

- `hypes_read`: 이름·별칭·설명에서 관련 그래프 탐색
- `hypes_rewrite`: 객체 추가·교체·삭제를 한 transaction으로 적용

Hypes는 대화나 프로젝트 자료를 저장하지 않습니다. 현재 요청이 저장된 관계보다 우선하며, 모델은 이후 상호작용에서 수정될 수 있습니다.

## 저장 위치

| 제품 | 기본 위치 |
|---|---|
| Sense | `~/Library/Application Support/Sense/` |
| Corpus | `~/Library/Application Support/Corpus/` |
| Hypes | `~/Library/Application Support/Hypes/` |

Provider 자료는 원래 서비스에 남습니다. 자세한 범위는 [PRIVACY.md](./PRIVACY.md)에 있습니다.

## 저장소 구조

`plugins/sense`, `plugins/corpus`, `plugins/hypes`와 `gateway`가 로컬 제품의 소스이자 설치·실행 경로입니다. `auth`는 소유자가 직접 배포하는 원격 인증 구성입니다. 다중 리소스 권한 분리와 Google 소유자 로그인은 로컬 환경에서 먼저 살펴보며, 실제 계정 자원과 자격 증명은 배포 환경에서만 만듭니다.

plugin base version을 바꿀 때에는 manifest, `pyproject.toml`, package `__version__`와 lockfile에 같은 버전을 반영합니다. 배포본은 각 plugin 폴더의 현재 소스에서 만들며, 갱신한 plugin이 시작되고 공개 MCP 도구를 내보내는 상태까지 이어서 다룹니다.

## 개발 검사

Python 소스, 스크립트와 테스트는 루트 [`ruff.toml`](./ruff.toml)의 형식을 따릅니다. 변경한 뒤 다음 검사를 실행합니다.

```sh
uvx ruff==0.16.5 format --check plugins/sense plugins/corpus plugins/hypes gateway
uvx ruff==0.16.5 check plugins/sense plugins/corpus plugins/hypes gateway
```

## License

[Apache License 2.0](./LICENSE). Runtime dependency는 각 라이선스를 따릅니다. [THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md)를 함께 보십시오.
