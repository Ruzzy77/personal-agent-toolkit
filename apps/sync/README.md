# Personal Agent Sync

Personal Agent Sync is the only component that receives Finder-folder authority.
It opens an outbound WebSocket to the remote context service; it does not listen
on a local port and does not expose the Mac through a tunnel.

## Responsibilities

- retain absolute folder locators, filesystem identities, and transfer approvals
  only in the private local database;
- recover same-volume Finder renames and moves by directory identity on macOS;
- reconcile Source changes into a bounded, coalescing queue and retry after
  network or analyzer failures;
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

1. Copy `config.example.toml` to the app configuration location and replace the
   example values. Paths in this file are local runtime settings and are never
   uploaded.
2. Install the app and its dependencies in a private environment.
3. Store the provisioned device token with `personal-agent-sync set-credential`.
4. Run `personal-agent-sync validate`, then `personal-agent-sync reconcile`.
5. Start it with `personal-agent-sync run`, or install the per-user background
   service with `personal-agent-sync install-agent`.

`personal-agent-sync status` shows only opaque Connection and document IDs,
queue state, and stable error codes. It does not print local roots.

Prepared, path-free migration payloads can be uploaded with
`personal-agent-sync import`. Automatic export from the existing local stores is
kept in the product-specific migration adapter so it can follow the finalized
Corpus durable-record schema rather than duplicating it here.
