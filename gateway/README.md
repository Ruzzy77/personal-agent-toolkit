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

See [`GUIDE.md`](./GUIDE.md) for setup, selection, Keychain, LaunchAgent, ChatGPT packaging,
transition, and rollback instructions.
