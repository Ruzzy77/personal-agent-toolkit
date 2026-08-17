# Personal Secure MCP Tunnel

이 gateway는 로컬에 설치된 Sense, Corpus와 Hypes 가운데 선택한 제품을 개인용 ChatGPT
developer connection에 연결한다. 로컬 전용 사용자는 gateway를 설치할 필요가 없다.

## 1. tunnel profile 만들기

OpenAI Platform에서 사용할 제품마다 Secure MCP Tunnel을 하나 만든다. 생성된 tunnel ID를
다음 명령에 넣는다. 선택하지 않을 제품의 인자는 생략한다.

```sh
./gateway/launchers/personal-agent-tunnel \
  --sense-tunnel-id tunnel_<sense-id> \
  --hypes-tunnel-id tunnel_<hypes-id> \
  --gateway-base-url http://127.0.0.1:18180 \
  --format shell
```

출력된 `init`과 `doctor` 명령을 실행한다. runtime API key는 명령이나 profile 파일에 넣지
말고 macOS Keychain의 service `personal-agent-tunnel-control-plane`, account `user`에 둔다.

## 2. 하나의 LaunchAgent 설치

선택한 제품은 `personal-agent-toolkit` marketplace에 설치되고 활성화되어 있어야 한다.

```sh
GATEWAY_ROOT=/absolute/path/to/personal-agent-toolkit/gateway
TUNNEL_CLIENT=/absolute/path/to/tunnel-client

"$GATEWAY_ROOT/launchers/personal-agent-tunnel-service" \
  install-gateway-launch-agent \
  --runtime-program "$GATEWAY_ROOT/launchers/personal-agent-tunnel-service" \
  --gateway-program "$GATEWAY_ROOT/launchers/personal-agent-tunnel-gateway" \
  --tunnel-client "$TUNNEL_CLIENT" \
  --product sense \
  --product hypes
```

Codex에 등록하지 않은 동등한 로컬 패키지는 선택한 제품마다
`--product-root product=/absolute/path`를 지정할 수 있다. Gateway는
`~/Library/LaunchAgents/com.ruzzy77.personal-agent-tunnel.gateway.plist` 하나만 쓴다.
API key는 plist에 저장하지 않는다.

LaunchAgent를 시작한 뒤 `http://127.0.0.1:18180/healthz`에서 선택한 제품이 표시되는지
확인한다.

## 3. ChatGPT 연결

ChatGPT developer settings에서 제품별 app을 만들고 해당 제품의 tunnel을 선택한다.
각 연결은 하나의 제품 경로만 사용한다.

- Sense: `/sense/mcp`
- Corpus: `/corpus/mcp`
- Hypes: `/hypes/mcp`

Gateway를 중단해도 로컬 plugin과 데이터는 지워지지 않는다. 다른 기기로 옮길 때에는 기존
host를 먼저 중단하고 제품 데이터와 Corpus source 등록을 각 제품의 절차에 따라 옮긴 뒤 같은
tunnel ID로 gateway profile을 다시 만든다. 같은 제품 tunnel을 두 host에서 동시에 실행하지
않는다.
