# Interactive runs for Claude Code and Codex

The sandbox cannot drive these two clients: Claude Code headless reports
"Not logged in" because it cannot read the Keychain, and Codex headless hit its
usage limit. Both are recorded as `environment_blocked`. Run them by hand when
the loading numbers are wanted.

## Register the surfaces

```sh
# Claude Code
claude mcp add --transport http surface-a https://surface-experiment.hiyaq77.workers.dev/s7k2q9/a/mcp
claude mcp add --transport http surface-b https://surface-experiment.hiyaq77.workers.dev/s7k2q9/b/mcp

# Codex
codex mcp add surface-a --url https://surface-experiment.hiyaq77.workers.dev/s7k2q9/a/mcp
codex mcp add surface-b --url https://surface-experiment.hiyaq77.workers.dev/s7k2q9/b/mcp
```

Leave exactly one of the two enabled for each session, and disable the
production toolkit connections while measuring.

## Per session

1. New conversation.
2. Send: `도구를 호출하지 말고 READY 한 단어만 답하십시오.`
3. Record the context display right away. Claude Code: `/context` (or
   `/context all`). Codex: whatever the session shows for context use.
4. Send: `시험 서버의 surface_probe를 한 번 호출하고, 반환된 receipt만 답하십시오.`
   (probe lives on the BASE/FULL/PAGED endpoints, not on A/B)
5. Record the context display again, plus the wall time for step 4.

Repeat BASE → FULL, FULL → BASE, BASE → FULL for three pairs.

## Collect

- The raw context readings, not a summary.
- Tool count and bytes each client reports.
- Whether the client shows tool search or deferred loading.
- Any refusal, timeout or login prompt, verbatim.

Probe endpoints:
`https://surface-probe.hiyaq77.workers.dev/s7k2q9/{base,full,paged}/mcp`
