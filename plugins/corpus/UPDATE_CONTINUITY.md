# Corpus update continuity

Codex captures plugin skills and MCP tools when a task starts. Installing a newer Corpus package
does not hot-reload that task. A missing `corpus_*` tool after an update is therefore not evidence
that the private Corpus is empty or that the enabled installation failed.

## Read-only fallback in an existing Codex task

Use the fallback only in local Codex with shell access and only when all of these checks pass:

1. Run `codex plugin list --json` and select entries whose `name` is `corpus` and whose
   `installed` and `enabled` fields are both true.
2. Continue only when exactly one entry matches, its `source.source` is `local`, and
   `source.path` is an absolute directory.
3. At that exact path, require `.codex-plugin/plugin.json`, `src/corpus/_build.py`, and executable
   `bin/corpus-readonly`. The manifest name and version must match the selected list entry, and
   `_build.py` must contain the same `BUILD_ID`.
4. Run only that package's `bin/corpus-readonly`. Do not resolve the launcher relative to the
   skill snapshot, search plugin cache directories, or choose a directory because its name or
   version looks newest.

The fallback allows only these existing CLI command shapes:

- `corpus list`
- `overview`
- `status`
- `inventory`
- `search`
- `read`
- `source list`
- `source fetch`
- `context list`
- `context show`

These map to the read-only MCP surface for listing, overview, status, inventory, lexical candidate
search, exact indexed-unit reads, linked-source metadata and exact completed-task fetches, and named
context reads. The launcher rejects every other command before invoking the Corpus CLI.

Never use the fallback for `scan`, `sync`, refresh, ingest, remote hydration, registration or source
scope changes, source binding/observation/refresh, context create/update/archive/migrate, migration,
cleanup, reconciliation, semantic commits, purge, or any other state change. If exact package
resolution fails, do not infer data absence or installation failure; stop the fallback.

## Updating and continuing work

Use this sequence for an owner or release update:

1. Build the candidate package and pass the complete clean-checkout validation, including package
   CLI and MCP smoke tests.
2. Replace an installed package only after the candidate passes.
3. After installation, ask the user to start a new task directly in the Codex UI. Do not use
   `fork_thread`, `create_thread`, or a delegated task as the update transition: programmatic tasks
   may inherit the caller or app's previous plugin registry and do not guarantee a fresh snapshot.
4. Continue the previous request in that UI-started task by including the original task as a
   `thread://` reference or by supplying a concise handoff with its request, owner commit,
   installed build version, completed checks, and remaining action.
5. In the UI-started task, verify the exact enabled package version with
   `codex plugin list --json`, match it to the package manifest and generated build identity, and
   inspect the actual MCP `tools/list`.
6. Require the stable default Corpus tool IDs and object output schemas before continuing the
   previous request and its explicit context:
   `corpus_list`, `corpus_overview`, `corpus_status`, `corpus_inventory`,
   `corpus_search_candidates`, `corpus_read`, `corpus_source_read`, `corpus_source_fetch`,
   `corpus_source_update`, `context_read`, `context_update`, `corpus_sync`, `corpus_scan`, and
   `corpus_refresh`. Every listed tool must expose `outputSchema.type=object`; compare changed input
   schemas against the validated candidate instead of assuming compatibility.

Installation, a programmatic task transition, and a task's inherited registry are not verification
that the task has the new snapshot. The user-created Codex UI task plus its version and tools-list
checks is the accepted update boundary.
