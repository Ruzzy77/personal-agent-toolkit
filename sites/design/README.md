# 개인 디자인 라이브러리

소유자의 디자인 레시피, 템플릿과 예시 자산을 찾고 비교하는 비공개 Site입니다. 특정 레시피를
정답처럼 복제하지 않고, 현재 프로젝트의 브랜드와 디자인 시스템을 우선한 뒤 필요한 원리와
재료만 골라 씁니다.

## 구성

- 화면은 `app/`에 있습니다.
- 레시피·패턴·버전 메타데이터는 `services/design`의 D1이 소유합니다.
- 템플릿·CSS·이미지 바이트는 같은 서비스의 비공개 R2가 소유합니다.
- Site는 서버 경로에서만 서비스 토큰을 사용하며 ChatGPT 소유자 인증이 없는 요청에는 자료를
  반환하지 않습니다.
- 공개 저장소와 plugin에는 개인 자산 사본을 넣지 않습니다.

로컬 실행에는 다음 서버 환경 변수가 필요합니다.

```text
DESIGN_SERVICE_URL=https://personal-agent-design.example.workers.dev
DESIGN_SITE_TOKEN=<site-only secret>
```

```bash
npm install
npm run dev
```

검사는 별도 생성물 대조나 자산별 snapshot 없이 lint와 실제 production build만 수행합니다.

```bash
npm run check
```

화면의 탐색·비교·요청 준비 흐름은 선택적으로 WebMCP 도구 세 가지로도 제공됩니다. 자산의 실제
읽기·쓰기는 소유자 인증 Design MCP 도구가 맡습니다. 배포된 Site는
<https://personal-material-index.ruzzy.chatgpt.site>입니다.
