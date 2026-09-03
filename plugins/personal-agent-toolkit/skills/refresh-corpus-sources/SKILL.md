---
name: refresh-corpus-sources
description: Refresh exact registered Corpus Source documents through the owner Sync app after source files change, when a materially improved extraction is explicitly needed, when freshness is in doubt, or when the user asks to sync Corpus. Do not use for Context revision, Work-file editing, registration changes, or remote-file hydration.
---

# Refresh Corpus Sources

Treat the extracted Corpus record as durable and the registered Source as the
authoritative update input when it is locally available. Ordinary questions use
the committed record without reopening the original file.

## On-demand remote refresh

1. Open the relevant Space and search its committed Source records.
2. Select the exact `connection_id` and `document_id` that require a current
   reread. Do not infer a document from a similar filename.
3. Call `corpus_source_refresh`. Include `expected_revision_sha256` when the
   current workflow has an exact last-known digest; otherwise omit it rather
   than inventing one.
4. A response containing `pending=true` is only an accepted queue entry. Follow
   its `job_id` with `corpus_job_status`. Completion requires a succeeded job
   response with `completed=true`, `revision_sha256`, and `projection_id`.
5. If Sync reports that the root or document is unavailable, ask the user to
   reconnect or rebind it. If the selected analyzer route requires approval,
   ask only for the exact revision and transfer ceiling reported by Sync.

Sync rechecks the current Connection role, generation, local availability, and
analyzer policy for every request. A Source Connection remains read-only: the
refresh reads an immutable temporary capture and changes only the durable
extraction projection. A failed extraction must leave the last successful
remote record active.

An explicit refresh re-analyzes unchanged bytes so an updated Document Files
extractor can produce a new projection. The new projection is committed before
the prior unprotected extractor projection is removed.

## Automatic file changes

The Personal Agent Sync background service detects Finder changes, coalesces
short event bursts, retries a bounded queue, and performs a slower safety scan.
Do not create a recurring AI refresh task merely to keep registered files
current. Use on-demand refresh when the task needs present-day fidelity or a
specific document needs materially improved extraction. Do not refresh merely
because an analyzer build or configuration identity changed; a deliberate
per-format reanalysis generation handles the exceptional bounded bulk case.

## Local maintenance fallback

Use the bundled refresher only for deliberate local development, initial
migration repair, or a local Corpus runtime that is not connected to the remote
Sync path:

```bash
python3 "${SKILL_DIR}/scripts/refresh_sources.py"
```

To refresh only explicitly named local registrations, repeat `--corpus`:

```bash
python3 "${SKILL_DIR}/scripts/refresh_sources.py" --corpus SOURCE_ID
```

The script scans selected registrations, refreshes locally available pending
documents in bounded batches, and stops after at most four passes per source.
It never enables remote hydration. Previously approved large documents are
handled one at a time; a changed identity requires a new explicit local
approval rather than a raised global limit.

## Result

Report `record_state` separately from `source_state`. An unavailable Source does
not make its last committed record unusable. Report a refresh as complete only
from the completed remote job or the local script's successful final result.
Distinguish a completed refresh from complete format coverage, and retain
partial extraction or policy-blocked gaps as explicit warnings rather than
repeatedly consuming analysis and storage writes.
