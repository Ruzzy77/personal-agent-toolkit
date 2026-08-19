---
name: show-corpus-overview
description: Show a read-only view of saved Corpus contexts, their connected files and provider records, and material that needs a fresh read. Use when the user wants to choose a context or see registered collections. Do not investigate sources or change Corpus data in this view.
---

# Show Corpus

Call `corpus_space_list`. Show Space titles and Context purposes first, then visible Connections, roles, access, permission, `source_state` and Current File when present.

Call `corpus_space_get` only when the user asks for one Space's Context items. Continue with `next_offset` only when the remaining items matter.

Keep Context and Connections distinct. A Context is saved working understanding; a Connection is a visible Source or Work location. Report the single `source_state` value instead of expanding internal index diagnostics.

This view is read-only. Do not scan, refresh, register or change Context to make it look current. A local-only Connection omitted from the remote response is unknown, not empty.

Keep the result as concise text. For a substantive follow-up, switch to `investigate-corpus`; read Source text only when the saved Context is not enough.
