---
name: show-corpus-overview
description: Show a read-only personal overview of registered Corpus collections, including source-index coverage, local and remote document states, linked provider records, reusable semantic context, stale items needing review, superseded history, archived contexts, open questions, and freshness. Use when the user asks to see, list, compare, audit, visualize, or choose among corpora, asks what Corpus currently knows, or asks how old interpretations are retained. Do not use for source investigation, index refresh, corpus registration, archive actions, or persistent context changes.
---

# Show Corpus Workspace

Present the current saved Corpus state as one workspace view. Keep source coverage and agent-authored
understanding visibly distinct: more indexed text does not mean better understanding, and a saved
context item remains an interpretation rather than an original-source claim.

## Read the saved state

1. Call MCP `corpus_overview` with five items per context when that tool is exposed in the current
   task.
2. If `corpus_overview` is absent, do not treat that as an empty Corpus or a failed installation.
   An already-running Codex task or Claude session can retain the plugin snapshot from before an
   update. In local Codex or Claude Code with shell access, use the host-specific exact
   enabled-package checks in
   [UPDATE_CONTINUITY.md](../../UPDATE_CONTINUITY.md). Only after every check passes, run that
   package's `launchers/corpus-readonly overview --max-items-per-context 5`. This personal CLI view also
   includes `local_only` corpora.
3. Never resolve fallback from this skill's directory, scan plugin caches, or choose a
   newest-looking package. If the enabled package is missing, ambiguous, or has a version or build
   mismatch, stop without inferring anything about the user's data.
4. Start with five items per context and increase only when the user asks to inspect more.
5. Treat the response as a stored-state view. Do not call `corpus_scan`, `corpus_sync`,
   `corpus_refresh`, `context_update`, or provider mutation tools merely to make the overview look
   current.
6. Preserve the returned execution boundary. A local-only corpus omitted from MCP output stays
   omitted; do not weaken its policy. Mention the omission only when it changes the user's reading
   of the overview.
7. When `inventory_complete=false`, a scan timestamp is old, context items are truncated, or a
   source has partial extraction, show that condition without turning it into a generic warning.

The fallback is read-only. Never use it for scan, sync, refresh, hydration, registration, linked
source changes, context changes, archive, migration, cleanup, or purge. If the request needs a
write or a tool introduced by the updated package, ask the user to start a new task or session
directly in the current host after validated installation. In Codex, do not use `fork_thread`,
`create_thread`, or a delegated task as the transition; carry a `thread://` reference or concise
handoff. In Claude Code, start a new session after checking the installed version and component
inventory. In Claude Cowork, confirm the plugin is enabled and start a new local session. Verify the
actual MCP tool surface there, and do not claim an in-place hot-reload.

## Build one integrated view

Read [overview-visual-spec.md](references/overview-visual-spec.md) before composing the view. When
the `visualize` skill is available, use it to create one interactive overview rather than separate
index and context charts. Otherwise return a compact Markdown table followed by grouped context
items for the selected corpus.

The first view must let the user answer:

- Which corpus should I open?
- How much of its supported source inventory is indexed, local but unindexed, or remote?
- Which provider records are linked?
- What reusable understanding, relationships, differences, questions, and gaps already exist?
- Which active interpretations need source review, and how much replaced or archived history exists?
- When was this state last observed?

Default to the corpus with an active reusable context and the largest current item count. Preserve a
user-selected corpus across presentation-only interactions. Keep the initial screen useful without
clicking.

## Keep overview and investigation separate

Use context item text as a readable map of saved understanding. Do not reopen source units, validate
claims, infer new project status, or add new context items while preparing the overview. If the user
selects a corpus or item and asks for evidence, changes, or a substantive answer, hand that follow-up
to `investigate-corpus`.

Show lifecycle counts without treating history as current knowledge. `stale_item_count` identifies
active interpretations whose linked source needs review. `superseded_item_count` is retained history,
and `archived_contexts` contains completed contexts that remain readable. Do not show superseded item
text by default, archive a context, or propose purge merely because these counts are nonzero.

When the visualization supports a drill-down action, send a follow-up message that names the selected
corpus and asks Corpus to verify the selected item against exact current sources. The overview itself
must remain read-only.

## Report only decision-relevant limits

Outside the visual, mention only conditions that affect use, such as an omitted local-only corpus,
an incomplete inventory, a materially stale scan, or truncated context. Do not repeat every number
already visible and do not describe implementation details.
