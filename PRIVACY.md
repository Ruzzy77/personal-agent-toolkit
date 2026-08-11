# Privacy boundary

Personal Agent Toolkit is local-first and ships with no user data.

## Sense

Sense stores one private work profile on the user's machine. The profile contains durable ways of
working and cross-work learning, not project files or raw conversation history.

- The plugin package contains no default or active profile.
- Import creates a read-only preview.
- Activation requires an explicit local command with the reviewed revision and digest.
- Sensitive persistence and broader profile use require explicit user confirmation.
- Plugin updates do not replace the private profile database.

## Corpus

Corpus indexes only sources the user explicitly registers.

- Source bytes remain in their original locations and are not written by Corpus.
- Local indexes and reusable context stay in the private Corpus runtime directory.
- Gmail bodies remain with Gmail. Completed Codex and Claude turns remain with their providers.
- Provider links keep only limited record details, the original record's location, and a fingerprint
  used to detect changes.
- Exact provider content is read at request time and is not copied into the Corpus index.
- Archived context remains readable. Destructive context purge is not currently provided.

Some explicitly requested extraction operations may run outside the local machine when the Corpus
execution policy allows it. Downloading cloud placeholder files uses network, disk, and local
storage. These actions are separate from ordinary local reads.

## Hypes

Hypes combines the `adapt-response` skill with a sessionless local MCP server and a private
cognitive-model database on the user's machine.

- It changes the actual response rather than producing a separate recommendation or interface.
- The private database stores compact concept relationships and explanation clues with an exact
  topic, task, and responsibility scope. It does not store raw conversations, general ability
  scores, personality, emotion, or sensitive traits.
- Provisional understanding stays in the visible conversation. The database does not accumulate
  intermediate candidates or observations.
- At task completion, handoff, a material conclusion, or topic change, the calling agent may retain
  one compact relation automatically only when it is stable, reusable, exactly scoped,
  non-sensitive, and likely to change a future explanation. It does not ask the user whether to
  save. Silence, brief assent, preferences, agreement, project facts, health, ability, personality,
  transcripts, full answers, and hidden reasoning are excluded.
- The retention basis distinguishes an explicit user request from an agent-selected conversation
  conclusion. It records the route used; it does not prove that the caller interpreted the
  conversation correctly.
- Active relations keep their exact scope and a review-after boundary. Agent-selected conversation
  conclusions are reviewed after 90 days by default; explicit retention requests are reviewed after
  180 days by default. Conflicting or due-for-review relations remain visible for recheck instead of
  silently influencing later responses. A recheck stores only the old relation and a bounded reason,
  not the competing claim or conversation.
- The MCP transport stores no connection or process session. Reads identify their exact scope;
  retention, recheck, and deletion writes carry the expected active-model revision and idempotency
  information. Only relations that pass the retention gate persist across processes.
- Automatic skill selection is based on the current request, not background observation. Hypes does
  not monitor the screen, keyboard, emotion, or unrelated conversations.
- Reviewed working preferences that belong across kinds of work remain in Sense. Hypes does not
  copy or update the Sense profile, and it does not copy Corpus sources or saved context.

## Private ChatGPT tunnel use

The product plugins remain local-first and contain no `.app.json`, maintainer-owned ChatGPT
connection, OAuth credential, or multi-user storage. Their streamable HTTP listeners accept only
loopback connections.

The optional gateway can connect selected installed products to the user's own OpenAI Secure MCP
Tunnels. It does not copy product data or create another product database. It starts the packaged
local launchers and proxies fixed loopback paths; one independently registered tunnel remains the
private transport for each selected product. The Platform runtime key is read from the user's login
Keychain and passed only to official tunnel-client processes. It is not placed in the product
processes, gateway process, LaunchAgent file, plugin package, or repository.

The public gateway package contains no tunnel id, developer connection id, API key, product data,
or absolute maintainer path. A user's generated tunnel profiles and `.app.json` bindings remain
private local configuration and are not part of this repository. Stopping the gateway leaves local
product data and installations unchanged.

Registering a tunnel-backed MCP endpoint for private developer testing does not publish it. Public
availability still requires a separate submission and review for each product. A separately hosted
multi-user service must additionally define authenticated isolation, retention, export and deletion,
logging redaction, abuse and quota controls, and the exact sources that may leave each user's
device. Shared operational infrastructure does not merge product data, scopes, tool surfaces, or
deletion boundaries; the gateway is not a cross-product profile or model router.

## Repository contents

The release repository must not contain:

- Sense, Corpus, or Hypes runtime databases, or any Hypes cognitive-model data;
- registered source contents or provider messages;
- `.env` files, credentials, tokens, or private keys;
- absolute paths from the maintainer's machine;
- generated caches, virtual environments, or staging files.

`scripts/validate_release.py` enforces these boundaries for the tracked release.
