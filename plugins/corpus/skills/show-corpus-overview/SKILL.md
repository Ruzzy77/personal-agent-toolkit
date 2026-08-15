---
name: show-corpus-overview
description: Show a read-only view of saved Corpus contexts, their connected files and provider records, and material that needs a fresh read. Use when the user wants to choose a context or see registered collections. Do not investigate sources or change Corpus data in this view.
---

# Show Corpus

Call `corpus_space_list`. Show readable Space titles and Context purposes first, followed by visible
Connections, their Role, Access Scope, Permission, connection state, and Current File when present.
Open a Space with `corpus_space_get` only when the user asks for its Context items. Do not choose a
Space merely because it contains more items. Omit `context_limit` and `context_offset` on the
initial call. `context_limit` counts Context items, not characters, and must stay between 1 and
100. If `has_more` is true, pass `next_offset` as `context_offset` to read the next page.

Keep three things distinct:

- a saved Context is reusable working understanding built from Sources;
- Connections describe visible Source or Work locations without exposing local roots;
- indexed coverage describes what can be read, not how complete an interpretation is.

This view is read-only. Do not scan, refresh, register, archive, delete, or add context merely to
make it look current. If the inventory is incomplete, a linked source has changed, or items are
truncated, mention it only when it affects what the user can rely on.

Questions and gaps describe the subject or missing material, never the user's understanding.
Local-only Connections omitted by the Remote surface remain unknown rather than empty; do not infer
their names, count, paths, or content.

When a visual overview is useful, follow
[overview-visual-spec.md](references/overview-visual-spec.md). Otherwise choose the simplest readable
form and use a table only when comparison benefits from it. If the user then asks to verify an item
or answer a substantive question, continue with `investigate-corpus` and read the exact current
Source text through `corpus_space_search` and `corpus_file_read`.
