---
name: show-corpus-overview
description: Show a read-only overview organized around the work contexts Corpus can help continue, the files, email, and completed agent work connected to them, and material that needs a fresh read. Use when the user asks what work Corpus knows, wants to choose a work context, or wants to see registered collections. Do not use for source investigation, index refresh, registration, archive actions, or persistent context changes.
---

# Show the Corpus overview

Present the work Corpus can help the user resume, then show the materials connected to it. Keep
source coverage separate from the agent's saved interpretation: more indexed text does not mean
better work interpretation, and saved context is not the same as a statement in the source.

## Read the saved state

1. Call MCP `corpus_overview` with five items per context when that tool is exposed in the current
   task.
2. If `corpus_overview` is absent, do not treat that as an empty Corpus or a failed installation.
   A task or session that was already open can keep the plugin version it started with. When local
   shell access is available, follow the exact enabled-package checks in
   [UPDATE_CONTINUITY.md](../../UPDATE_CONTINUITY.md). Only after every check passes, run that
   package's `launchers/corpus-readonly overview --max-items-per-context 5`. This private overview
   also includes `local_only` corpora.
3. Never guess a package path, search caches, or choose a package because it looks newest. If the
   installed package cannot be verified exactly, stop without inferring anything about the user's
   data.
4. Start with five items per context and increase only when the user asks to inspect more.
5. Treat the response as a stored-state view. Do not call `corpus_scan`, `corpus_sync`,
   `corpus_refresh`, `context_update`, or provider mutation tools merely to make the overview look
   current.
6. Preserve the returned execution boundary. A local-only corpus omitted from MCP output stays
   omitted; do not weaken its policy. Mention the omission only when it changes the user's reading
   of the overview.
7. When `inventory_complete=false`, a scan timestamp is old, context items are truncated, or a
   source has partial extraction, show that condition without turning it into a generic warning.

The fallback is read-only. Never use it for scan, sync, refresh, downloads, registration, source
changes, context changes, archive, migration, cleanup, or deletion. If the request needs a newly
installed tool or any persistent change, tell the user that the current task has not loaded the
update and ask them to start a new task or session after the installation is checked. Do not create,
fork, or delegate that transition on the user's behalf because it may retain the older plugin. Do
not include build identifiers, package paths, or tool inventories in the normal handoff; show them
only when diagnosing a failure.

## Build one integrated view

Read [overview-visual-spec.md](references/overview-visual-spec.md) before composing the view. When
the `visualize` skill is available, use it to create one interactive overview rather than separate
index and context charts. Otherwise return a compact Markdown table followed by grouped context
items for the selected corpus.

The first view must let the user answer:

- Which ongoing body of work should I continue?
- What kinds of outputs recur in that work?
- What files, email, and completed agent work belong with it?
- What source-linked work interpretation and unresolved work or source questions have been carried
  forward?
- What needs to be read again before relying on it?

Questions and gaps in this view describe unresolved work, missing material, evidence, or source
coverage. Do not present them as the user's misunderstanding, skill level, or cognitive state; that
kind of topic-, task-, or responsibility-specific understanding belongs with Hypes.

Preserve a work context the user has selected. If the current request clearly names one context,
open that context first. Otherwise show a compact list of work contexts without choosing one by
item count. Collections without a saved work context remain available as source collections.
Keep document counts and provider details behind the first layer unless they change what the user
can continue.

## Keep overview and investigation separate

Use context item text as a readable map of saved work interpretation. Do not reopen source units,
validate claims, infer new project status, or add new context items while preparing the overview. If
the user selects a corpus or item and asks to check it against current sources, compare changes, or
answer a substantive question, hand that follow-up to `investigate-corpus`.

`stale_item_count` identifies current interpretations whose linked source needs review.
`archived_contexts` contains completed contexts that remain readable. Do not present legacy
superseded-item counts as part of the current work overview.

When the visualization supports a drill-down action, send a follow-up message that names the selected
corpus and asks Corpus to verify the selected item against exact current sources. The overview itself
must remain read-only.

## Report only decision-relevant limits

Outside the visual, mention only conditions that affect use, such as an omitted local-only corpus,
an incomplete inventory, a materially stale scan, or truncated context. Do not repeat every number
already visible and do not describe implementation details.
