---
name: show-corpus-overview
description: Show a concise read-only view of saved Corpus contexts, connected files, provider records, and source freshness.
---

# Show Corpus

Call `corpus_space_list`. Show Space titles and Context purposes first, then visible Connections, roles, access, permission, `record_state`, `source_state`, and Current File when present.

`corpus_space_get` opens the Space selected by the user. `next_offset` continues relevant Context items.

Keep Context and Connections distinct. A Context is durable saved understanding; a Connection is a visible Source or Work location. Keep record usability separate from source availability rather than collapsing both into one freshness label. Do not expand internal index diagnostics unless the user asks.

This view presents current read-only metadata and does not perform revisions. An explicit follow-up may revise existing item kind, body and status or replace the complete approved Context Skill through `investigate-corpus` and the version-checked Chat tools. Context creation or archival, item creation or deletion, other attributes and Source-link changes are outside the current public MCP. Local Context commands modify only the development or migration store, not the remote canonical Context.

Finder registration and permissions remain local. An exact Source reread can use `refresh-corpus-sources` through the owner's Sync app; it updates Source records, not Context attributes or provenance. An omitted local Connection has unknown remote state.

Keep the result concise. A substantive follow-up continues through `investigate-corpus`; durable records supply captured detail, and a current Source check is needed only when the request requires present-day fidelity.
