# Host 설계 v1.0

Spark의 작업공간과 실행을 툴킷 MCP 도구 여덟 개로 제공한다. 플랫폼 하네스는 클라이언트에 남고 Spark에는 파일과 실행만 둔다. 클라이언트는 기존 Personal Agent Toolkit 앱·플러그인 그대로이며 새로 설치할 것이 없다.

## 1. 구성

```
[Spark-A]  ~/Agent-Workspace          /mnt/nas/works (NFS ro)   /mnt/nas/backup (NFS rw)
  personal-agent-host.service   Sync + Host MCP (HTTP 127.0.0.1:18790)
  personal-agent-tunnel.service cloudflared (outbound, 공개 호스트명 없음)
  personal-agent-backup.service/.timer
  docker                        personal-agent-host-sandbox:1

[Cloudflare]  Tunnel spark-host → Workers VPC Service spark-host (http, 127.0.0.1:18790)
              remote-context Worker /mcp 에 host_* 도구 합류, 기존 OAuth에 host.read·host.write 추가

[클라이언트]  기존 앱·플러그인. 도구 목록 새로 고침과 scope 재동의 한 번.
```

Spark로 들어오는 포트는 없다. 관리용 SSH는 그대로 두되 별도의 MCP 전송 경로는 만들지 않는다.

## 2. Worker adapter

- `services/remote-context/src/mcp.ts`에 `registerHostTools(server, env, principal)`을 추가한다. 도구 8개의 schema·annotation을 정적으로 등록하고, 핸들러는 도구 이름과 arguments만으로 내부 `tools/call` 요청을 만들어 `env.HOST_VPC.fetch("http://spark-host/mcp", …)`로 보낸다.
- Worker–Host 구간은 MCP `2026-07-28` 무세션 호출로 고정한다. initialize·세션 저장소 없음. 헤더는 `Authorization: Bearer <host-upstream.token>`, `Content-Type: application/json`, `Accept: application/json, text/event-stream`, `MCP-Protocol-Version: 2026-07-28`, `Mcp-Method: tools/call`, `Mcp-Name: <도구명>`이며, 본문 `params._meta`에 `io.modelcontextprotocol/protocolVersion`, `io.modelcontextprotocol/clientInfo`, `io.modelcontextprotocol/clientCapabilities`를 넣는다. 헤더와 본문 값은 일치시킨다. 외부 클라이언트의 세션 ID·버전 헤더·Origin·OAuth bearer는 복사하지 않는다.
- 응답은 JSON-RPC 봉투를 벗겨 `content`·`structuredContent`·`isError`를 그대로 도구 결과로 돌려준다. Host 미응답·upstream 인증 실패는 그 도구의 오류(`isError=true`)이며 다른 제품 도구에 영향이 없다. 자동 재전송 없음.
- scope: `host_write`·`host_exec`·`host_job_cancel`은 `host.write`, 나머지 다섯은 `host.read`. `RESOURCE_SCOPES.toolkit`과 auth Worker의 scope 묶음에 둘을 추가한다. Host에는 principal·scope를 전달하지 않는다. Host는 단일 소유자다.
- `products.json`에 제품 `host`(도구 8개)를 추가한다. `check_repository.py`는 `apps/host`의 Python 소스를 `ast`로 읽어 `@mcp.tool(name="host_…")` decorator의 이름을 추출하고, products.json ↔ `registerHostTools` 등록 이름 ↔ Python decorator 이름 세 곳이 같은 8개인지 대조한다.
- wrangler: `vpc_services: [{ binding: "HOST_VPC", service_id: … }]`, secret `HOST_UPSTREAM_TOKEN`.

## 3. Host 서버

- 패키지 `apps/host`(`personal-agent-host`). `apps/sync`를 라이브러리로 쓰고 Corpus 격리 runtime을 같은 subprocess JSON 계약으로 부른다.
- 프로세스 하나. `MCPServer`(`mcp` 2.0.0)의 `streamable_http_app()`을 ASGI로 올리고, 최상위 lifespan에서 MCP session manager와 Sync 루프·job 관리를 함께 시작한다. `mcp.run()`은 쓰지 않는다. 설정은 `stateless_http=True, json_response=True, transport_security=TransportSecuritySettings(allowed_hosts=["spark-host"])`.
- HTTP 인증은 `config/host-upstream.token` bearer 하나. Sync의 device 자격은 `config/sync-device.token`.
- 외부 프로세스 호출(Corpus helper, rg, docker)은 모두 비동기 subprocess다.

prefix `~/.local/share/personal-agent-host/`: `bin/`(launcher, uv, cloudflared), `runtimes/`(host venv, corpus venv), `config/`(host.toml, 토큰 2개, tunnel.json), `state/`, `jobs/`, `logs/`.

## 4. 도구

공통: `root`는 등록된 root ID, 경로는 root 기준 상대 경로(≤4,096 B), ID·version 문자열 ≤256 B. 문자열 크기는 UTF-8 byte. 요청 전체 ≤4 MiB. 실패는 `isError=true`. `status`는 `queued | running | succeeded | failed | cancelled | timed_out`. `?`는 선택.

| 도구 | 입력 | 출력 | 한도·동작 |
|---|---|---|---|
| `host_capabilities` | 없음 | `version`, `limits{read_bytes, write_bytes, job_output_bytes, timeout_s, wait_s}` | 2 MiB, 2 MiB, 256 KiB, 21600, 50. 도구 목록·root 정책은 반환하지 않음 |
| `host_roots` | 없음 | `roots[{id, permission, execute, sources[]}]` | permission: read_only/create_only/read_write, execute: none/sandbox. 절대 경로 없음 |
| `host_search` | `root`, `paths?[]`(기본 `["**/*"]`, ≤32), `pattern`(정규식 ≤4,096 B), `max_results?`(기본 100, ≤500), `context?`(기본 2, ≤5) | `matches[{path, line, text, before[], after[]}]`, `truncated` | 10초, 발췌 합계 256 KiB. 한도 도달 시 부분 결과 + truncated |
| `host_read` | `root`, `files[{path, start_line?=1, end_line?}]`(1~32), `max_bytes?`(기본 65,536, ≤2 MiB) | `files[{path, content, version, start_line, end_line}]`, `truncated` | UTF-8 텍스트만. 행 번호 1부터 양 끝 포함. 행 단위로 자르고 한 행이 한도를 넘으면 오류. version은 파일 전체 기준 |
| `host_write` | `root`, `path`, `content?` 또는 `replace?{start_marker, end_marker, content}` 또는 `delete?`, `expected_version?` | `path`, `version` | 셋 중 정확히 하나. 결과 파일 ≤2 MiB, marker 각 1~4,096 B, 각각 한 번만 등장, marker는 남기고 사이만 교체. 부모 폴더 자동 생성. `"absent"`는 신규 생성 전용, 생략하면 버전 비교 없이 적용(권한 검사는 그대로). 삭제 후 version은 `"absent"`. 디렉터리 삭제 없음 |
| `host_exec` | `root`, `cwd?="."`, `argv?[]` 또는 `shell?`, `stdin?`, `timeout_s?`(기본 1800, ≤21,600), `wait_s?`(기본 5, ≤50) | `job_id`, `status`, `exit_code`(null 가능), `stdout_tail`, `stderr_tail`, `truncated` | argv/shell 중 하나. argv ≤256개·각 ≤16 KiB·합계 ≤64 KiB, stdin ≤1 MiB 후 EOF. 꼬리 스트림별 32 KiB. 짧게 끝나도 job_id 반환. 대기 중이면 `queued` |
| `host_job` | `job_id`, `stream?="stdout"`, `offset?=0`, `limit?=65,536`(≤262,144) | `job_id`, `status`, `exit_code`, `stream`, `output`, `next_offset`, `eof`, `truncated` | offset은 저장된 UTF-8 로그의 byte 위치. limit 0이면 상태만. 문자 경계에서 잘라 next_offset으로 이어 읽음. eof는 job 종료와 출력 끝을 모두 만족할 때 |
| `host_job_cancel` | `job_id` | `job_id`, `status` | queued면 대기열 제거, running이면 컨테이너 종료 후 cancelled. 종료된 job은 기존 상태 |

- 읽기 도구 다섯은 `readOnlyHint`. 응답이 돌려준 경로는 다음 입력에 그대로 쓴다.
- `host_write`는 Corpus Work 계약(권한 검사, 원자 교환, 직전본 복구)으로 쓴다. `expected_version` 생략은 helper까지 그대로 전달해 버전 비교만 건너뛴다. 사전 읽기·capabilities 호출을 쓰기의 필수 단계로 두지 않는다. 일반적인 쓰기는 `root, path, content` 세 필드로 끝난다.
- `host_exec`는 Work 역할이 있고 `permission=read_write`, `execute=sandbox`인 root에서만 허용한다. 컨테이너가 `/workspace`에 직접 쓰며 Corpus의 version·복구는 적용되지 않는다. Source 겸용 root의 변경은 Sync가 재추출한다.

## 5. 실행

```
docker create -i --name pah-<job-id> --user <uid>:<gid>
  --cap-drop ALL --security-opt no-new-privileges --network none
  --memory 8g --cpus 4 --pids-limit 512
  --read-only --tmpfs /tmp:rw,size=1g
  --log-opt max-size=64m --log-opt max-file=2
  -v <root>:/workspace  -v <허용 Source>:/sources/<id>:ro …
  -w /workspace/<cwd>  personal-agent-host-sandbox:1 <argv>
docker start -ai pah-<job-id>      # 비동기 subprocess. stdin 전달 후 EOF, TTY 없음
```

- `shell`은 컨테이너 안에서 `sh -c`. 네트워크는 `none` 고정.
- `timeout_s`는 실행 시작부터, `wait_s`는 접수부터 센다. 클라이언트 연결 종료는 취소가 아니다.
- 데몬이 출력을 `jobs/<id>/`에 스트림별 64 MiB까지 저장하고 초과분은 소비만 하며 `truncated=true`. 종료 후 exit code를 기록하고 컨테이너를 지운다. 재시작 시 `pah-*` 컨테이너를 실행 중·종료 모두 다시 읽어 출력 저장을 마친 뒤 최종 상태를 기록한다.
- 동시 실행은 `max_concurrent_jobs`(기본 4)까지, 넘으면 `queued`. 종료된 job은 7일 보관.
- 이미지 `plugins/host/sandbox/Dockerfile`: Ubuntu 24.04 arm64, git, curl, build-essential, python3, uv, ripgrep, jq, rsync.

## 6. 설정

```toml
service_url = "https://personal-agent-context.<account>.workers.dev"
device_id = "spark-a"
data_root = "~/.local/share/personal-agent-host/state"
corpus_data_root = "~/.local/share/personal-agent-host/state/corpus"
corpus_python = "~/.local/share/personal-agent-host/runtimes/corpus/bin/python"

[host]
listen = "127.0.0.1:18790"
allowed_hosts = ["spark-host"]
sandbox_image = "personal-agent-host-sandbox:1"
max_concurrent_jobs = 4

[[connections]]
space_id = "research-note"
connection_id = "main"
root = "~/Agent-Workspace/research-note"
roles = ["source", "work"]
permission = "read_write"
execute = "sandbox"
sources = ["research-project-administration/source-5"]

[[connections]]
space_id = "research-project-administration"
connection_id = "source-5"
root = "/mnt/nas/works/KIRIA-로봇직업교육센터 구축사업"
roles = ["source"]
permission = "read_only"
```

## 7. Sync Linux 실행 경로

- `credentials.py`: Keychain 대신 `config/sync-device.token`. `cli.py`: LaunchAgent 대신 systemd user unit과 `loginctl enable-linger`. `config.py`: XDG 경로. `paths.py`·`materialization.py`는 기존 darwin 분기로 충분하다.
- `install-runtimes.sh`: `uv`를 prefix에 설치, rhwp 대상 `linux-aarch64`, `systemctl --user`.

## 8. 설치·제거

- Spark: `personal-agent-host install`(prefix, venv, unit 4개, 토큰 파일), `uninstall`(unit 해제, device 해제, prefix 삭제). `cloudflared` 바이너리는 prefix, tunnel 토큰은 `config/tunnel.json`. Docker 이미지 하나. fstab 두 줄.
- 클라이언트: 없음.
- 맥: 이전 후 Sync 런타임·LaunchAgent·Keychain 항목 제거.

## 9. NAS와 백업

- DSM: NFS(v4.1) 켜고 works(Spark IP, ro)·backup(Spark IP, rw) 내보내기. fstab: `192.168.50.87:/volume1/<works> /mnt/nas/works nfs4 ro,noexec,nosuid,_netdev,x-systemd.automount 0 0`, `192.168.50.87:/volume1/backup /mnt/nas/backup nfs4 rw,noexec,nosuid,_netdev,x-systemd.automount 0 0`.
- `backup.sh`(매일 03:30): `mountpoint -q /mnt/nas/backup`이 아니면 종료. `rsync -a --delete ~/Agent-Workspace/ /mnt/nas/backup/agent-workspace/`. `state/`의 SQLite는 `sqlite3 <db> ".backup <dst>"`로 사본을 떠서 보내고, `config/`는 토큰·tunnel.json을 제외하고 복사한다. 결과는 `logs/backup.log`.
- 툴킷 정본(D1·R2)의 export는 Host와 분리된 별도 스크립트로 둔다.
- NAS backup 폴더에 스냅샷 일정(매일, 30일).

## 10. 순서

1. 연결: Tunnel·VPC Service 생성, Spark에 Host 서버 뼈대(읽기·쓰기·실행 하나씩)와 Worker adapter 뼈대를 올려 ChatGPT·Codex·Claude·Aside에서 호출. 폰 앱 노출 확인. document-files 추출 1건. DSM NFS, linger, 로컬 `main` push.
2. 구현: Sync Linux 경로, Host 도구·Connection 해석, job·Docker, CLI·설치·백업, Worker adapter·scope, `plugins/host`·`products.json`·`check_repository.py`.
3. 이전: 맥 → Spark 초기 복사 → Spark에서 시험 root로 확인 → 맥 Sync 정지 → 최종 차이 복사 → Spark device 등록과 연결 rebind → Spark 쓰기 활성화 → 백업 복원 한 번 열어 본 뒤 맥 정리.

되돌리기: rebind 전에는 Spark `uninstall`. rebind 뒤에는 연결을 맥 device로 되돌리고 Spark의 변경을 맥으로 복사한 뒤 `uninstall`.
