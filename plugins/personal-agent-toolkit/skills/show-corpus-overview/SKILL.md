---
name: show-corpus-overview
description: Show a concise read-only view of saved Corpus contexts, connected files, provider records, and source freshness.
---

# Show Corpus

Call `corpus_space_list`. Show Space titles and Context purposes first, then visible Connections, roles, access, permission, `record_state`, `source_state`, and Current File when present.

`corpus_space_get` opens the selected Space. Use `include_context_skill=false` when the exposed schema supports it and this view only needs Context content; include the Skill when its method is relevant. `next_offset` continues relevant Context items. Native documents and host-matched Workspace bindings may be shown through their exposed list or resolve tools when they are part of the user's requested view.

Keep Context and Connections distinct. A Context is durable saved understanding; a Connection is a visible Source or Work location. Keep record usability separate from source availability rather than collapsing both into one freshness label. Do not expand internal index diagnostics unless the user asks.

This overview is read-only. A follow-up or direct user action in the workbench may use the matching version-checked service operation through `investigate-corpus`. Legacy item revisions, native documents and Space or Workspace setup retain their separate contracts. Local Context commands modify only the development or migration store, not the remote canonical Context.

Finder registration and permissions remain local. An exact Source reread can use `refresh-corpus-sources` through the owner's Sync app; it updates Source records, not Context attributes or provenance. An omitted local Connection has unknown remote state.

Keep the result concise. A substantive follow-up continues through `investigate-corpus`; durable records supply captured detail, and a current Source check is needed only when the request requires present-day fidelity.
