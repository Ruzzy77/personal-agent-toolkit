---
name: refresh-corpus-sources
description: Refresh registered local Corpus Source indexes after source files change, when freshness is in doubt, or when the user asks to sync Corpus. Do not use for Context revision, Work-file editing, registration changes, or remote-file hydration.
---

# Refresh Corpus Sources

Treat each registered source folder as an update input, its extracted Corpus record as durable, and FTS as a rebuildable projection. Run the bundled refresher rather than reconstructing the synchronization loop:

## One-time refresh

```bash
python3 "${SKILL_DIR}/scripts/refresh_sources.py"
```

To refresh only explicitly named registrations, repeat `--corpus`:

```bash
python3 "${SKILL_DIR}/scripts/refresh_sources.py" --corpus SOURCE_ID
```

## Monitored refresh

For a recurring monitor, keep its private warning history outside the plugin and pass the same path on every run:

```bash
python3 "${SKILL_DIR}/scripts/refresh_sources.py" --warning-state "${STATE_PATH}"
```

The first successful run initializes a baseline for each selected source instead of reporting every existing warning as new. A later run reports `new`, `increased`, and `reappeared` items as alerting changes. Mention `resolved` and `decreased` items only when useful, and do not repeat unchanged warnings. A failed refresh does not advance the baseline.

After a coordinated extractor release and successful reindex, replace the old baseline once:

```bash
python3 "${SKILL_DIR}/scripts/refresh_sources.py" \
  --warning-state "${STATE_PATH}" \
  --reset-warning-baseline
```

Do not reset the baseline merely to hide an unresolved warning.

## Boundaries

The script scans every selected registration, refreshes locally available pending documents in bounded batches, and stops after at most four passes per source. It never enables remote hydration. A failed new extraction must leave the last successful record active.

Previously approved large documents are refreshed one at a time after the ordinary bounded passes. Approval remains an explicit local user action. If the source identity changed, leave it skipped until the user approves the current file again; do not raise the global file limit or approve a replacement automatically.

Do not modify source files, source scope, or registrations. Corpus may update a registered root automatically only when macOS proves that a same-volume Finder move retained the saved filesystem identity. If identity resolution fails, report the root as unavailable; do not guess a replacement, unregister it, or discard its durable records.

## Result

Use the script's final JSON and exit status as the result. A source is successfully refreshed when its scan completes and no locally refreshable work or unexplained outdated projection remains. An outdated projection is a warning only when the script can attribute every such projection to an oversized file, an unavailable remote file, or a current extraction failure. `partial` projections and these non-actionable gaps may remain; report them as warnings rather than repeatedly retrying them.

Report how many sources were checked, which documents changed or were indexed, and any unresolved errors. Report `record_state` separately from `source_state`; an unavailable source does not imply that its saved record is unusable. In a one-time refresh, summarize current warnings. In a monitored refresh, follow the warning delta instead of restating the complete warning inventory. Distinguish a completed refresh from a fully covered source.
