---
name: refresh-corpus-sources
description: Refresh registered local Corpus Source indexes after source files change, when freshness is in doubt, or when the user asks to sync Corpus. Do not use for Context revision, Work-file editing, registration changes, or remote-file hydration.
---

# Refresh Corpus Sources

Treat each registered source folder as canonical and its Corpus index as a derived projection. Run the bundled refresher rather than reconstructing the synchronization loop:

```bash
python3 "${SKILL_DIR}/scripts/refresh_sources.py"
```

To refresh only explicitly named registrations, repeat `--corpus`:

```bash
python3 "${SKILL_DIR}/scripts/refresh_sources.py" --corpus SOURCE_ID
```

The script scans every selected registration, refreshes locally available pending documents in bounded batches, and stops after at most four passes per source. It never enables remote hydration.

Do not modify source files, source scope, registration roots, or registrations. If a registered root is unavailable, report it; do not rebind, unregister, delete, or substitute another path.

Use the script's final JSON and exit status as the result. A source is successfully refreshed when its scan completes and no locally refreshable work or unexplained outdated projection remains. An outdated projection is a warning only when the script can attribute every such projection to an oversized file, an unavailable remote file, or a current extraction failure. `partial` projections and these non-actionable gaps may remain; report them as warnings rather than repeatedly retrying them.

Report how many sources were checked, which documents changed or were indexed, and any unresolved errors or warnings. Distinguish a completed refresh from a fully covered source.
