# Workspace — Sense · Corpus

Sense 기준·연결 스킬과 Corpus 프로젝트의 정본문서를 읽고 편집하는 비공개 화면입니다. 소스 정본은 Toolkit의 `sites/context/`이며, 기존 Site의 배포 대상과 접근 범위를 유지합니다. 화면에 별도 지침 DB나 사용자 인증 정보를 저장하지 않습니다.

## 읽기와 편집

본문 중심 화면에서 상단 탐색 버튼으로 자료·목차를 엽니다. 자료 패널 안에서 Sense 또는 프로젝트를 검색해 고르고 문서를 엽니다. 넓은 화면은 겹쳐 여는 측면 패널, 좁은 화면은 전체 폭 패널을 사용하며 선택 후 본문으로 돌아옵니다. 제목 없는 Context 항목은 두 줄 발췌로 표시하고 원문이나 제목은 변경하지 않습니다. 목록은 식별자·제목·버전 중심이며 전문은 선택한 문서만 읽습니다. 프로젝트 검색은 정본문서·Context 항목·Source 후보를 구분합니다. Source는 읽기 전용입니다.

본문은 섹션별로 고칩니다. 스킬은 이름·설명·본문, 정본문서는 제목·본문·적용 범위를 편집합니다. 출처와 승인 provenance 등 편집하지 않은 메타데이터는 저장 직전 정본에서 확인해 보존합니다. 철회된 출처를 다른 자료로 대체하거나 빠뜨려 저장하지 않습니다.

수정안은 현재 탭에 보관됩니다. 저장 버튼은 정본 반영 요청이며 Codex 대화를 거칠 필요가 없습니다. 저장 단위는 Sense 섹션 묶음, 프로젝트 Context 항목 묶음, 개별 스킬 또는 개별 정본문서입니다. 상단 저장은 모든 수정안에 적용합니다. 한 단위가 실패해도 다른 초안은 보존합니다. 같은 Context의 20개 초과 수정은 앞선 저장 응답의 버전을 이어받고 남은 원본 내용을 대조합니다. 다른 변경이 개입하면 해당 Context의 후속 저장을 중단하며 무조건 재시도하지 않습니다.

저장 전 정본 식별자·원본 내용·버전을 확인하고, 기존 서비스의 버전 검사로 저장한 뒤 재조회합니다. 재조회 결과와 제출 초안이 일치할 때만 저장됨으로 표시합니다. 저장 중 생긴 새 초안이나 정규화 차이는 비교 대상으로 남깁니다. 직전 문서 복원도 현재 버전을 대조한 새 저장이며, 미저장 초안이 있으면 먼저 처리해야 합니다. 이전 버전 응답은 조회 당시 문서·버전·요청 순서가 여전히 맞을 때만 표시하며, 다른 문서를 열거나 비교를 닫으면 무효화합니다.

운영 설정에 Workspace 주소가 있으면 원격·로컬 Sense overview에서 같은 화면으로 이동할 수 있습니다. 별도 로컬 Sense HTML은 동일한 읽기 조판과 Lucide 아이콘을 사용하는 읽기 전용 보기이며 자동 이동하지 않습니다.

기본 지침이 저장됐다는 사실은 Codex 실행에 적용됐다는 뜻이 아닙니다. 원격 정본의 관리용 배포본을 갱신하고 새 실행을 확인하는 절차는 Toolkit 관리 스킬을 따릅니다. 일반 지침 조회에는 화면이 필요하지 않습니다.

## Codex와 함께 사용

기존 탭의 자료와 미저장 초안을 먼저 확인합니다. 탭을 초기화하거나 다른 자료로 조용히 대체하지 않습니다. 실제 정본을 읽고 `GuidanceSource`의 정확한 식별자·위치·버전·내용·권한·canonical locator를 전달합니다. 제목으로 대상을 추측하지 않습니다.

일곱 WebMCP 도구는 기존 이름을 유지합니다:

- `load_guidance_sources`
- `list_guidance_sources`
- `read_guidance_source`
- `open_guidance_section`
- `update_guidance_draft`
- `get_pending_guidance_changes`
- `acknowledge_guidance_saved`

이 도구들은 탭 안에서만 작동합니다. 에이전트 저장은 사용자의 요청 범위에서 기존 Sense·Corpus 도구로 수행한 뒤 정본을 재조회하고, 제출 당시 `expectedVersion`·`draftId`로 확인합니다. 늦은 확인에 맞춰 새 `draftId`를 끼워 넣지 않습니다. 저장되지 않은 내용을 저장됐다고 확인하지 않습니다.

WebMCP가 없어도 화면의 직접 조회·저장과 JSON 가져오기·수정안 복사가 작동합니다. 연결 없는 로컬 파일은 정확한 바이트 SHA-256을 사용하고 기존 파일 도구로 저장합니다. 설치 캐시와 base 관리 배포본은 수동 편집 정본이 아닙니다.

## 서비스 연결

`app/api/context/[operation]/route.ts`는 Sites의 인증된 사용자 헤더를 서버에서 읽고 허용한 작업만 기존 Context 서비스에 전달합니다. 같은 출처의 요청만 받으며 브라우저에 내부 토큰을 전달하지 않습니다.

Site runtime 설정: `CONTEXT_SERVICE_URL`, 비밀 `CONTEXT_SITE_TOKEN`.
기존 Context service 설정: 비밀 `CONTEXT_SITE_TOKEN`, `CONTEXT_SITE_USER_ID`, `CONTEXT_SITE_OWNER_ID`. Site별 검증된 사용자 식별자를 소유자에 명시적으로 연결합니다. 다른 Site의 사용자 ID나 이메일에서 추측하지 않습니다. `/api/identity`는 현재 인증된 방문자 자신의 Site ID만 반환합니다.

MCP와 Site는 같은 서비스·소유자·범위·버전 검사 경로를 사용합니다. 서비스가 없으면 정본 조회·저장이 실패하며 탭의 초안은 유지됩니다. 일반 파일 도구의 OS 권한을 격리하는 기능은 아닙니다.

## 소스와 배포

- `app/page.tsx`: 읽기·목차·편집·비교 화면
- 화면 아이콘: 고정 버전 `lucide-react`의 개별 아이콘을 사용하며, 버튼의 접근성 이름과 공통 크기·선 굵기를 유지합니다. 배포 라이선스는 `public/lucide-license.txt`에 포함합니다.
- `lib/context.ts`: 목록·선택 조회와 정본 저장 어댑터
- `lib/guidance.ts`: 탭 초안·원본 충돌·저장 확인
- `lib/webmcp.ts`, `contracts/webmcp-contract.json`: 기존 페이지 계약
- `fixtures`, `schemas`, `contracts/mcp-contract.json`, `lib/prototype-data.ts`: 실행하지 않는 과거 Resolver 예시. 기존 변경은 보존합니다.

`npm run lint`, `npx tsc --noEmit`, `npm run build`로 확인합니다. Toolkit의 기존 Site export 도구로 별도 배포 checkout을 만들며, Toolkit 저장소 전체를 Site 저장소에 올리지 않습니다. `.env`, `.dev.vars`, 개인 정본·복구본은 export에 포함하지 않습니다. 같은 `.openai/hosting.json`의 Site에만 비공개 배포합니다.
