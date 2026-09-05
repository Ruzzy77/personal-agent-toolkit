# Personal Agent Sync

Personal Agent Sync is the only component that receives Finder-folder authority.
It opens an outbound WebSocket to the remote context service; it does not listen
on a local port and does not expose the Mac through a tunnel.

## Responsibilities

- retain absolute folder locators and filesystem identities only in the private
  local database;
- recover same-volume Finder renames and moves by directory identity on macOS;
- explicitly rebind copied checkouts or restored folders with new filesystem
  identities while preserving logical document IDs by safe relative path and
  verifying the hashes of previously projected files;
- wake reconciliation from filesystem events, coalesce short event bursts, and
  retain a slower full scan for startup, recovered roots, and missed events;
- keep Source changes in a bounded queue and retry network or analyzer failures
  without rescanning every connected tree;
- exclude operating-system metadata such as `.DS_Store`, AppleDouble sidecars,
  Finder index folders, and Windows thumbnail caches even when a Connection
  intentionally includes project dotfiles;
- keep ordinary unsupported files as metadata-only Source records without
  uploading their bytes or retrying document analysis until those bytes change;
- remove only old `capture-*` staging files left by interrupted runs, in bounded
  batches after a 24-hour safety delay;
- analyze an immutable capture through the shared Document Files job contract;
- retain exact adapter identities as projection provenance, but queue existing
  unchanged documents only when Document Files explicitly raises that format's
  reanalysis generation;
- process explicit refreshes and current Finder changes before maintenance-only
  analyzer refreshes, so a large upgrade batch cannot delay live Source updates;
- stage and atomically commit extracted Corpus projections while leaving the
  last good remote revision readable on failure;
- reconcile exact remote records no longer retained by a completed local
  Corpus snapshot, while preserving current and Context-linked records;
- apply deterministic remote record retention in bounded passes after each full
  reconciliation, without judging the semantic importance of extracted text;
- receive bounded Work jobs, recheck current Connection policy and generation,
  and delegate actual file operations to the installed local Corpus authority;
- receive exact Source refresh jobs, recheck local Source access and the
  embedded analyzer, and keep the remote job open until the requested projection is
  committed or the attempt fails;
- store the device secret in Keychain and cache completed job responses so a
  reconnect can safely replay a job ID.

Source bytes are temporary. The remote Corpus keeps extraction records and
provenance anchors, not original document bytes. `local_only` Connections never
send Source state or file content.

When a remote-visible Source file disappears, its last good extracted record
stays readable before entering the retention lifecycle. Managed records wait 30
days before archive, 180 more days before trash, and 30 more days before purge;
transient records use 7, 30, and 7 days. A successful search or Source-unit read
renews the pre-trash access clock. Explicitly protected records and records
linked from an active Context are restored to active state and excluded from
automatic deletion. Each full reconciliation applies at most 50 actions per
Corpus and stores only one rotating scan cursor, so cleanup cannot produce an
unbounded operational history.

Document Files is installed in the Sync environment and runs as an isolated
subprocess with the same Python. Corpus keeps its own helper environment. The
Sync process exchanges bounded JSON with both helpers and does not import their
application code in-process. Remote Work requests pin Corpus to the
Sync-managed Document Files executable; they do not discover or mutate a Codex
or Claude plugin cache.

An adapter ID, implementation version, or configuration hash may change because
of packaging, refactoring, a runtime update, or a setting that does not invalidate
an existing extraction. Sync therefore records those exact values for provenance
and deduplication but does not use them as a freshness signal. Each format also
declares a small `reanalysis_generation`. A release increments it only when
unchanged documents need materially different extraction results. Existing
documents without a recorded generation are treated as the first baseline without
per-document writes or uploads. The database upgrade also retires queue entries
created by the former identity comparison once.

## Local document analysis

Sync always runs the installed Document Files backend and uploads only its
extraction envelope. The subprocess consumes the
`document-files.analysis-job.v1` contract and an immutable local byte capture.
There is no Cloudflare analyzer, remote fallback, approval record, or document
byte upload. If the embedded runtime cannot run, the queue records
`runtime_unavailable` and leaves the last committed projection active.

## Setup

1. Run `scripts/install-runtimes.sh`. It prepares Corpus and a Sync environment
   containing Document Files in two private, durable environments rather than a
   client plugin cache or repository worktree. It also provisions the verified
   `rhwp` release before document processing. On an update, both environments
   are completed before the existing background agent is briefly stopped,
   swapped, and restarted; a failed restart restores the previous runtime set.
2. Generate the private configuration with `personal-agent-sync
   init-from-corpus`, supplying the remote service URL and installed Corpus
   Python path. The command discovers remote-visible Spaces
   and their current Source/Work policies. Local paths stay in this file.
3. Store the provisioned device token with `personal-agent-sync set-credential`.
4. Run `personal-agent-sync validate`, then `personal-agent-sync migrate-local`.
   Migration directly exports Sense and Hypes state, uses the isolated Corpus
   runtime for the public Space projection, and copies durable Corpus document,
   revision, projection, unit, issue, Context-link, and external-provider
   metadata without copying original document bytes. It resumes at immutable
   projection boundaries after interruption. When a complete Corpus has been
   copied, it removes only exact remote-minus-local record IDs in bounded
   batches and reports the resulting shard size.
   `verify-migration --products products.json` takes the expected release's
   product registry (the example path is relative to the repository root) and
   compares durable identities, content hashes, lifecycle
   state, and extraction metadata. It recognizes a newer revision or analyzer
   projection only when the local Sync ledger records that exact Source digest
   and committed projection. Continuously advancing observation fields such as
   last-seen, last-accessed, and repeated-capture timestamps are not treated as
   content-identity mismatches while the local authority is live. A replaced
   file's retired identity is verified as unavailable without treating Sync's
   private detached locator or last observed file size as remote data.
   It also checks the deployed Sense, Corpus, Hypes, and unified MCP names,
   versions, and exact public tool-name lists against that registry before
   comparing durable records. The registry is a required operator input, not
   an installed snapshot or a value learned from the server being checked.
   The metadata
   receipt is compared with the exact locally recorded migration checkpoint,
   so later Finder scan timestamps do not invalidate a completed migration.
   Once a Corpus document migration has completed, a later run does not reopen
   that initial snapshot with projection identities created afterward. Current
   Source changes continue through Sync, while the rerun can still update shared
   metadata and remove only migration remnants created after completion.
   A successful initial pass removes checkpoints for projections retired before
   completion; an interrupted pass keeps them so migration can resume safely.
5. Run `personal-agent-sync reconcile` and verify the queue.
6. Start it with `personal-agent-sync run`, or install the per-user background
   service with `personal-agent-sync install-agent`.

The release registry is used only for this one-time migration check; the normal
daemon does not read it. Remote-only version changes do not require reinstalling
Sync, Corpus, or Document Files. Update these local environments when their code,
dependencies, or compatibility requirements change. An older verifier without
`--products` can be replaced at that next local update; until then, use the
current checkout's verifier in an isolated command rather than changing the
running daemon just to update release expectations.

From the current repository root, that command is
`uv run --project apps/sync --frozen personal-agent-sync verify-migration --products products.json`.
It uses the checkout's environment, not the installed background service.

Ordinary Finder renames and moves need no configuration change. If a folder was
copied, restored, or recreated as a new checkout, its filesystem identity is no
longer proof that it is the same Source. Stop Sync and run
`personal-agent-sync rebind-root SPACE:CONNECTION NEW_ROOT`. The explicit rebind
updates every Connection that shared the old root, rewrites only those private
local locators in the configuration, keeps the isolated local Corpus Source and
Work registrations aligned, preserves matching document identities, and queues
only files whose bytes actually changed. It never searches the home folder for
a merely similar directory name. If a later step fails, rerunning the same
command resumes from the already aligned authorities rather than guessing a
different folder.

`personal-agent-sync status` shows only opaque Connection and document IDs,
queue state, and stable error codes. It does not print local roots.

Remote clients request an on-demand refresh with the exact Space, Connection,
and document IDs. The request can include the last known revision digest. Sync
re-analyzes unchanged bytes when explicitly requested, atomically activates the
new projection, and removes only an unprotected superseded projection. The same
conservative cleanup follows an ordinary content change, so unreferenced prior
extractions do not accumulate with each saved version.
When migrated content is unchanged, Sync first resolves and reuses the durable
remote revision identity; an analyzer upgrade can replace its projection
without duplicating the captured revision.
Ordinary metadata-only filesystem changes still reuse the committed projection.
That reuse updates path, size, modification time, residency, and eligibility in
one metadata request without re-uploading extracted units.

Moves use the same immutable capture and digest comparison before reusing a
projection. A simultaneous content change therefore triggers analysis instead
of being treated as a path-only update. A format change keeps a `format_refresh`
event until the new projection is committed, even when bytes are unchanged;
format aliases and extension capitalization alone do not force analysis. Failed
format updates retain the previous projection with `changed` Source state, and
successful updates carry the current MIME type while reusing the byte revision.
Capture also checks the queued file's
device and inode; replacement after reconciliation stops with `source_changed`
until the next reconciliation identifies the new file. Moving a tracked file
over another tracked path preserves the moving document's ID and detaches the
previous occupant under the existing record-retention rules. Available and
unavailable records can consequently share a historical relative path without
being the same document.

Completion and retry backoff apply only to the queue event and file observation
that were processed. A newer observation during analysis or upload remains
queued, including a move, deletion, or format change. The comparison includes
file identity and metadata as well as event time, so equal timestamps do not
discard a different observation.

These checks do not re-analyze previously completed projections. If a specific
record is found to contain stale text after an earlier path-only update, refresh
that exact document; do not change format generations or retention to repair it.

`personal-agent-sync storage-report` reads the current remote shard sizes,
record counts, derived-index state, and largest logical projections. It is an
explicit diagnostic rather than a scheduled scan. `personal-agent-sync
storage-maintain` removes staged uploads older than 24 hours by default,
rebuilds older search projections from user-visible paths and extracted text
without duplicating canonical structure JSON, and losslessly rewrites one
bounded batch of legacy Source-anchor metadata and repeated table-cell
structure paths. Source-unit reads still return the full structure. Larger
one-time compaction is explicit
through `--unit-metadata-batches`; new uploads use the compact representation
immediately. It never chooses
canonical records for deletion by size. Exact local-snapshot reconciliation and
the bounded age-based retention pass are the only automatic canonical cleanup
paths, and both preserve explicitly protected or active Context-linked records.

Prepared migration payloads can still be uploaded with `personal-agent-sync
import`. Remote storage contains no operational Finder locator. Source-derived
text or user-authored Context content may contain a literal path when that text
was part of the record; it is treated as content, never as filesystem authority.

`reconcile_seconds` controls queue retry and root-recovery checks. Filesystem
events are coalesced for `event_debounce_seconds`; `full_reconcile_seconds`
controls the missed-event safety scan. The defaults are 15 seconds, 2 seconds,
and 15 minutes respectively.

`include_hidden = true` includes intentional hidden project content such as
`.github` or `.claude`. It never includes known operating-system metadata
artifacts. Connection-specific directory names and relative path prefixes can
be removed from the scan with `exclude_directory_names` and
`exclude_path_prefixes`.
