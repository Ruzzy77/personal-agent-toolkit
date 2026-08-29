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

## 제품 버전 갱신

Sense, Corpus 또는 Hypes의 버전을 올린 뒤에는 Codex에 설치된 세 plugin을 먼저 갱신합니다. 이어 기존 LaunchAgent 설치 명령을 같은 tunnel client와 제품 목록으로 다시 실행합니다. 설치 프로그램이 새 Codex plugin 경로를 확인해 LaunchAgent 구성을 교체하며, 사용자 데이터와 tunnel ID는 바꾸지 않습니다. 새 구성을 실제 실행 중인 프로세스에 적용합니다.

```sh
launchctl kickstart -k \
  "gui/$(id -u)/com.ruzzy77.personal-agent-tunnel.gateway"
curl --fail --silent --show-error \
  http://127.0.0.1:18180/healthz
```

상태 응답에서 선택한 제품이 모두 준비됐는지 확인합니다. ChatGPT 웹에서는 `플러그인 → 설치됨 → Sense·Corpus·Hypes → 관리`로 이동해 각 developer plugin을 `새로 고침`합니다. 액션 목록이 현재 MCP 도구와 일치하고 권한이 `모든 액션 허용`인지 확인합니다. 새로 고침으로 액션이 바뀌지 않으면 해당 developer plugin을 다시 연결하고 같은 endpoint를 사용합니다.

## ChatGPT connection

ChatGPT developer settings에서 제품별 app과 tunnel을 연결합니다.

| 제품 | MCP 경로 |
|---|---|
| Sense | `/sense/mcp` |
| Corpus | `/corpus/mcp` |
| Hypes | `/hypes/mcp` |

Gateway를 중단해도 plugin과 데이터는 유지됩니다. Host를 옮길 때에는 기존 서비스를 멈추고 제품 데이터를 이전한 뒤 같은 tunnel ID로 profile을 다시 만듭니다.
