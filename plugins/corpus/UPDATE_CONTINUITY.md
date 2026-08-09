# Corpus update continuity

Codex captures plugin skills and MCP tools when a task starts, and Claude captures them when a
session starts. Installing a newer Corpus package does not hot-reload that running host. A missing
`corpus_*` tool after an update is therefore not evidence that the private Corpus is empty or that
the enabled installation failed.

## Read-only fallback in an existing local session

Use the fallback only when the current host has local shell access and its exact checks pass:

- In Codex, run `codex plugin list --json`. Select entries whose `name` is `corpus` and whose
  `installed` and `enabled` fields are both true. Continue only when exactly one entry matches, its
  `source.source` is `local`, and `source.path` is an absolute directory.
- In Claude Code, run `claude plugin list --json`. Continue only when exactly one enabled entry has
  an `id` beginning with `corpus@` and an absolute `installPath`. At that path, require the version
  in `.claude-plugin/plugin.json` to match the listed version.
- In Claude Cowork or any host without local shell access, do not use this fallback. Start a new
  local session that exposes the Corpus MCP tools.

At the exact Codex `source.path` or Claude Code `installPath`, require `src/corpus/_build.py` and an
executable `launchers/corpus-readonly`. `_build.py` must contain the selected package's version as
its `BUILD_ID`. Run only that package's `launchers/corpus-readonly`. Do not resolve the launcher
relative to the skill snapshot, search plugin cache directories, or choose a directory because its
name or version looks newest.

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
3. Cross the refresh boundary for the host that was updated:
   - In Codex, ask the user to start a new task directly in the Codex UI. Do not use
     `fork_thread`, `create_thread`, or a delegated task as the transition. Continue with the
     original `thread://` reference or a concise handoff.
   - In Claude Code, run `claude plugin list --json`, verify the exact enabled version, use
     `claude plugin details corpus@MARKETPLACE` to check the two skills and Corpus MCP inventory,
     and check `claude mcp list`. Then start a new Claude Code session.
   - In Claude Cowork, verify the plugin is enabled in the marketplace UI, then start a new local
     Cowork session.
4. The handoff must include the request, owner commit, installed build version, completed checks,
   and remaining action.
5. In the new task or session, match the installed version to the package manifest and generated
   build identity, then inspect the actual MCP tool surface.
6. Require the stable default Corpus tool IDs and object output schemas before continuing the
   previous request and its explicit context:
   `corpus_list`, `corpus_overview`, `corpus_status`, `corpus_inventory`,
   `corpus_search_candidates`, `corpus_read`, `corpus_source_read`, `corpus_source_fetch`,
   `corpus_source_update`, `context_read`, `context_update`, `corpus_sync`, `corpus_scan`, and
   `corpus_refresh`. Every listed tool must expose `outputSchema.type=object`; compare changed input
   schemas against the validated candidate instead of assuming compatibility.

Installation or a running host's inherited registry is not verification that it has the new
snapshot. A user-started Codex task, Claude Code session, or local Cowork session plus its version
and MCP checks is the accepted update boundary.
