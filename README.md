# Personal Agent Toolkit

![Personal Agent Toolkit](./assets/personal-agent-toolkit-banner.png)

Sense, Corpus와 Hypes를 로컬에서 운영하는 개인용 에이전트 도구 모음입니다.

- **Sense**: 여러 작업에 적용할 사용자 통제형 작업 프로필
- **Corpus**: 업무 맥락과 원본 Source, 편집 가능한 Work 폴더의 연결
- **Hypes**: 에이전트가 유지하는 수정 가능한 사용자 관계 모델

제품 코드와 manifest, asset, lockfile만 배포하며 사용자 자료와 runtime database는 포함하지 않습니다.

## 선택 기준

- 중요한 선택에 장기적인 목적·책임·판단 기준이 필요하면 Sense를 사용합니다.
- 이어지는 업무의 파일, 이메일, 이전 작업이나 원문이 필요하면 Corpus를 사용합니다.
- 저장된 사용자 관계가 현재 해석·설명·선택을 바꿀 때에는 Hypes를 사용합니다.

단순 조회, 형식 변환과 한 단계 실행에는 관련 없는 개인 맥락을 불러오지 않습니다.

## 요구사항

- macOS
- [uv](https://docs.astral.sh/uv/)
- Python 3.11 이상
- Codex, Claude Code 또는 plugin을 지원하는 로컬 Claude Cowork

각 launcher는 committed lockfile에서 독립 Python 환경을 구성합니다.

## 설치

### Codex

```sh
codex plugin marketplace add Ruzzy77/personal-agent-toolkit
codex plugin add sense@personal-agent-toolkit
codex plugin add corpus@personal-agent-toolkit
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

## 갱신

### Codex

```sh
codex plugin marketplace upgrade personal-agent-toolkit
codex plugin add hypes@personal-agent-toolkit
```

### Claude Code

```sh
claude plugin marketplace update personal-agent-toolkit
claude plugin update hypes@personal-agent-toolkit --scope user
```

로컬 checkout으로 등록한 marketplace는 해당 디렉터리의 현재 내용을 사용합니다.

## Sense 시작

예시 프로필을 편집한 뒤 가져옵니다.

```sh
cp examples/sense-profile.example.json /tmp/my-sense-profile.json
./plugins/sense/bin/sense import-profile --input /tmp/my-sense-profile.json
./plugins/sense/bin/sense read --view full
./plugins/sense/bin/sense status
```

Sense는 현재 프로필 하나만 유지합니다. 일반 수정은 관련 section을 교체하며, 민감 항목과 영구 삭제는 로컬 명령에서 처리합니다.

## Corpus 시작

읽을 Source를 등록합니다.

```sh
./plugins/corpus/bin/corpus corpus add \
  --id my-work \
  --root /absolute/path/to/my-work \
  --execution-policy local_only
```

Chat과 로컬 작업이 함께 편집할 폴더는 Context에 Work Connection으로 연결합니다.

```sh
./plugins/corpus/bin/corpus workspace connect \
  --id my-drafts \
  --context ACTIVE_CONTEXT_ID \
  --name "My drafts" \
  --root /absolute/path/to/my-drafts \
  --execution-policy external_host_allowed
```

Source는 읽기 전용이며 Work Connection만 파일 편집을 허용합니다. 로컬 파일이 정본이고, 교체에는 직전 읽기에서 받은 version token을 사용합니다.

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

`plugins/sense`, `plugins/corpus`, `plugins/hypes`와 `gateway`가 소스이자 설치·실행 경로입니다. 별도 package 생성 단계는 없습니다.

## License

[Apache License 2.0](./LICENSE). Runtime dependency는 각 라이선스를 따릅니다. [THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md)를 함께 보십시오.
