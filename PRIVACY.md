# Privacy boundary

Personal Agent Toolkit is local-first and ships with no user data.

## Sense

Sense stores one private set of user-controlled guidance and user-approved Section Skills on the
user's machine. The profile covers durable intent, responsibility, and lessons that should affect
important choices in different contexts. A Section Skill holds one reusable workflow attached to a
profile section. Neither holds project facts, project files, or raw conversation history.

- The plugin package contains no default or active guidance.
- The private database stores one current profile. Section Skills are stored as private `SKILL.md`
  files under their linked sections. Sense stores no revision history, replay receipts, source
  locations, or source hashes.
- Import uses a profile the user has already reviewed. Replacing an existing profile requires an
  explicit local confirmation.
- Section Skill changes, sensitive guidance, and permanent deletion require explicit local
  confirmation.
- Ordinary updates use only the current section's change token and complete replacement text.
- Plugin updates do not replace the current guidance or Section Skills. The schema 2 upgrade keeps
  the active profile and removes superseded history and provenance fields.

## Corpus

Corpus indexes only sources the user explicitly registers.

- Source bytes remain in their original locations and are not written by Corpus.
- Local indexes and reusable context stay in the private Corpus runtime directory.
- The index keeps the current revision and extraction projection for each current source file. It
  does not accumulate source snapshots, scan-event history, or older extracted copies.
- Gmail bodies remain with Gmail. Completed Codex and Claude turns remain with their providers.
- Provider links keep only limited record details, the original record's location, and a fingerprint
  used to detect changes.
- Exact provider content is read at request time and is not copied into the Corpus index.
- Archived context remains readable. Destructive context purge is not currently provided.
- A separately connected local work folder is writable only when the user explicitly registers it
  for an active Corpus context. The local file remains the latest copy; Corpus does not upload or
  maintain an offline cloud copy of the folder.
- Work-folder replacement uses a freshly observed file version and stops on concurrent changes.
  Corpus keeps only the latest recoverable replacement for each path as a private copy under its
  runtime and removes older or expired recovery copies during bounded local maintenance.
- Hidden, sensitive, temporary, linked, and special files are excluded. Work-folder deletion
  requires an explicit request and current file version; tools do not move or execute files, and
  local root paths are not returned through the Chat surface.

Some explicitly requested extraction operations may run outside the local machine when the Corpus
execution policy allows it. Downloading cloud placeholder files uses network, disk, and local
storage. These actions are separate from ordinary local reads.

## Hypes

Hypes combines the `use-user-model` skill with a sessionless local MCP server and a private,
revisable relationship model of the user on the user's machine.

- The relationship model is the agent's current, revisable understanding of the user. It is not a
  user-authored profile, an external evaluation, or a source of objective facts about the person.
- The database stores agent-created Nodes, Predicates, and Edges. It does not store raw
  conversations, full answers, task records, project facts, Corpus sources, Sense guidance, or
  hidden reasoning.
- Hypes reads only when the model could materially change the current response and writes only when
  the interaction changed a reusable part of the agent's model. The skill can be selected
  implicitly, but a conversation that neither depends on nor changes the model makes no Hypes call.
  Ordinary conversation completion is not stored.
- Current user input always takes priority over stored structure. When that input actually changes
  a reusable relation, the agent replaces or deletes the old structure rather than adding review,
  evidence, confidence, or retention records to every relation. Task-local facts and wording are
  not written back as changes to the relationship model.
- The MCP exposes only `hypes_read` and `hypes_rewrite`. A rewrite is one SQLite transaction and
  either changes the complete requested graph patch or leaves the graph unchanged. Deleting a Node
  or Predicate also removes its incident Edges in that transaction.
- Schema initialization runs once at server start. Ordinary reads use a read-only SQLite connection
  and do not acquire a schema-write lock.
- The local database is `hypes-ontology.sqlite3`. Earlier Hypes databases are not read, converted,
  deleted, or included in the active model.
- The Hypes data directory and database must be owned by the current user and use modes `0700` and
  `0600`. Hypes refuses unsafe links, ownership, or permissions before it writes.
- Automatic skill selection is based on the current request. Hypes does not monitor the screen,
  keyboard, emotion, or unrelated conversations.
- Sense continues to own durable user-set direction, while Corpus owns registered sources and
  project relationships. Hypes does not copy either store.

## Private ChatGPT tunnel use

The product plugins remain local-first and contain no `.app.json`, maintainer-owned ChatGPT
connection, OAuth credential, or multi-user storage. Their streamable HTTP listeners accept only
loopback connections.

The optional gateway can connect selected installed products to the user's own OpenAI Secure MCP
Tunnels. It does not copy product data or create another product database. The supervising service
starts the selected local launchers, and the gateway proxies their fixed loopback paths. Each
product uses its own tunnel. The Platform runtime key is read from the user's login Keychain and
passed only to official tunnel-client processes. It is not placed in the product processes,
gateway process, LaunchAgent file, plugin package, or repository.

The public gateway package contains no tunnel id, developer connection id, API key, product data,
or absolute maintainer path. A user's generated tunnel profiles and `.app.json` bindings remain
private local configuration and are not part of this repository. Stopping the gateway leaves local
product data and installations unchanged.

Registering a tunnel-backed MCP endpoint for private developer testing does not publish it. Public
availability still requires a separate submission and review for each product. The gateway is not
a hosted service, cross-product profile, or model router.

## Repository contents

The release repository must not contain:

- Sense, Corpus, or Hypes runtime databases, or any Hypes relationship-model data;
- registered source contents or provider messages;
- `.env` files, credentials, tokens, or private keys;
- absolute paths from the maintainer's machine;
- generated caches, virtual environments, or staging files.

The three product directories under `plugins/` are both the source and the marketplace installation
targets. They contain no build-time copy of runtime data or maintainer credentials. The optional
gateway remains a separate package and follows the same repository-content boundary.
