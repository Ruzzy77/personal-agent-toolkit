---
name: show-corpus-overview
description: Show a read-only view of saved Corpus contexts, their connected files and provider records, and material that needs a fresh read. Use when the user wants to choose a context or see registered collections. Do not investigate sources or change Corpus data in this view.
---

# Show Corpus

Call `corpus_overview` with five items per context. Show readable titles and purposes first, followed
by the kinds of material connected to them. Do not choose a context merely because it contains more
items.

Keep three things distinct:

- a saved context is earlier interpretation linked to sources;
- connected collections and provider records point to originals;
- indexed coverage describes what can be read, not how complete an interpretation is.

This view is read-only. Do not scan, refresh, register, archive, delete, or add context merely to
make it look current. If the inventory is incomplete, a linked source has changed, or items are
truncated, mention it only when it affects what the user can rely on.

Questions and gaps describe the subject or missing material, never the user's understanding. Local-
only collections omitted by the current connection remain unknown rather than empty.

When a visual overview is useful, follow
[overview-visual-spec.md](references/overview-visual-spec.md). Otherwise use a compact table and the
saved items for the selected context. If the user then asks to verify an item or answer a substantive
question, continue with `investigate-corpus` and read the exact current source units.
