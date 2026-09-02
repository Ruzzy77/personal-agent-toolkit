# Personal Agent Context Service

## Purpose

This service is the owner-operated remote runtime for Sense, Corpus, and Hypes.
It replaces client-specific local MCP processes and the ChatGPT tunnel while
preserving the existing product boundaries and public tool names.

Journal remains a separate service. Document Files remains a replaceable
analyzer: the local Sync app may invoke it locally or send an authorized,
temporary document capture to a remote analyzer.

## Runtime boundary

```text
Codex / Claude / ChatGPT
        |
        | OAuth resource token
        v
Personal Agent Context Worker
  |-- /sense/mcp
  |-- /corpus/mcp
  |-- /hypes/mcp
  |-- owner-state D1
  |-- CorpusShard Durable Objects
  `-- SyncBroker Durable Objects
             ^
             | outbound WebSocket
             |
       Personal Agent Sync (macOS)
             |
             | read-only Source capture / version-checked Work access
             v
       Finder files + Document Files analyzer
```

The Worker never opens a path on the owner's Mac. The Sync app is the only
component with local filesystem authority. It keeps path locators and macOS
filesystem identities locally and exposes only stable Connection, document,
revision, and job identifiers to the service.

## Storage

### Owner-state D1

The small, cross-product state is stored in D1:

- the current Sense profile and approved Section Skills;
- the Hypes node, predicate, and edge graph;
- Corpus Space, Context, Context Skill, Connection, and Current File metadata;
- registered Sync devices, bounded jobs, and migration receipts.

No absolute local path is stored in D1.

### CorpusShard Durable Objects

Each Corpus Source has one SQLite-backed Durable Object named from the owner
and `corpus_id`. A shard stores document identity, the last successful revision,
extraction projections, Source units, and its FTS5 projection. Per-Corpus
sharding avoids making the current multi-gigabyte source fabric depend on one
database's size or write serialization.

A projection upload is staged under an `upload_id`. The existing active
projection remains visible while units arrive. Commit verifies the declared
unit count and manifest hash, then changes the active revision in one SQLite
transaction. Failed and abandoned uploads never replace the last good record.
Original document bytes are not retained.

## Sync behavior

The Sync app connects outward to the SyncBroker. There is no inbound listener
or public tunnel on the Mac.

1. Filesystem events and start/resume reconciliation update local state.
2. A rename or same-volume move updates the local locator without creating a
   new content revision.
3. A content change is captured and analyzed automatically when the Connection
   policy permits it.
4. Remote reads use the last committed record. If a client requires a current
   source or a live Work operation, the service sends a bounded job to the
   connected Sync app.
5. The user is asked for help only when the app cannot recover access or local
   policy requires explicit approval.

The broker accepts one active writer for a device. Every job has a stable ID,
scope, deadline, maximum payload, and idempotency key. The app rechecks local
policy and file identity before reading or writing.

## Document analysis

The Sync app selects one analyzer route per Connection policy:

- `local`: invoke the installed Document Files process and upload only its
  extraction envelope;
- `remote`: upload an authorized temporary capture for remote analysis;
- `approval_required`: wait for explicit owner approval before a document may
  leave the Mac.

Both routes must produce the same versioned extraction envelope. Corpus owns
document and revision identity, provenance anchors, atomic activation, and
search. Document Files owns format detection, parsing, OCR, structural units,
coverage, and extraction issues.

## Migration and cutover

Migration is additive and resumable. Stable IDs and current versions are
uploaded first, followed by Corpus projections in bounded batches. A receipt
records the source digest and imported counts. Local data remains authoritative
until read and write parity has been checked in every supported client.

The ChatGPT tunnel and local MCP launchers are removed from active client
configuration only after:

1. Sense, Hypes, Corpus Context, Source search/read, and Work operations pass
   contract tests against the remote endpoints;
2. migrated counts, IDs, hashes, and selected exact reads match the local
   stores;
3. the Sync app reconnects after restart and sleep, processes a changed Source,
   and preserves the last good revision on failure;
4. Codex, Claude Desktop, Claude Code, claude.ai, and web ChatGPT expose the
   same current tool schemas and operate on the same committed state.

