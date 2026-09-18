# Host

Host는 항상 켜진 호스트(Spark)의 작업공간과 샌드박스 실행을 툴킷 MCP 도구 `host_*`로 제공한다. 설계는 [DESIGN.md](./DESIGN.md), 구현은 [`apps/host`](../../apps/host), 클라이언트 쪽 합류는 `services/remote-context/src/host.ts`다.

- 클라이언트는 기존 Personal Agent Toolkit 앱·플러그인 그대로다. 새로 설치할 것이 없다.
- 호스트에는 `personal-agent-host` 하나(prefix `~/.local/share/personal-agent-host`)와 `cloudflared`가 있고, Worker는 Workers VPC Service로 호스트의 `127.0.0.1:18790`에 닿는다.

## 도구

`host_capabilities`, `host_roots`, `host_search`, `host_read`, `host_write`, `host_exec`, `host_job`, `host_job_cancel`. 통합 툴킷 MCP(`/mcp`)에 포함되고, Claude Code·Aside처럼 제품별 URL을 쓰는 클라이언트는 `/host/mcp`를 등록한다(`.mcp.json`). 계약은 DESIGN.md 4절.

## 호스트 설치

```sh
apps/host/scripts/install-linux.sh            # uv, venv 2개, cloudflared, launcher, 샌드박스 이미지, upstream 토큰
$EDITOR ~/.local/share/personal-agent-host/config/host.toml   # apps/host/config.example.toml 참고
# tunnel 토큰을 config/tunnel.token 에 두고
~/.local/share/personal-agent-host/bin/personal-agent-host install   # systemd user unit 4개
```

`personal-agent-host status`는 유효 설정을, `backup`은 `[host.backup]` 대상으로 즉시 백업을, `uninstall [--purge]`는 unit 해제(와 prefix 삭제)를 수행한다.
