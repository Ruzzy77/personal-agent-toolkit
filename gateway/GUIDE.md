# Personal Secure MCP Tunnel

로컬에 설치한 Sense, Corpus와 Hypes를 개인 ChatGPT developer connection에 연결합니다. 제품마다 tunnel과 connection을 하나씩 사용합니다.

## Tunnel profile

OpenAI Platform에서 연결할 제품의 Secure MCP Tunnel을 만든 뒤 tunnel ID를 입력합니다.

```sh
./gateway/launchers/personal-agent-tunnel \
  --sense-tunnel-id tunnel_<sense-id> \
  --hypes-tunnel-id tunnel_<hypes-id> \
  --gateway-base-url http://127.0.0.1:18180 \
  --format shell
```

출력된 `init`과 `doctor` 명령을 실행합니다. Runtime API key는 macOS Keychain의 service `personal-agent-tunnel-control-plane`, account `user`에 둡니다.

## LaunchAgent

선택한 제품이 `personal-agent-toolkit` marketplace에 설치·활성화된 상태에서 실행합니다.

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

Codex marketplace 밖의 package는 제품별 `--product-root product=/absolute/path`로 지정합니다. LaunchAgent 위치는 다음과 같습니다.

```text
~/Library/LaunchAgents/com.ruzzy77.personal-agent-tunnel.gateway.plist
```

상태는 `http://127.0.0.1:18180/healthz`에서 볼 수 있습니다.

## ChatGPT connection

ChatGPT developer settings에서 제품별 app과 tunnel을 연결합니다.

| 제품 | MCP 경로 |
|---|---|
| Sense | `/sense/mcp` |
| Corpus | `/corpus/mcp` |
| Hypes | `/hypes/mcp` |

Gateway를 중단해도 plugin과 데이터는 유지됩니다. Host를 옮길 때에는 기존 서비스를 멈추고 제품 데이터를 이전한 뒤 같은 tunnel ID로 profile을 다시 만듭니다.
