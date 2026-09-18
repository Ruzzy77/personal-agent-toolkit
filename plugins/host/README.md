# Host

Host는 항상 켜진 호스트(Spark)의 작업공간과 샌드박스 실행을 툴킷 MCP 도구 `host_*`로 제공한다. 설계는 [DESIGN.md](./DESIGN.md), 구현은 [`apps/host`](../../apps/host), 클라이언트 쪽 합류는 `services/remote-context/src/host.ts`다.

- 클라이언트는 기존 Personal Agent Toolkit 앱·플러그인 그대로다. 새로 설치할 것이 없다.
- 호스트에는 `personal-agent-host` 하나(prefix `~/.local/share/personal-agent-host`)와 `cloudflared`가 있고, Worker는 Workers VPC Service로 호스트의 `127.0.0.1:18790`에 닿는다.
