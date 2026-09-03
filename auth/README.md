# Personal Agent Auth

Personal Agent Toolkit의 여러 원격 서비스가 소유자 인증을 함께 쓰도록 만드는 Cloudflare Workers용 구성입니다. 서비스별 자료 저장소와 실행 코드는 분리한 채 인증만 공유합니다.

현재 폴더에는 Google 소유자 로그인과 다중 리소스 OAuth 계약이 구현되어 있습니다. 실제 Cloudflare 자원, Google OAuth client와 운영 자격 증명은 포함하지 않습니다.

## 인증 구조

Personal Agent Auth는 ChatGPT 같은 OAuth client에는 OAuth authorization server로 동작하고, Google에는 OpenID Connect client로 동작합니다.

1. OAuth client가 리소스 URI와 권한을 지정해 `/authorize`를 엽니다.
2. 서버가 등록된 리소스와 허용 권한을 검사한 뒤 Google authorization code flow로 전환합니다.
3. 소유자는 Google에서 계정을 선택하고 정보 제공에 동의합니다.
4. callback에서 Google ID token의 서명, issuer, audience, 만료와 nonce를 검증합니다.
5. Google `sub` 또는 검증된 이메일이 소유자 허용 목록에 있을 때만 OAuth grant를 발급합니다.

Browser binding cookie와 만료되는 state를 사용하며 Google 로그인에도 PKCE S256을 적용합니다. Google access token과 refresh token은 저장하지 않습니다.

Cloudflare OAuth Provider는 불투명 토큰과 grant를 `OAUTH_KV`에 저장합니다. `AuthService.validateAccessToken()`은 같은 인증 Worker 안에서 토큰을 풀고, 호출 서비스가 요구한 리소스와 권한을 다시 검사합니다. 리소스 Worker는 `validateBearerRequest()`로 Authorization 헤더를 읽습니다. 전체 요청이나 서비스 자료는 인증 Worker에 보내지 않습니다.

## 호스팅 경계

비공개 `AuthService` RPC는 같은 Cloudflare 계정의 Worker가 Service Binding으로 호출할 때 사용합니다. 따라서 Library 화면이 Sites에 있어도 ChatGPT가 호출할 `/api/mcp`는 Cloudflare MCP Worker에 두는 구성을 기본안으로 삼습니다.

```text
ChatGPT ──OAuth──▶ Personal Agent Auth Worker
   │                        ▲
   │                        │ private Service Binding
   └────MCP request────▶ Library MCP Worker

Browser ──────────────▶ Library Sites frontend
```

Sites 화면과 Library MCP Worker는 서로 다른 역할을 맡습니다. `services/library`의 Worker는
비공개 `AuthService`로 사용자를 확인하고 Sites의 문서·이미지 API에 요청을 전달합니다. Sites
안에 별도 MCP endpoint를 두거나 공개 HTTPS token inspection을 추가하지 않습니다.

## 운영 설정

[`wrangler.example.jsonc`](./wrangler.example.jsonc)를 `wrangler.jsonc`로 복사한 뒤 다음 값을 운영 환경에 맞춥니다. `wrangler.jsonc`는 실제 resource URI를 담을 수 있으므로 Git에서 제외합니다.

- `AUTH_ISSUER`: 배포 뒤에도 바뀌지 않는 HTTPS origin
- `RESOURCE_REGISTRY_JSON`: [`resources.example.json`](./resources.example.json) 형식의 리소스와 권한
- `OAUTH_KV`: OAuth grant와 흐름 state를 보관할 KV namespace

Worker secret에는 다음 값을 넣습니다.

- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `OWNER_GOOGLE_SUBS`: 허용할 Google subject. 여러 값은 쉼표나 줄바꿈으로 구분합니다.
- `OWNER_GOOGLE_EMAILS`: 초기 설정에 쓸 수 있는 검증된 Google 이메일 허용 목록
- `OWNER_ID`: 여러 리소스와 Sync 앱이 함께 쓰는 `owner_<32자리 hex>` 형식의 불투명 소유자 ID. 새 통합 배포에서는 Auth와 Context Worker에 같은 값을 설정합니다.

Google OAuth client의 승인된 redirect URI는 다음 주소와 정확히 같아야 합니다.

```text
${AUTH_ISSUER}/oauth/google/callback
```

초기 연결에는 이메일 허용 목록을 쓸 수 있습니다. Google `sub`를 확인한 뒤에는 `OWNER_GOOGLE_SUBS`를 기준으로 운영하는 편이 계정 식별에 더 안정적입니다. 자격 증명과 소유자 식별자는 `.env`, `.dev.vars`, `wrangler.jsonc`나 공개 저장소에 기록하지 않습니다.

## 로컬 시험

Node.js 24 이상에서 실행합니다.

```sh
npm install
npm run check
```

시험은 다음 범위를 검증합니다.

- 리소스 URI와 scope 격리
- PKCE S256과 액세스 토큰 폐기
- Service Binding을 통한 bearer token 검증
- Google authorization code, ID token과 소유자 허용 목록
- authorization 요청, callback browser binding과 Google 거절 흐름

Google token endpoint와 JWKS는 시험 안에서 대체합니다. 실제 Google 계정이나 Cloudflare 자원을 사용하지 않습니다.

## 배포 순서

1. 고정 issuer를 정합니다.
2. Google OAuth web client를 만들고 정확한 callback URI를 등록합니다.
3. Cloudflare KV namespace와 인증 Worker를 만듭니다.
4. Google 자격 증명과 소유자 허용 목록을 Worker secret으로 등록합니다.
5. 인증 Worker를 배포하고 OAuth metadata와 Google callback을 확인합니다.
6. Library, Journal과 Personal Agent Context Worker에 `AuthService` Service Binding을 연결합니다.
7. 각 리소스의 읽기 scope로 먼저 연결한 뒤 필요한 쓰기 scope를 별도로 확인합니다. Context Worker에서는 Sense·Corpus·Hypes가 서로 다른 resource URI와 scope를 사용하므로 한 리소스의 토큰으로 다른 리소스를 열 수 없습니다.

각 제품의 데이터와 서비스 코드는 인증 저장소와 분리하며, `plugins`, `services`와 `sites`
아래의 제품별 경계를 따릅니다.
