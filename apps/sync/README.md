# Personal Agent Sync

Personal Agent Sync is the only component that receives Finder-folder authority.
It opens an outbound WebSocket to the remote context service; it does not listen
on a local port and does not expose the Mac through a tunnel.

## Responsibilities

- retain absolute folder locators, filesystem identities, and transfer approvals
  only in the private local database;
- recover same-volume Finder renames and moves by directory identity on macOS;
- wake reconciliation from filesystem events, coalesce short event bursts, and
  retain a slower full scan for startup, recovered roots, and missed events;
- keep Source changes in a bounded queue and retry network or analyzer failures
  without rescanning every connected tree;
- analyze an immutable capture through the shared Document Files job contract;
- stage and atomically commit extracted Corpus projections while leaving the
  last good remote revision readable on failure;
- receive bounded Work jobs, recheck current Connection policy and generation,
  and delegate actual file operations to the installed local Corpus authority;
- store the device secret in Keychain and cache completed job responses so a
  reconnect can safely replay a job ID.

Source bytes are temporary. The remote Corpus keeps extraction records and
provenance anchors, not original document bytes. `local_only` Connections never
send Source state or file content.

Corpus and Document Files run in separate helper environments. This preserves
their independent dependency contracts; the Sync process exchanges bounded
JSON with each helper and never imports either package in-process.

## Analyzer routes

- `local` runs the installed Document Files backend and uploads only its
  extraction envelope.
- `remote` streams one version-pinned temporary capture to the configured remote
  analyzer and then discards the capture.
- `approval_required` uses the remote route only after the exact document ID,
  content digest, and byte ceiling have been approved locally.

Both analyzer routes consume and return the same `document-files.analysis-job.v1`
contract. The remote Worker only proxies authorized bytes to the separately
deployed analyzer binding; it does not reimplement document parsing.

## Setup

1. Run `scripts/install-runtimes.sh`. It installs Sync, Corpus, and Document
   Files into three private, durable environments rather than a client plugin
   cache or repository worktree.
2. Generate the private configuration with `personal-agent-sync
   init-from-corpus`, supplying the remote service URL and the installed Corpus
   and Document Files Python paths. The command discovers remote-visible Spaces
   and their current Source/Work policies. Local paths stay in this file.
3. Store the provisioned device token with `personal-agent-sync set-credential`.
4. Run `personal-agent-sync validate`, then `personal-agent-sync migrate-local`.
   Migration directly exports Sense and Hypes state, uses the isolated Corpus
   runtime for the public Space projection, and copies durable Corpus document,
   revision, projection, unit, issue, Context-link, and external-provider
   metadata without copying original document bytes. It resumes at immutable
   projection boundaries after interruption.
5. Run `personal-agent-sync reconcile` and verify the queue.
6. Start it with `personal-agent-sync run`, or install the per-user background
   service with `personal-agent-sync install-agent`.

`personal-agent-sync status` shows only opaque Connection and document IDs,
queue state, and stable error codes. It does not print local roots.

Prepared migration payloads can still be uploaded with `personal-agent-sync
import`. Remote storage contains no operational Finder locator. Source-derived
text or user-authored Context content may contain a literal path when that text
was part of the record; it is treated as content, never as filesystem authority.

`reconcile_seconds` controls queue retry and root-recovery checks. Filesystem
events are coalesced for `event_debounce_seconds`; `full_reconcile_seconds`
controls the missed-event safety scan. The defaults are 15 seconds, 2 seconds,
and 15 minutes respectively.
