---
name: show-corpus-overview
description: Show a concise read-only view of saved Corpus contexts, connected files, provider records, and source freshness.
---

# Show Corpus

Call `corpus_space_list`. Show Space titles and Context purposes first, then visible Connections, roles, access, permission, `source_state` and Current File when present.

`corpus_space_get` opens the Space selected by the user. `next_offset` continues relevant Context items.

Keep Context and Connections distinct. A Context is saved working understanding; a Connection is a visible Source or Work location. Report the single `source_state` value instead of expanding internal index diagnostics.

This view presents current read-only metadata and does not perform revisions. Scan, refresh, registration, Context creation or archival, item creation or deletion, and Source-link changes remain local operations. An explicit follow-up may revise existing item kind, body and status or replace the complete Context Skill through `investigate-corpus` and the version-checked Chat tools. An omitted local Connection has unknown remote state.

Keep the result concise. A substantive follow-up continues through `investigate-corpus`; current Source text supplies details beyond saved Context.
