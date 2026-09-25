# Toolkit Web

Flow 작업물을 중심으로 파일·Sense·Corpus·Journal·Library와 UIKit 자료를 이용하는 비공개 화면입니다. 소스는 `sites/context/`, 배포 대상은 소유자의 Cloudflare Worker입니다. 인증 토큰은 서버의 세션 저장소에 두며 브라우저에 전달하지 않습니다.

## UI Kit

공통 자산은 `@personal-agent/ui-kit@1.4.1`입니다. `vendor/personal-agent-ui-kit-1.4.1.tgz`와 잠금 파일로 버전을 고정하고 `react.css`, `document.css`, `react` 진입점을 사용합니다. 공통 CSS 복사본을 별도로 수정·관리하지 않습니다. 상단바는 `.su-appbar > .su-appbar__inner`, 읽기 본문은 문서 프로필을 사용합니다. 작업·관리 화면의 부모 배치는 `su-workspace`, `su-section`, `su-toolbar`, `su-row`, `su-stack`, `su-grid`로 구성합니다.

Flow는 작업공간, 라이브러리, 파일을 하나의 상단바에서 제공합니다. apps/flow/src/work-surface의 버전 고정 패키지로 로컬판과 AppHeader, WorkCanvas, LibraryBrowser 및 읽기 화면을 공유합니다. 주 화면에는 에이전트가 만든 HTML이나 기존 작업물 하나를 표시합니다. 블록 편집 폼, 배치 선택기와 유형별 생성 메뉴는 기본 화면에 두지 않습니다.

라이브러리는 기존 보관본과 범위가 지정된 재사용 자료를 검색하고 읽습니다. Sense, Hypes, Corpus, Journal, 발간물과 UIKit 자료는 원본 식별자로 연결하며 정본 서비스의 읽기 및 권한 절차를 유지합니다. 자료를 열거나 연결하는 것만으로 본문에 넣지 않습니다. 원본 변경, 기준 채택과 발행은 각각 기존 확인 절차를 따릅니다. 파일은 등록된 Host 폴더를 사용하며 자동 복사나 이동은 하지 않습니다.

HTML 작업물은 버전 고정 자산과 함께 별도 샌드박스에서 실행합니다. 앱의 인증 정보와 외부 통신 권한은 넘기지 않습니다. 수정은 기준 버전이 일치할 때 즉시 반영하며 충돌과 되돌리기를 지원합니다. 웹 배포와 Host 연결 갱신은 소스 수정과 별개입니다.

입력·선택·버튼·툴팁·메뉴는 고정 버전의 공식 Apps SDK UI를 `app/ui.tsx`에서 연결합니다. 아이콘은 Lucide 원본입니다. 화면별 스타일은 내용 배치만 담당하며 공통 상단바의 높이·좌우 여백을 덮어쓰지 않습니다. 상단바의 보조 동작은 더 보기 안에 둡니다. 목록과 본문 안의 도구는 기능·빈도·대상에 맞게 배치하며 일괄 메뉴화하지 않습니다. 저장·복구·삭제 확인은 기존 기능과 권한을 유지합니다.

## 읽기와 편집

본문 중심 화면에서 상단 탐색 버튼으로 자료·목차를 엽니다. 자료 패널 안에서 Sense 또는 프로젝트를 검색해 고르고 문서를 엽니다. 검색창은 선택한 자료 범위 바로 아래에 두며, 목록을 스크롤해도 범위와 검색창은 상단에 유지됩니다. 넓은 화면은 겹쳐 여는 측면 패널, 좁은 화면은 전체 폭 패널을 사용하며 선택 후 본문으로 돌아옵니다. 제목 없는 Context 항목은 두 줄 발췌로 표시하고 원문이나 제목은 변경하지 않습니다. 목록은 식별자·제목·버전 중심이며 전문은 선택한 문서만 읽습니다. 프로젝트 검색은 정본문서·Context 항목·Source 후보를 구분합니다. Source는 읽기 전용입니다.

읽기 화면에는 문서 제목과 본문을 한 번씩 표시합니다. 스킬은 본문 첫 제목이 있으면 화면 제목으로 사용하되 저장된 이름은 바꾸지 않습니다. 읽는 화면의 소제목·문단·목록·표 셀·코드에 바로 커서를 놓고 수정합니다. 선택한 부분에만 얇은 테두리를 표시하며, 별도 편집 모드나 섹션별 반복 버튼은 두지 않습니다. 문서 구조·메타데이터는 더 보기의 마크다운 편집에서 고칩니다. 스킬은 이름·설명·본문, 정본문서는 제목·본문·적용 범위를 편집합니다. 출처와 승인 provenance 등 편집하지 않은 메타데이터는 저장 직전 정본에서 확인해 보존합니다. 철회된 출처를 다른 자료로 대체하거나 빠뜨려 저장하지 않습니다.

직접 편집은 해당 부분의 Markdown 범위만 바꾸고 나머지 원문 바이트, 링크 목적지와 서식은 보존합니다. 붙여넣기는 일반 텍스트로 받습니다. 입력 중에는 문단을 다시 덮어쓰지 않아 커서와 한글 조합을 유지하며, 해당 부분의 편집을 마치면 서식을 다시 맞춥니다. 원시 HTML·이미지 등이 섞인 부분은 마크다운 편집으로 다룹니다.

수정안은 현재 탭에 보관되며 자동으로 정본에 저장하지 않습니다. 저장 버튼은 정본 반영 요청이며 Codex 대화를 거칠 필요가 없습니다. 저장 단위는 Sense 섹션 묶음, 프로젝트 Context 항목 묶음, 개별 스킬 또는 개별 정본문서입니다. 미저장 수정안이 있을 때 나타나는 상단 저장은 모든 수정안에 적용하며 접근성 이름과 툴팁도 같은 범위를 표시합니다. 한 단위가 실패해도 다른 초안은 보존합니다. 같은 Context의 20개 초과 수정은 앞선 저장 응답의 버전을 이어받고 남은 원본 내용을 대조합니다. 다른 변경이 개입하면 해당 Context의 후속 저장을 중단하며 무조건 재시도하지 않습니다.

저장 전 정본 식별자·원본 내용·버전을 확인하고, 기존 서비스의 버전 검사로 저장한 뒤 재조회합니다. 재조회 결과와 제출 초안이 일치할 때만 저장 완료를 알립니다. 정상 상태의 정본 라벨은 반복 표시하지 않습니다. 상태 문구 한 줄의 자리는 미리 확보해 미저장 표시가 나타나거나 사라져도 본문이 움직이지 않게 합니다. 저장 중 생긴 새 초안이나 정규화 차이는 비교 대상으로 남깁니다. 직전 문서 복원도 현재 버전을 대조한 새 저장이며, 미저장 초안이 있으면 먼저 처리해야 합니다. 이전 버전 응답은 조회 당시 문서·버전·요청 순서가 여전히 맞을 때만 표시하며, 다른 문서를 열거나 비교를 닫으면 무효화합니다.

가져오기는 자료 탐색 패널에, 다시 불러오기·직전 저장본·원본 정보·수정안 복사·화면 모드는 더 보기 안에 둡니다. 이름·설명·적용 범위는 마크다운 편집에서 고칩니다. 원본 버전과 실행 적용 확인은 원본 정보에서 구분합니다. 비교는 넓은 화면에서 나란히, 좁은 화면에서 위아래로 표시합니다.

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

`app/api/context/[operation]/route.ts`는 소유자 세션과 요청 출처를 검증하고 허용된 작업만 기존 Context 서비스로 전달합니다. 변경 요청에는 CSRF 검사를 적용합니다. 브라우저에는 세션 쿠키만 전달하고 서비스 접근·갱신 토큰은 서버에 둡니다.

`wrangler.example.jsonc`의 `APP_ORIGIN`, `AUTH_ISSUER`, `CONTEXT_SERVICE_URL`, `TOOLKIT_RESOURCE`, `OAUTH_CLIENT_ID`를 소유자 환경에 맞추고 `WEB_SESSION_KV`를 연결합니다. 실제 `wrangler.jsonc`와 비밀 값은 저장소에 포함하지 않습니다.

UIKit 조회는 `UIKIT` 서비스 바인딩의 `ArchiveReader`를 사용합니다. UIKit의 `READ_CONSUMERS`에 Toolkit의 정확한 resource audience와 읽기 scope를 등록해야 합니다. `/api/uikit/search`, `read`, `preview`는 인증 후 같은 발행본을 읽으며, 미리보기는 네트워크·상위 화면 접근이 차단된 프레임에서 실행합니다. 발행본을 고정한 참조는 원본 파일을 Flow에 복사하지 않습니다.

MCP와 웹은 같은 소유자·범위·버전 검사 경로를 사용합니다. 서비스가 없으면 정본 조회·저장이 실패하며 탭의 초안은 유지됩니다. 일반 파일 도구의 OS 권한을 격리하는 기능은 아닙니다.

## 통합 관리

Workspace의 **더 보기 → 관리**에서 Corpus, Sense, Library와 Design을 전환합니다.
`/manage`는 Corpus, `/manage/sense`, `/manage/library`, `/manage/design`은 각 제품의 관리 화면입니다.
Library의 읽기·편집 화면과 저장 방식은 유지합니다. Design 관리는 기존 자료의 복구 절차를 위한 경로이며 새 디자인 자료는 UIKit에서 다룹니다. `/design`의 이전 북마크는 소유자 인증 후 UIKit 갤러리로 이동합니다.

관리 요청은 `/api/management/<operation>`에서 기존 Workspace의 소유자 세션과 서버 보관 접근 토큰으로 통합 Worker의 `/admin/v1/<operation>`을 호출합니다. 소유자 연결과 제품별 작업 정의를 재사용하며, 별도 Site·저장소·인증 토큰을 만들지 않습니다. 이 경로는 관리 화면에 필요한 작업만 허용하고 본문 개정·원본 파일 쓰기·Hypes·Journal 작업은 노출하지 않습니다.

## 소스와 배포

- `app/page.tsx`: Flow로 이동
- `app/flow/page.tsx`, `components/flow`: 공통 상단바와 단일 주 작업물, 라이브러리 및 파일 탐색, HTML 실행과 수정안 비교
- `app/files/page.tsx`: 기존 작업공간 파일 탐색. 보고 있는 파일을 Flow의 참고 자료로 연결할 수 있습니다.
- `components/flow/flow-resource-link.tsx`: 원본 화면에서 Flow로 여는 공통 링크. 자료 식별자 검사는 `@personal-agent/flow-surface/resource-reference`를 사용합니다.
- `app/context/page.tsx`: Sense·Corpus 읽기·편집·비교 화면
- 화면 아이콘: 고정 버전 `lucide-react`의 개별 아이콘을 사용하며, 버튼의 접근성 이름과 공통 크기·선 굵기를 유지합니다. 배포 라이선스는 `public/lucide-license.txt`에 포함합니다.
- `lib/context.ts`: 목록·선택 조회와 정본 저장 어댑터
- `lib/guidance.ts`: 탭 초안·원본 충돌·저장 확인
- `app/inline-document.tsx`, `lib/inline-markdown.ts`: 직접 편집과 원문 범위 보존
- `lib/webmcp.ts`, `contracts/webmcp-contract.json`: 기존 페이지 계약
- `fixtures`, `schemas`, `contracts/mcp-contract.json`, `lib/prototype-data.ts`: 실행하지 않는 과거 Resolver 예시. 기존 변경은 보존합니다.

`npm test`, `npm run lint`, `npx tsc --noEmit`, `npm run build`로 확인한 뒤 기존 Worker에 `npx wrangler deploy`로 배포합니다. Vite가 생성한 `dist/server/wrangler.json`을 사용하므로 `UIKIT` 등 원본 구성의 바인딩을 유지해야 합니다. `.env`, `.dev.vars`, 개인 정본·복구본은 배포 자산에 포함하지 않습니다. Flow 저장 계약이 바뀐 경우 공통 패키지·Host·Context 서비스를 맞춰 배포합니다.
