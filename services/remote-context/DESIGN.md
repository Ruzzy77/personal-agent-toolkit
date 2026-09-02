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

No operational Finder locator is stored in D1. Source-derived or user-authored
text may contain a literal path, but it has no filesystem authority.

### CorpusShard Durable Objects

Each Corpus Source has one SQLite-backed Durable Object named from the owner
and `corpus_id`. A shard stores document identity, retained revisions,
extraction projections, Source units, external-provider binding/run/record
metadata, and its FTS5 projection. Per-Corpus
sharding avoids making the current multi-gigabyte source fabric depend on one
database's size or write serialization.

A projection upload is staged under an `upload_id`. The existing active
projection remains visible while units arrive. Commit verifies the declared
unit count and manifest hash, then changes the active revision in one SQLite
transaction. Failed and abandoned uploads never replace the last good record.
Original document bytes are not retained. A repeated upload whose revision,
projection manifest, metadata, and unit count already match the committed state
returns the existing receipt without rewriting Source units or the FTS index.
Identical external-provider snapshots are likewise recognized by digest.

Staging keeps a digest of each canonical unit envelope rather than a second
complete JSON copy. Committed Source anchors omit identity, content-hash, and
structural-locator fields only when those values exactly match the normalized
document, revision, projection, and structure columns; reads reconstruct the
same public anchor. This reduces repeated storage without discarding source
structure or provenance. Inventory reports the shard database size and bounded
staged-upload identities.

Structural-only units remain canonical Corpus records but are not copied into
the FTS5 search projection. An explicit, versioned maintenance pass removes
legacy structural-only FTS rows in bounded projection batches. It does not
rewrite or delete document, revision, projection, Source-unit, or Context data.
Detailed storage inspection is read-only and runs only on operator request; it
reports logical record sizes and the largest projections without adding
per-unit accounting writes to ordinary ingestion.

After a complete local snapshot has been migrated, Sync may reconcile exact
remote-minus-local document, projection, and abandoned-upload IDs in bounded
batches. The shard refuses to remove a current active projection or a record
referenced by an active Corpus Context. This cleanup follows the local
retention decision; it does not infer semantic importance or prune records only
because they are large.

Sync may also remove abandoned staged uploads after a configured minimum age.
Age-based cleanup never targets a committed projection, and the ordinary
default leaves a full day for an interrupted upload to resume.

Committed units retain the complete logical Source anchor returned to clients,
but the shard stores document, revision, projection, path, and structural-locator
values only once when they match the projection header. Reads reconstruct those
invariants, avoiding per-unit metadata amplification without weakening provenance.

## Sync behavior

The Sync app connects outward to the SyncBroker. There is no inbound listener
or public tunnel on the Mac.

1. Filesystem events wake a coalesced reconciliation. Startup, recovered roots,
   and a slower periodic scan cover missed events without repeatedly traversing
   every Source tree.
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

Migration is resumable. Stable IDs, document inventory, external-provider
metadata, and current versions are uploaded first, followed by retained Corpus
projections in bounded batches. A receipt
records the source digest and imported counts. Local data remains authoritative
until read and write parity has been checked in every supported client.

The ChatGPT tunnel and local MCP launchers are removed from active client
configuration only after:

1. Sense, Hypes, Corpus Context, Source search/read, and Work operations pass
   contract tests against the remote endpoints;
2. migrated counts, IDs, hashes, durable metadata, and selected exact reads
   match the local stores; liveness timestamps that may advance during the
   comparison are checked operationally rather than as content identity;
3. the Sync app reconnects after restart and sleep, processes a changed Source,
   and preserves the last good revision on failure;
4. Codex, Claude Desktop, Claude Code, claude.ai, and web ChatGPT expose the
   same current tool schemas and operate on the same committed state.
