# MCP surface experiment: Aside A/B

## Surfaces
- A: 103 tools, served 142,965 B, Aside inventory 81,942 B
- B: 13 tools, served 11,266 B, Aside inventory 7,507 B
- Backend: one experiment Worker with a separate D1, test Host on Spark (127.0.0.1:18791), synthetic fixtures only.

## Results (32 runs, 8 cases x 2 surfaces x 2 repeats)

| surface | passed | wrong-target changes | median | mean |
|---|---|---|---|---|
| A | 16/16 | 0 | 16s | 17.4s |
| B | 16/16 | 0 | 25s | 26.7s |

W1: 2/2 on both surfaces.

## Paired completion time

| case | A | B | B-A |
|---|---|---|---|
| W1 | 11s | 20s | +9s |
| W1 | 15s | 12s | -3s |
| W2 | 15s | 14s | -1s |
| W2 | 12s | 14s | +2s |
| C1 | 15s | 24s | +9s |
| C1 | 16s | 23s | +7s |
| C2 | 14s | 34s | +20s |
| C2 | 14s | 41s | +27s |
| H1 | 19s | 17s | -2s |
| H1 | 16s | 20s | +4s |
| M1 | 19s | 46s | +27s |
| M1 | 22s | 49s | +27s |
| X1 | 22s | 30s | +8s |
| X1 | 25s | 29s | +4s |
| X2 | 20s | 26s | +6s |
| X2 | 24s | 28s | +4s |

- pairs where both passed: 16
- median A 16.0s, median B 25.0s
- median(B-A) = +6.5s; B is slower in 13 of 16 pairs

## Verdict by the agreed rule
- B common gate: passed 16/16, equal to A, W1 2/2, no wrong-target change. Gate met.
- Success improvement path: B did not pass more than A (16 vs 16). Not met.
- Time improvement path: B is slower, not 20%/2s faster. Not met.
- Context path: not applied for Aside (no observable context).
- Therefore B is not selected. A stays.

## Decision (reviewer, 2026-09-19)
Aside uses surface A through the single unified /mcp connection. This is not a
finding that native search works; it is the choice of the direct surface as the
operating default from what could be observed here. It is also not a decision to
keep seven per-product connections.
The test bench stays in place as the reference environment for the other
clients; only the Aside test connections are switched off. No B variant with a
longer direct-exposure list is built now: the comparison for such a variant
would be A, which already scored 16/16, and tuning the exposure list to these
eight tasks would fit the surface to the test rather than to real use.

## Notes
- Aside keeps a cached inventory without outputSchema: 81,942 B for A, 7,507 B for B.
- Aside pages through tools/list correctly (20 per page, 6 pages, last page reached).
- Aside ignores notifications/tools/list_changed: a session's tool list is fixed at start.
- Aside connects to a stdio server only when a tool is actually called.
- Claude Code and Codex: environment_blocked (sandbox login, usage limit). Interactive material prepared separately.
- The largest B penalties are C2 (+20s, +27s) and M1 (+27s, +27s): both need a schema first, so discovery adds a round trip.

## Close-out (2026-09-19)

```
실험 상태: 종료
운영 표면: 기존 통합 /mcp, 기존 도구 직접 노출
제품 스위치: 현재 의미와 저장값 유지
Aside: A 선택
Claude Code·Codex 대화형 측정: not_measured
미측정 사유: 사용자 제약에 따라 추가 플랫폼 측정 제외
B 및 직접 노출 확대 변형: 채택하지 않음
후속 측정·설계 미결 항목: 없음
시험 인프라: 철거
```

Claude Code and Codex headless attempts stay recorded as `environment_blocked`.
For those clients this is not a finding that the surface performs the same; it
records that production continues unchanged without further measurement.

## Teardown (2026-09-19 01:46 UTC)

| resource | id | result |
|---|---|---|
| Aside connections `exp-a`, `exp-b` | - | removed with their cached inventories |
| Spark `personal-agent-host-test.service` and `~/mcp-surface-test/` | - | stopped, disabled, unit and directory deleted; port 18791 closed |
| Durable Object classes `CorpusShard`, `SyncBroker` (test worker) | migration `v2` `deleted_classes` | deployed before the worker was deleted |
| Worker `surface-experiment` | - | deleted (endpoint answers 404) |
| Worker `surface-probe` | - | deleted (endpoint answers 404) |
| VPC service `surface-host-test` | `01a0b731-8e32-7b23-95eb-2b8378d9bfbf` | deleted; shared service `spark-host` kept |
| D1 `personal-agent-surface-test` | `d0352ad8-3f6e-4d22-acc1-a2f4977ba19f` | deleted |
| R2 `surface-experiment-assets` | - | deleted |

Nothing failed to delete. No experiment-only credential remains: the test Host
token lived in `~/mcp-surface-test/config` and went with that directory, and the
`HOST_UPSTREAM_TOKEN` secret went with the deleted worker.

Production after teardown: context service 0.7.0 healthy, `/host/mcp` answering
401 for an unauthenticated call, Spark Host and tunnel active.

철거 완료, 후속 작업 없음.
