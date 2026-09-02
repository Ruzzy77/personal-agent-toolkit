# Privacy boundary

Personal Agent Toolkit ships with no user data. Sense, Corpus, Hypes, and Journal use owner-operated remote services; Personal Agent Sync and Document Files keep Finder access under the owner’s local policy. Design has no product data store.

## Sense

Sense stores one owner-scoped set of user-controlled guidance and user-approved Section Skills in
the remote context service's D1 database. The profile covers durable intent, responsibility, and
lessons that should affect important choices in different contexts. A Section Skill holds one
reusable workflow attached to a profile section. Neither holds project files or raw conversation
history.

- The plugin package contains no default or active guidance and only declares the authenticated
  remote MCP endpoint and its Skills.
- Migration copies the current local profile and approved Skills without creating revision history.
  Subsequent ordinary updates replace complete sections with conflict-safe change tokens.
- The authenticated owner may read a sensitive section only by its explicit identifier. Ordinary
  overview responses omit sensitive text. Sensitive revisions, sensitive Skill changes, Skill
  removal, and permanent deletion remain restricted to the local administrative interface.
- Plugin updates do not replace the stored profile or Section Skills. Each owner identifier is part
  of every storage key, and tokens are valid only for the exact Sense resource and scopes.

## Corpus

Corpus keeps durable Context and extracted Source records independent of the current Finder path.
Only sources and Work folders that the owner explicitly connects are represented remotely.

- Space, Context, Context Skill, Connection, Current File, Sync-device, and migration-receipt state
  is stored in the owner-scoped D1 database. Each Source's documents, revisions, extraction
  projections, Source units, issues, provider metadata, and search index are stored in a separate
  owner-scoped SQLite Durable Object.
- Corpus retains extracted text, structural units, provenance anchors, and the revisions needed for
  durable reading. It does not retain original document bytes. Failed or incomplete uploads never
  replace the last committed projection.
- Operational Finder locators, filesystem identifiers, and transfer approvals stay in Personal
  Agent Sync's private local database. Remote records use stable Connection, document, revision,
  projection, and job identifiers rather than an authoritative local path.
- A `local_only` Connection sends neither Source state nor file content. Other Connections may send
  extraction output after the Sync app rechecks the current scope, generation, and transfer policy.
  A separately deployed remote analyzer receives only a version-pinned temporary capture when the
  Connection permits it; an `approval_required` route also requires approval for the exact document
  revision and byte limit.
- Gmail bodies remain with Gmail. Completed Codex and Claude turns remain with their providers.
  Provider links keep bounded record metadata and freshness identities; selected Context items are
  the durable interpreted knowledge.
- Work operations travel through a bounded remote job and an outbound Sync connection. The Sync app
  rechecks the Connection's role, generation, and read or write permission before delegating to the
  local Corpus authority. Replacements and destructive operations require the expected current
  version and the product-specific confirmation fields.
- Hidden, temporary, linked, special, and policy-excluded files are not captured. Temporary Source
  snapshots use private local storage and are deleted after analysis or failure.

## Hypes

Hypes stores one owner-scoped, revisable relationship graph in the remote context service's D1
database and exposes it through an authenticated MCP endpoint.

- The relationship model is the agent's current, revisable understanding of the user. It is not a
  user-authored profile, an external evaluation, or a source of objective facts about the person.
- The database stores agent-created Nodes, Predicates, and Edges. It does not store raw
  conversations, full answers, task records, project facts, Corpus sources, Sense guidance, or
  hidden reasoning.
- Current user input always takes priority over the stored graph. A rewrite is one transaction and
  either changes the complete requested graph patch or leaves it unchanged. Deleting a Node or
  Predicate also removes its incident Edges in that transaction.
- Hypes uses only nonsensitive reusable relationships. Automatic Skill selection does not monitor
  the screen, keyboard, emotion, or unrelated conversations.
- Every graph row is keyed to the authenticated owner. Tokens are accepted only for the exact Hypes
  resource and requested scopes.

## Journal

Journal is an owner-operated remote service for daily capture, weekly status, and period review.
Its Cloudflare D1 database is the canonical chronology; Corpus remains the canonical home for
project source material and reusable project context.

- Journal stores concise item titles, current summaries, lane and resolution, project keys,
  timestamps, source references, append-only state events, weekly closures, corrections, and
  Corpus propagation receipts.
- Journal does not store raw email bodies, document bytes, calendar descriptions, browser history,
  or full AI conversations. Those remain with their source provider or local file.
- Monitoring credentials can create and refresh observations but cannot mark an item completed,
  held, canceled, or active. Those resolution changes require the authenticated owner.
- Closing a week makes its items immutable. Later changes are separate correction events rather
  than rewrites of the closed chronology.
- Only an explicit durable outcome with a target project becomes a Corpus propagation candidate.
  Journal does not create a combined Journal archive in Corpus or copy transient daily activity
  into project Context.
- The Sites frontend keeps its service credential as a secret runtime environment variable and is
  published with owner-only access. WebMCP tools operate only on the same visible board actions.
- The remote MCP uses the owner authentication service and exact Journal resource scopes. The
  automation client uses a separate read-and-ingest credential stored outside the repository.
- D1 exports are operational backups controlled by the owner. They are not shipped in the plugin
  or public repository.

## Design

Design is a Skill-only plugin with a static, publicly licensed reference pack. It has no MCP
server, external account connection, background process, or product data store. The reference pack
contains reusable patterns, optional recipes, example assets, and source attribution; it contains
no project-only extensions or user content. Design can read or edit project files only through the
host agent's current tools and permissions, and the plugin does not retain those files or task
history.

## Private ChatGPT tunnel use

The gateway and Secure MCP Tunnel are transitional compatibility paths while the remote migration
is being verified. They are not part of the target operating model.

The permanent Sense, Corpus, and Hypes endpoints run in the owner-operated remote context service.
Codex, Claude, ChatGPT, and claude.ai connect to those HTTPS resources directly, so they do not
receive a local launcher path or an inbound route to the Mac. Personal Agent Sync opens only an
outbound WebSocket for authorized Source updates and Work jobs.

The public gateway package contains no tunnel identifier, developer connection identifier, API key,
product data, or absolute maintainer path. Private tunnel profiles remain outside the repository.
The gateway and its active client connections are removed only after migration parity and client
cutover have been verified; removing them does not delete either the remote records or the local
source files.

## Optional remote authentication template

The `auth` directory is a self-deploy template for one owner to authorize the remote context and
Journal services.

- The public repository contains no Google client secret, Cloudflare credential, owner identifier,
  production token, grant, or session.
- A deployer supplies the resource registry and secrets directly to their own Cloudflare account.
- OAuth grants and opaque tokens remain in the deployer's `OAUTH_KV` namespace.
- Every resource has an exact audience URI and its own scopes. A token for one resource is rejected
  by another resource.
- Resource Workers validate tokens through a Cloudflare Service Binding to a private RPC entrypoint.
  The template does not expose a public token-inspection endpoint.
- Google login uses the authorization code flow with PKCE. The authentication Worker verifies the
  ID token signature, issuer, audience, expiry, nonce, subject, and verified email before checking
  the owner's allowlist.
- The authentication Worker does not retain Google's access token or refresh token. It keeps only
  the approved Google identity claims in its own OAuth grant.
- Production deployment identifiers and credentials remain outside the repository.

## Repository contents

The release repository must not contain:

- Sense, Corpus, Hypes, or Journal runtime databases, or any Hypes relationship-model data;
- registered source contents or provider messages;
- `.env` files, credentials, tokens, or private keys;
- absolute paths from the maintainer's machine;
- generated caches, virtual environments, Node dependency folders, local Worker state, or staging
  files.

The product directories under `plugins/` are marketplace installation targets. They contain no
build-time copy of runtime data or maintainer credentials. The Design plugin contains only its
Skills and the generated public reference pack. `services/journal` contains the
deployable schema and service source, and `sites/journal` contains the owner-only Sites frontend;
runtime values and secrets stay in ignored configuration or the hosting environment. Public
resource and Site endpoints may appear in the plugin manifest. The optional gateway remains a
separate package and follows the same repository-content boundary.

The authentication template and Journal service record their resolved JavaScript dependencies in
`auth/package-lock.json` and `services/journal/package-lock.json`.
