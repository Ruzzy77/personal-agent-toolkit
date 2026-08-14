# Corpus update continuity

Codex captures plugin skills and MCP tools when a task starts, and Claude captures them when a
session starts. Installing a newer Corpus package does not hot-reload that running host. A missing
`corpus_*` tool after an update is therefore not evidence that the private Corpus is empty or that
the enabled installation failed.

## Read-only fallback in an existing local session

Use the fallback only when the current host has local shell access and its exact checks pass:

- In Codex, run `codex plugin list --json`. Select entries whose `name` is `corpus` and whose
  `installed` and `enabled` fields are both true. Continue only when exactly one entry matches, its
  `pluginId` is `corpus@personal-agent-toolkit`, its `source.source` is `local`, and `source.path`
  is an absolute directory. The same marketplace must contain enabled
  `sense@personal-agent-toolkit` and `hypes@personal-agent-toolkit` entries.
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
context reads. The launcher rejects every other command before invoking the Corpus CLI. On a
package that already contains the Space surface, it also permits read-only Space listing, Source
search, live file listing, and live file reads. It never permits a write, Current File selection,
restore, Connection change, or any legacy Workspace command.

Never use the fallback for `scan`, `sync`, refresh, ingest, remote hydration, registration or source
scope changes, source binding/observation/refresh, context create/update/archive/migrate, migration,
cleanup, reconciliation, semantic commits, purge, or any other state change. If exact package
resolution fails, do not infer data absence or installation failure; stop the fallback.

## Updating and continuing work

Use this sequence for an owner or release update:

1. Build the candidate package and pass the complete clean-checkout validation, including package
   CLI and MCP smoke tests. A personalized projection must use a new
   `provider-packages/staged/personal-agent-toolkit-<build>` path; direct build to the canonical
   path is rejected. Require the marketplace name and display name to be
   `personal-agent-toolkit` and `Personal Agent Toolkit`, and keep the staged candidate.
2. Activate the staged candidate and retain the returned activation ID. Activation writes a
   private transaction journal outside the canonical tree and preserves the previous projection.
   Do not start another activation while this transaction is pending. Use `--activation-status`
   after an interruption.
3. Run `--install-verify-activation` with the exact activation ID. It installs
   `sense@personal-agent-toolkit`, `corpus@personal-agent-toolkit`, and
   `hypes@personal-agent-toolkit`, then requires one local marketplace at the canonical path and
   exact enabled versions and source paths for all three plugins. If that exact marketplace was
   absent before activation, this step registers it and records that fact in the transaction.
   A failed check keeps the prior projection. Use `--rollback-activation` to restore it; rollback
   also reinstalls and verifies the previous three plugins, not only the canonical files. For a
   first installation, rollback removes the three new plugins and only the canonical marketplace
   registration added after activation, then verifies that both are absent before removing the new
   canonical projection.
4. Cross the refresh boundary for the host that was updated:
   - In Codex, ask the user to start a new task directly in the Codex UI. Do not use
     `fork_thread`, `create_thread`, or a delegated task as the transition. Continue with the
     original `thread://` reference or a concise handoff.
   - In Claude Code, run `claude plugin list --json`, verify the exact enabled version, use
     `claude plugin details corpus@personal-agent-toolkit` to check the three standard skills and
     Corpus MCP inventory; a private personalized build may additionally contain only the
     explicitly selected Context Skill discovery bridges. Check `claude mcp list`, then start a new
     Claude Code session.
   - In Claude Cowork, verify the plugin is enabled in the marketplace UI, then start a new local
     Cowork session.
5. The handoff must include the request, owner commit, installed build version, activation ID,
   completed checks,
   and remaining action.
6. In the new task or session, match the installed version to the package manifest and generated
   build identity, then inspect the actual MCP tool surface.
7. Require the stable default Corpus tool IDs and object output schemas before continuing the
   previous request and its explicit context:
   `corpus_space_list`, `corpus_space_get`, `corpus_space_search`, `corpus_file_list`,
   `corpus_file_read`, `corpus_file_write`, `corpus_file_select_current`, and
   `corpus_file_restore`. These are the eight default Space tools. Every listed tool must expose
   `outputSchema.type=object`; `corpus_space_list` must report `surface_revision=space-v2`. The
   former 20 Source, Context, maintenance, and Workspace tools must not be present in the default
   surface. Compare changed input schemas against the validated candidate instead of assuming
   compatibility.
8. Require the local/tunnel skill inventory to contain `investigate-corpus`,
   `show-corpus-overview`, and `work-in-corpus-folder`. A private personalized projection may add
   discovery bridges listed in its projection metadata, but no Context Skill instructions. Hosted
   remote Corpus intentionally keeps its separate read-only skill projection and is not changed by
   this local work-folder release.
9. Only after the new task or session passes these runtime checks, run `--finalize-activation` with
   the same activation ID. Finalization rechecks digests and installed versions before deleting the
   previous projection. It first renames that projection to its transaction-specific retired path,
   so rerunning finalization after an interrupted deletion completes the cleanup. It leaves the
   staged candidate in place for deliberate later cleanup.

Installation or a running host's inherited registry is not verification that it has the new
snapshot. A user-started Codex task, Claude Code session, or local Cowork session plus its version
and MCP checks is the accepted update boundary.
