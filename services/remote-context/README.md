# Personal Agent Context service

Owner-operated remote MCP and synchronization service for Sense, Corpus, and
Hypes.

The service uses the existing Personal Agent Auth Worker for OAuth, D1 for
owner-level state, one SQLite-backed Durable Object per Corpus Source, and a
SyncBroker Durable Object for outbound macOS Sync connections.

## Local checks

```sh
npm install
npm run check
```

Copy `wrangler.example.jsonc` to the ignored `wrangler.jsonc`, provision the D1
database and Durable Object namespaces, and set the resource URLs to their
final HTTPS endpoints. Credentials and production resource identifiers do not
belong in the repository.

## Endpoints

- `/sense/mcp`
- `/corpus/mcp`
- `/hypes/mcp`
- `/sync/v1/connect`
- `/health`

The Sync endpoint accepts only the dedicated device credential. MCP endpoints
accept resource-specific owner OAuth tokens. Operational Finder locators and
filesystem identities remain in the Sync app. A path appearing literally in
source-derived or user-authored text is stored only as untrusted content and is
never interpreted as remote filesystem authority.

The Corpus MCP can enqueue one exact `source.refresh` job for a registered
document and expose its bounded job status. The outbound Sync app rechecks the
current local Connection role, generation, source availability, and analyzer
policy before reading bytes. A successful job reports the committed revision
and projection; a queued response is not completion.

The Sync-only verification summary reports the deployed Sense, Corpus, and
Hypes server versions and public tool-name sets. Migration verification compares
that manifest before record parity, so a healthy endpoint running an older MCP
surface is not mistaken for a completed rollout.

## Storage operations

The Sync operator interface provides an explicit storage report and a
conservative maintenance command. The report scans logical unit sizes only when
requested, so normal Source reads and uploads do not pay for per-unit accounting
writes. Maintenance removes sufficiently old abandoned staging data and
compacts only the derived search index. Canonical Corpus records continue to
follow local retention and Context protection.

Legacy Source-unit anchors can be rewritten in bounded batches to remove fields
that are already guaranteed by their document, revision, projection, or
structure row. Reads reconstruct the complete public anchor, so this reduces
stored bytes without dropping extracted structure or provenance.
Repeated table-cell container data in structure paths uses the same lossless
approach. The canonical Source-unit read reconstructs the original object,
while the search index retains its unmodified structural text. Existing shards
are scanned once in bounded batches; new uploads write the compact storage form
before commit.
Deploy the reader first with `STRUCTURE_PATH_COMPACTION_WRITE_ENABLED=false`.
After read and migration verification, deploy the same reader with the flag set
to `true` and retain the reader-compatible deployment as the rollback floor.
Redundant legacy Source-unit indexes are removed during maintenance; the
remaining primary and uniqueness indexes already cover the live read paths.
