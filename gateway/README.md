# Personal Agent Tunnel Gateway

This optional toolkit component lets private ChatGPT developer connections use selected Sense,
Corpus, and Hypes plugins that are already installed locally. It is not a fourth plugin and does
not add a combined tool namespace. One macOS LaunchAgent supervises the selected installed product
servers, one fixed-path loopback gateway, and one OpenAI Secure MCP Tunnel client per selected
product.

The gateway does not copy or migrate product data. Codex installations are discovered from the
enabled `personal-agent-toolkit` marketplace entries. A manual product root can be supplied for an
equivalent local installation. Each selected product remains independently registered in ChatGPT
and keeps its own tunnel and fixed gateway path.

The official builder requires the remote-runtime owner to be the exact root of a clean Git
checkout. It rejects hidden index flags and ambient Git overrides, compares the index and each
tracked path's exact name, kind, executable bit, and raw bytes with the recorded commit, and ignores
local filters when it checks untracked files. It assembles only commit bytes, fixes output modes
independently of the caller's umask, and records that commit in the release sentinel. If replacing
an existing release cannot finish before retirement begins, the previous release is restored. If
deleting an already retired release is interrupted, the verified new release remains active, the
CLI reports `cleanup_pending=true`, and the next build finishes that cleanup before it changes the
active release.

See [`GUIDE.md`](./GUIDE.md) for setup, selection, Keychain, LaunchAgent, ChatGPT connection testing,
transition, and rollback instructions.
