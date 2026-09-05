# Personal Agent Toolkit

![Personal Agent Toolkit](./assets/personal-agent-toolkit-banner.png)

Personal Agent Toolkit은 업무 맥락과 문서 작업을 연결하는 개인용 에이전트 도구 모음입니다.
Sense, Corpus, Hypes, Journal, Library와 Design은 소유자 인증형 상시 원격 MCP로 공유합니다.
Document Files는 단일 문서 Skill과 호스트 실행 환경으로 문서를 처리하며, Personal Agent Sync는
허용된 Finder 자료와 원격 서비스를 연결합니다.

- **Sense**: 여러 작업에 적용할 사용자 통제형 작업 프로필과 연결된 범용 Skill
- **Corpus**: 업무 맥락과 원본 Source, 편집 가능한 Work 폴더의 연결
- **Document Files**: PDF·Office·HWP/HWPX 읽기, 지원 형식의 작성·편집·변환·렌더링과 Google Docs·Sheets·Slides 작업
- **Hypes**: 에이전트가 유지하는 수정 가능한 사용자 관계 모델
- **Journal**: 일간 확인, 사용자 확정 상태, 주간 마감과 기간별 흐름을 잇는 개인 불릿저널
- **Design**: 제품 화면의 설계·구현·검토, 사용자 조사와 개인 디자인 자산·템플릿 라이브러리
- **Library**: Daily·Digest·Research 발간호의 읽기, 편집, 이미지 업로드와 발행
- **Personal Agent Sync**: Finder 자료의 이동·변경 감지, 로컬 추출 결과 반영과 원격 Work 요청 수행
- **Personal Agent Auth**: 여러 원격 서비스가 함께 쓰는 소유자 운영형 OAuth 구성

제품 코드와 manifest, asset, lockfile만 배포하며 사용자 자료와 runtime database는 포함하지 않습니다.

## 선택 기준

- 중요한 선택에 장기적인 목적·책임·판단 방향이나 연결된 범용 작업 방법이 필요하면 Sense를 사용합니다.
- 이어지는 업무의 파일, 이메일, 이전 작업이나 원문이 필요하면 Corpus를 사용합니다.
- 저장된 사용자 관계가 현재 해석·설명·선택을 바꿀 때에는 Hypes를 사용합니다.
- 오늘과 이번 주의 진행 상태, 사용자 확정 처리와 기간별 기록이 필요하면 Journal을 사용합니다.
- 제품 화면을 만들거나 고치고, 디자인·접근성을 검토하거나 사용자 조사를 다룰 때에는 Design을 사용합니다.
- 기존 Library 발간호를 읽거나 고치고 새 호를 발행할 때에는 Library를 사용합니다.
- 문서 파일을 읽고 구조·값을 추출하거나 DOCX·PDF·XLSX·CSV·PPTX·HWPX 또는 Google
  Docs·Sheets·Slides를 만들고 고칠 때에는 Document Files를 사용합니다. 형식별 안내는 이 Skill
  안에서 필요한 부분만 읽습니다. 열린 Excel 앱 제어는 별도 live-control 기능의 범위입니다.

단순 조회, 형식 변환과 한 단계 실행에는 관련 없는 개인 맥락을 불러오지 않습니다.

## 요구사항

- 원격 MCP와 OAuth 연결을 지원하는 Codex, Claude Code, Claude Desktop/Cowork, ChatGPT 또는 claude.ai
- 로컬 Source와 Work를 연결할 때 macOS, [uv](https://docs.astral.sh/uv/)와 Python 3.12 이상

OpenAI에서는 원격 제품을 등록 app 하나로 연결하고 문서 기능은 단일 `document-files` Skill로
배포합니다. Codex는 이를 통합 plugin으로 설치하고, 비공개 개인 ChatGPT는 등록 app과 계정의
`Document Files` Personal Skill 하나를 사용합니다. Claude에서는 제품별 plugin을
쓰고 Document Files는 local MCP로 유지합니다. 로컬 Source와 Work를 연결하는 Mac에는 Corpus runtime과
Document Files를 포함한 Sync runtime을 설치합니다. AI client의 plugin cache나 저장소 worktree는 Sync
실행 경로로 사용하지 않습니다.

선택 기능인 Personal Agent Auth의 로컬 시험에는 Node.js 24 이상이 필요합니다. Cloudflare와 Google 설정은 실제 원격 배포 단계에서만 필요합니다.

## 설치

### ChatGPT 개인 계정

ChatGPT의 **Personal**에는 `Personal Agent Toolkit` 등록 app 하나를 설치합니다. 비공개 개인 app은
plugin 파일을 함께 배포하지 않으므로 문서 기능은 다음 명령으로 만든 `document-files` archive
하나를 **Personal → Skills → 컴퓨터에서 업로드**에서 설치합니다. archive는 임시 출력물이며
저장소에 보관하지 않습니다.

```sh
python3 scripts/build_chatgpt_personal_skills.py /tmp/personal-agent-toolkit-skills
```

설치 구성은 Plugin 화면의 `Personal Agent Toolkit` app 하나와 Skills 화면의 `Document Files`
하나입니다. 형식별 작성·편집 방법과 Google Docs·Sheets·Slides 처리 방법은 이 Skill에 포함하며,
`Documents`, `PDF`, `Spreadsheets`, `Presentations`나 형식별 별칭을 따로 설치하지 않습니다.
기존 다섯 Skill 구성에서 전환하는 절차는 아래 [ChatGPT와 Codex 갱신](#chatgpt와-codex)을 따릅니다.

새 대화에서 통합 Skill의 읽기·추출과 형식별 작성·편집이 동작하는지 확인한 뒤 같은 기능의 OpenAI
기본 Documents, PDF, Spreadsheets, Presentations를 비활성화하거나 제거합니다. 개인 계정에서 해당
항목이 `관리자가 설치함`으로 고정되어 있으면 제거할 수 없으므로 `Document Files` Personal Skill
카드에서 작업을 시작하고 기본 항목을 따로 선택하지 않습니다. 별도 Document Files app이나 제품별
`Created by me` app은 함께 사용하지 않습니다. 개인 Skill을 명시적으로 선택한 작업에서는 같은
기능의 OpenAI 기본 plugin이나 Library를 함께 호출하지 않도록 archive에 개인 계정용 실행 경계를
덧붙입니다. 이 통합은 호스트 라이브러리나 Excel live-control 같은 별도 live 앱 제어 기능의 제거를
뜻하지 않습니다.

Sense, Corpus, Hypes, Journal, Library와 Design의 원격 도구를 사용할 때에는 설치·갱신 뒤 새 Chat 또는
Work 대화를 시작하고, 현재 데이터를 읽거나 바꾸는 메시지마다 `Personal Agent Toolkit` app을 선택하거나
`@`로 지정합니다. app 선택은 메시지 단위이며, 개발자 MCP를 지원하지 않는 대화 유형에서는 기존 대화에서
재시도하지 않고 지원되는 새 대화로 옮깁니다.

### Codex

Codex에서는 같은 등록 app을 참조하는 `Personal Agent Toolkit` plugin 하나를 저장소 marketplace에서
설치합니다. 설치 항목만 이 이름을 쓰며 내부 제품과 Skill은 짧은 이름을 유지합니다.

```sh
codex plugin marketplace add Ruzzy77/personal-agent-toolkit
codex plugin add personal-agent-toolkit@personal-agent-toolkit
```

로컬 checkout에서는 저장소 루트에서 다음 명령으로 marketplace 소스를 등록할 수 있습니다.

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
claude plugin install journal@personal-agent-toolkit --scope user
claude plugin install design@personal-agent-toolkit --scope user
claude plugin install library@personal-agent-toolkit --scope user
```

로컬 checkout은 저장소 루트에서 `claude plugin marketplace add .`로 등록합니다.

### Claude Cowork

`Customize → Plugins → Add marketplace`에서 다음 marketplace를 추가합니다.

```text
Ruzzy77/personal-agent-toolkit
```

설치나 갱신 뒤에는 새 세션을 시작합니다.

## 원격 MCP 연결

OpenAI 통합 app과 Claude의 제품별 plugin은 다음 상시 HTTPS endpoint를 사용합니다.

| 제품 | endpoint |
|---|---|
| Personal Agent Toolkit | `https://personal-agent-context.hiyaq77.workers.dev/mcp` |
| Sense | `https://personal-agent-context.hiyaq77.workers.dev/sense/mcp` |
| Corpus | `https://personal-agent-context.hiyaq77.workers.dev/corpus/mcp` |
| Hypes | `https://personal-agent-context.hiyaq77.workers.dev/hypes/mcp` |
| Journal | `https://personal-agent-journal.hiyaq77.workers.dev/mcp` |
| Library | `https://personal-library-mcp.hiyaq77.workers.dev/api/mcp` |
| Design | `https://personal-agent-design.hiyaq77.workers.dev/mcp` |

OpenAI plugin은 등록 app을 통해 통합 endpoint를 사용하고, Claude plugin은 제품별 endpoint를 직접
내장합니다. ChatGPT와 Codex는 같은 등록 app과 소유자 인증을 사용합니다. claude.ai에서는 제품별
주소를 사용자 계정의 MCP 연결로 등록합니다. 어느 경우에도 Mac의 loopback server나 공개 터널은
필요하지 않습니다.

Journal, Library와 Design의 시각 화면은 각각 소유자 전용
[Journal Site](https://personal-journal.ruzzy.chatgpt.site)와
[Library Site](https://personal-edition-library.ruzzy.chatgpt.site),
[Design Reference Library Site](https://personal-material-index.ruzzy.chatgpt.site)를 사용합니다.

원격 서비스는 [`auth`](./auth/README.md)의 Google 소유자 인증을 공유합니다. 인증 Worker와 같은
Cloudflare 계정에 둔 MCP Worker는 비공개 Service Binding으로 토큰을 검사합니다. Sites 화면과
MCP endpoint는 서로 다른 배포 경계입니다.

### Finder 자료 연결

로컬 자료를 연결할 Mac에는 [`apps/sync`](./apps/sync/README.md)를 설치합니다. Sync 앱만 Finder
경로와 파일시스템 정체성을 보유하고 외부 방향 연결을 유지합니다. 허용된 Source의 변경은 Sync와
같은 환경에 설치된 Document Files로 로컬 분석한 뒤 확정된 Corpus revision으로 원자적으로 반영하며,
허용된 Work 요청은 현재 Connection 권한과 generation을 다시 검사한 뒤 로컬 Corpus에 위임합니다.
원문 바이트는 Personal Agent Toolkit 서버나 원격 Corpus에 보내지 않고 로컬 projection만 전송합니다.
로컬 분석 환경이 없으면 `runtime_unavailable`로 중단하며 Cloudflare 분석기로 폴백하지 않습니다.

분석기와 설정의 정확한 식별자는 각 projection이 만들어진 조건을 확인하는 데 사용합니다. 이
식별자가 달라졌다는 이유만으로 내용이 같은 문서를 다시 분석하지는 않습니다. 기존 결과를
실제로 다시 만들어야 하는 형식만 Document Files의 명시적인 재분석 세대를 올려 제한된
대기열에서 갱신합니다.

## 버전 갱신

관련 변경을 release로 묶어 소스와 원격 저장소에 반영하고, 영향을 받는 서비스·Site·클라이언트만
갱신합니다. 작은 변경마다 삭제·설치하지 않고 기존 업데이트 기능을 먼저 사용합니다. 구체적인
반영 단위와 확인 시점은 [`DESIGN.md`](./DESIGN.md#반영-묶음과-확인-시점)를 따릅니다.

- 공개 MCP 도구의 이름·설명·입출력 구조나 `annotations`가 바뀌면 OpenAI 통합 app의 등록을
  새로 고치고 해당 클라이언트의 실제 도구 설명·구조를 확인합니다. Skill·manifest·host 번들이
  바뀌면 Codex의 packaging revision과 영향을 받는 Claude plugin 또는 개인 ChatGPT Skill을 갱신합니다.
- Journal·Library·Design의 공유 업무 구현을 바꾸면 개별 Worker와 통합 Context Worker를
  함께 반영합니다. 두 경로가 같은 제품 저장층에 접근하므로 한쪽만 구형 구현으로 남기지 않습니다.
  Site 변경은 해당 화면의 별도 배포에 반영합니다.
- 원격 version만 바뀌면 Sync·Corpus·Document Files의 로컬 실행 환경을 재설치하지 않습니다.
  초기 이관 확인은 확인할 release의 `products.json`을 사용하고, 로컬 코드·의존성·호환 조건이
  달라질 때 로컬 환경을 갱신합니다.

GitHub marketplace에서 현재 배포본을 갱신하며, 로컬 checkout marketplace는 발행 전 시험에만
사용합니다. 설치 표시와 새 작업에서의 실제 사용 확인은 구분합니다.

### ChatGPT와 Codex

OpenAI용 원격 제품 변경은 `Personal Agent Toolkit` 등록 app에 한 번 반영합니다. 문서 Skill 변경은
Codex 통합 plugin을 갱신하고, 개인 ChatGPT에서는 같은 빌드 명령으로 `document-files` archive
하나를 다시 만들어 기존 `Document Files`를 교체합니다. 새 작업에서 현재 Skill과 변경된 등록 app
도구를 확인합니다.

기존 다섯 문서 Skill을 단일 Skill로 전환할 때에는 다음을 하나의 반영 묶음으로 수행합니다.

1. Codex 통합 plugin을 정상 업데이트합니다. 개인 ChatGPT에서는 **만들기 → 컴퓨터에서 업로드**로
   `document-files` archive를 올리고 같은 이름의 **기존 항목 교체**를 선택합니다. 편집기에 archive를
   첨부하는 것은 전체 Skill 교체가 아닙니다.
2. 통합 Skill에 형식별 작업 안내와 필요한 실행 파일이 포함됐고, 읽기·추출·작성·편집이 동작하는지
   확인합니다. Google native 문서 경로는 필요한 연결 도구와 권한이 있는 환경에서 구분해 확인합니다.
3. 이전에 올린 `Documents`, `PDF`, `Spreadsheets`, `Presentations` Personal Skill 네 항목을
   제거합니다. 이전 archive의 식별자는 `word-documents`, `pdf-files`, `workbooks`, `slide-decks`일
   수 있으므로 같은 이름의 기본 plugin과 구분합니다. 형식별 실행 코드나 호스트 라이브러리는
   제거하지 않습니다.
4. 새 세션에서 Toolkit의 문서 Skill이 `document-files` 하나로 노출되고 네 별도 Skill이 사라졌는지,
   현재 본문과 실제 문서 작업이 일치하는지 확인합니다. 기존 세션의 자동 목록과 설치 표시만으로
   전환 완료를 판정하지 않습니다.

이 절차는 단일 Skill 전환의 완료 조건이며, 안내 문서의 변경 자체가 설치 완료를 뜻하지 않습니다.

```sh
codex plugin marketplace upgrade personal-agent-toolkit
codex plugin add personal-agent-toolkit@personal-agent-toolkit
codex plugin list --json
```

변경한 plugin이 활성화되어 있고 새 버전과 현재 Skill 또는 도구가 보이는지 확인한 뒤 새 작업을
시작합니다.

### Claude Code

```sh
claude plugin marketplace update personal-agent-toolkit
claude plugin update sense@personal-agent-toolkit --scope user
claude plugin update corpus@personal-agent-toolkit --scope user
claude plugin update document-files@personal-agent-toolkit --scope user
claude plugin update hypes@personal-agent-toolkit --scope user
claude plugin update journal@personal-agent-toolkit --scope user
claude plugin update design@personal-agent-toolkit --scope user
claude plugin update library@personal-agent-toolkit --scope user
claude plugin list
```

갱신 뒤 Claude Code를 다시 시작하고 새 세션에서 각 plugin의 버전, 현재 Skill 또는 원격 MCP
도구를 확인합니다.

### Claude Desktop

`사용자 지정 → 플러그인 → 내 것`에서 변경한 plugin의 추가 작업 메뉴로 `업데이트 확인`을 실행하고,
상세 화면의 `업데이트`를 사용합니다. 목록 조회 전에는 상세 화면에 업데이트 버튼이 없을 수 있습니다.
정상 갱신 뒤에도 이전 버전이 남거나 설치가 손상된 경우에만 해당 plugin을 재설치합니다.

원격 도구의 이름·설명·입출력 구조나 `annotations`가 바뀌었다면 기존 커넥터 상세 화면의 추가 옵션에서
`도구 목록 새로 고침`을 실행합니다. 설명만 바뀐 경우도 포함합니다. plugin 갱신과 커넥터 목록
갱신은 별개이며 기존 인증과 도구 권한은 유지합니다. Desktop을 다시
시작한 뒤 새 Cowork 세션에서 현재 Skill과 실제 도구 입력·호출을 확인합니다. 기존 작업은 목록을
새로 고쳐도 이전 입력을 유지할 수 있으므로 그 상태를 서버 배포 실패로 단정하지 않습니다. 일반
Chat의 Skill 표시나 설치 버전만으로 MCP 연결과 실제 사용을 확인했다고 하지 않습니다.

### claude.ai

Sense·Corpus·Hypes·Journal·Library·Design의 사용자 MCP 연결을 각각 현재 endpoint로 등록하거나 갱신하고
소유자 인증을 마칩니다. 각 연결의 액션 목록이 현재 MCP 도구와 일치하는지 확인합니다. Secure
MCP Tunnel과 로컬 gateway를 사용하던 이관은 완료됐으며, 현재 구성에는 다시 추가하지 않습니다.

Document Files는 웹 클라이언트에 별도 MCP로 노출하지 않습니다. ChatGPT는 개인 계정에 올린 Skill과
OpenAI host 실행 환경을 사용하며, 필요한 실행 기능이 없는 작업은 로컬 Sync나 로컬 Codex로
옮깁니다.

### 완료 기준

변경한 버전과 lockfile을 포함한 소스가 원격 저장소에 반영되고, OpenAI 통합 app·Codex plugin·개인
ChatGPT의 `Document Files` Skill과 Claude plugin이 각각 갱신된 뒤 지원 client에서 현재 Skill과
같은 MCP 도구를 확인해야 갱신이 완료됩니다.
로컬 Source를 연결한 경우 Sync의 재접속, 이동·변경 감지, 실패 복구와 권한 거부도 함께
검증합니다.

Sites 전용 소스 저장소에는 임시 export를 한 방향으로 반영합니다. 로컬 package 의존성을 포함한
자급식 소스는 `python3 scripts/export_site_source.py sites/<product> <temporary-directory>`로 만들며,
생성된 디렉터리를 정본으로 다시 편집하거나 저장소에 남기지 않습니다.

## Sense 시작

plugin과 원격 MCP의 소유자 인증을 마치면 모든 지원 클라이언트가 같은 현재 프로필을
읽습니다. 일반 항목과 Section Skill은 현재 버전을 대조해 대화에서 수정합니다.

아래 launcher는 로컬 구현을 시험하거나 최초 이관 자료를 준비할 때만 사용합니다. 여기서
바꾼 내용은 원격 정본을 자동으로 바꾸지 않습니다.

```sh
cp examples/sense-profile.example.json /tmp/my-sense-profile.json
./engines/sense/launchers/sense import-profile --input /tmp/my-sense-profile.json
./engines/sense/launchers/sense read --view full
./engines/sense/launchers/sense status
```

최초 원격 이관은 [`apps/sync`](./apps/sync/README.md)의 소유자 장치 인증 절차를 따릅니다.
Sense는 소유자별 현재 프로필 하나를 유지하고 일반 수정은 관련 section을 원자적으로
교체합니다. 민감 section 본문은 명시적인 조회에서만 읽을 수 있으며 공개 원격 MCP로
수정할 수 없습니다.
각 section에는 검토한 `SKILL.md`를 하나까지 연결할 수 있습니다. 색인에는 Skill의 이름과 설명이
나타나고, 해당 section을 읽으면 전체 작업 방법을 함께 불러옵니다. 일반 Section Skill은 사용자가
명시적으로 요청하면 현재 버전과 전체 교체안을 대조해 Chat에서 수정할 수 있습니다. 민감 Skill의
원격 저장, Skill 제거와 원격 데이터 영구 삭제는 공개 MCP 범위에 포함하지 않습니다.

## 로컬 Corpus 연결 시작

원격 Corpus 조회만 할 때에는 아래 로컬 명령이 필요하지 않습니다. Finder 자료를 연결할 Mac에
Personal Agent Sync의 고정 runtime을 설치한 뒤 읽을 Source를 등록합니다.

```sh
./engines/corpus/launchers/corpus corpus add \
  --id my-work \
  --root /absolute/path/to/my-work \
  --execution-policy local_only
```

Chat과 로컬 작업이 함께 편집할 폴더는 Context에 Work Connection으로 연결합니다.

```sh
./engines/corpus/launchers/corpus workspace connect \
  --id my-drafts \
  --context ACTIVE_CONTEXT_ID \
  --name "My drafts" \
  --root /absolute/path/to/my-drafts \
  --execution-policy external_host_allowed
```

Source는 읽기 전용이며 Work Connection만 파일 편집을 허용합니다. 원격 Corpus에는 마지막 정상
추출 revision을 내구성 있게 보존하고, 로컬 원자료는 필요할 때 Sync가 다시 읽습니다. Work 파일
교체에는 직전 읽기에서 받은 version token을 사용합니다. 사용자가 명시적으로 요청하면 기존
Context 항목의 종류·본문·상태를 현재 Context version과 대조해 한 번에 수정할 수 있습니다.
Context Skill은 Source와 분리되어 있으며, 사용자가 요청한 전체 교체안을 현재 Skill 버전과
대조해 Chat에서 수정할 수 있습니다.

## Hypes 시작

Hypes는 Node, Predicate와 Edge로 사용자 관계를 표현합니다. 공개 MCP 도구는 다음 두 개입니다.

- `hypes_read`: 이름·별칭·설명에서 관련 그래프 탐색
- `hypes_rewrite`: 객체 추가·교체·삭제를 한 transaction으로 적용

Hypes는 대화나 프로젝트 자료를 저장하지 않습니다. 현재 요청이 저장된 관계보다 우선하며, 모델은 이후 상호작용에서 수정될 수 있습니다.

## Design 시작

Design은 화면 설계·구현을 다루는 `design`, 근거가 있는 검토를 다루는 `design-review`, 사용자
조사 설계와 종합을 다루는 `design-research`의 세 Skill과 개인 디자인 자산 서비스를 함께
제공합니다. 레시피·패턴 메타데이터는 Design D1, 템플릿과 예시 파일은 Design R2가 정본입니다.
현재 프로젝트의 디자인 시스템을 우선하며, 시각 방향 탐색에 실제로 필요할 때만 후보 1–3개를
골라 씁니다.

시각적 탐색과 비교는 <https://personal-material-index.ruzzy.chatgpt.site>에서 할 수 있으며, 화면
소스는 `sites/design`, 저장과 MCP는 `services/design`에서 관리합니다. 개인 자산은 공개 저장소나
plugin 묶음에 복사하지 않습니다.

## Journal 시작

Journal plugin을 설치하고 원격 MCP의 소유자 인증을 마치면 이번 주 보드와 기간 기록을 대화에서 읽을 수 있습니다. 시각적 보드는 <https://personal-journal.ruzzy.chatgpt.site>에서 확인하며, 화면 소스는 `sites/journal`에서 관리합니다.

인증된 원격 MCP를 호출할 수 있는 자동화는 `manage-journal` Skill과
`journal_ingest_items`로 달라진 항목만 반영합니다. MCP client가 아닌 별도 로컬 모니터가
필요할 때에만 `plugins/journal/launchers/journal`과 읽기·ingest 전용 자격 증명을 사용합니다.
이 토큰은 macOS Keychain의 `personal-agent-journal-ingest` service에 두며 저장소나 자동화
프롬프트에 넣지 않습니다.

## Library 시작

Library plugin을 설치하고 원격 MCP의 소유자 인증을 마치면 Daily·Digest·Research 발간호를
대화에서 읽고 고치거나 새 호를 발행할 수 있습니다. 읽기와 직접 편집은
<https://personal-edition-library.ruzzy.chatgpt.site>에서 이어지며, 화면 소스는
`sites/library`, 원격 MCP는 `services/library`에서 관리합니다.

본문이나 시각물을 만들고 개작할 때에는 `manage-library` Skill이 Corpus의
`library-editorial` Context와 현재 발간호를 연결합니다. 온라인 정본을 바꾼 뒤에는 같은
발간호를 다시 읽어 HTML, 참고자료, 표지와 발행 정보가 실제로 저장됐는지 확인합니다.

## 저장 위치

| 제품 | 기본 위치 |
|---|---|
| 원격 Sense·Corpus·Hypes | 소유자 인증형 원격 저장층 |
| 원격 Journal | 소유자 운영형 D1 |
| Library 문서·이미지 | Library service D1·R2 |
| Design 레시피·템플릿 | Design service D1·R2 |
| Document Files 입력 | 호출자와 현재 실행 호스트 소유; 원격 서비스에 보관하지 않음 |
| Sync 상태·정책·runtime | `~/Library/Application Support/Personal Agent Sync/` |
| 선택적인 이관 입력 | 기존 로컬 Sense·Corpus·Hypes의 `Application Support` 폴더 |

Provider 자료는 원래 서비스에 남습니다. 자세한 범위는 [PRIVACY.md](./PRIVACY.md)에 있습니다.

## 저장소 구조

제품 사이의 공통 실행 경계와 통일 방향은 [`DESIGN.md`](./DESIGN.md), 현재 제품·version·공개 MCP와
client 배포 묶음은 [`products.json`](./products.json)을 기준으로 합니다. 제품 내부 동작은 각
plugin의 `DESIGN.md`에 둡니다.

`plugins/sense`, `plugins/corpus`, `plugins/hypes`, `plugins/journal`, `plugins/library`, `plugins/design`은 제품 계약과
Claude용 원격 MCP 연결 및 Skill을 배포합니다. `plugins/document-files`는 단일 Python 정본과 Claude
local MCP와 단일 `document-files` Skill을 소유합니다. `plugins/personal-agent-toolkit`은 일곱 제품의
현재 Skill, Document Files의 형식별 내부 참고 자료·host 실행 번들과 통합 등록 app을 담는 OpenAI
배포 묶음입니다.
`engines/sense`, `engines/corpus`, `engines/hypes`의 Python 구현은 로컬 개발·이관 및 Sync에만
사용합니다. `apps/sync`는 Finder 권한을 가진 outbound-only
bridge, `services/remote-context`는 세 제품의 원격 저장·MCP·Sync broker와 여섯 상태형 제품의 OpenAI
통합 MCP를 제공합니다. 문서 분석용 Cloudflare Worker나 Service Binding은 두지 않습니다.
Design은 `plugins/design`, `services/design`, `sites/design`으로 구성하며 개인 데이터는 service의
D1·R2에만 둡니다. `services/journal`은 Journal 서비스이고,
`sites/journal`은 소유자 전용 화면입니다. `services/library`와 `sites/library`는 Library의 서비스
소유 저장·MCP와
읽기·편집 화면을 나눕니다. 기존 Sites 저장층의 이전과 rollback 경계는
[`DESIGN.md`](./DESIGN.md)에 구분해 둡니다. `auth`는 원격 제품이 함께 쓰는 소유자 인증
구성입니다. 실제 계정 자원과 자격 증명은 배포 환경에서만 만듭니다.

plugin base version을 바꿀 때에는 해당 client manifest와 `products.json`을 맞춥니다. OpenAI 통합
plugin의 packaging revision은 제품 release와 분리합니다. plugin 자체에 Python package가 있는
Document Files와 같은 제품으로 배포하는 service·Site는 package와 lockfile도 같은 release version을
사용합니다. 배포본은 각 plugin 폴더의 현재 소스에서 만들며, 갱신한 plugin이 시작되고 공개 MCP
도구를 내보내는 상태까지 이어서 다룹니다.

## 개발 검사

Sense, Corpus, Hypes와 Sync의 Python 소스·스크립트·테스트는 루트
[`ruff.toml`](./ruff.toml)의 형식을 따릅니다. 변경한 뒤 다음 검사를 실행합니다.

```sh
python3 scripts/check_repository.py
uvx ruff==0.16.5 format --check engines/sense engines/corpus engines/hypes apps/sync
uvx ruff==0.16.5 check engines/sense engines/corpus engines/hypes apps/sync
```

Pull request와 `main` 갱신에서는 저장소 계약, Python runtime과 원격 Worker·Site를 검사합니다.
Document Files는 자체 형식 설정과 전체 테스트를 사용합니다. `rhwp`가 필요한 HWP 보조 기능은
지원되는 플랫폼에서 서명과 체크섬을 확인하고, 그 밖의 검사는 Cloudflare에 배포하거나 운영
데이터를 읽고 쓰지 않습니다.

## License

[Apache License 2.0](./LICENSE). Runtime dependency는 각 라이선스를 따릅니다. [THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md)를 함께 보십시오.
