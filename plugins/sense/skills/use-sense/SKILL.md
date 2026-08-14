---
name: use-sense
description: Use Sense when an important choice may depend on durable intent, responsibility, or a lesson that remains useful in different contexts. Also use it when the user asks to see or change Sense. Do not use it for simple retrieval, literal transformation, or a direct one-step request.
---

# Use Sense

Sense is background for a choice, not a template for the answer. Do not turn its section names or
wording into headings, lists, or policy language in the answer.

1. Read `sense_read` with `view=index`, then only the sections that could change the choice. Reuse
   the same revision during a direct continuation.
2. Check current facts in the current project and its original results. The current request and
   current sources take priority over retained guidance.
3. Reach your own conclusion and answer the subject directly. Do not mention Sense or its internal
   categories unless the user asks or the distinction is necessary.

If Sense is unavailable, continue from the conversation and current sources. Diagnose it only when
the user asks about Sense.

Keep each kind of information in one place:

- project facts and methods stay with the project;
- continuing questions and source-linked relationships stay in the Corpus chosen by the user;
- the agent's revisable concepts and relations about the user stay in Hypes;
- Sense keeps only guidance that should affect important choices in different contexts.

Revise Sense only when the user explicitly asks to save an explicit correction or observed result
as such guidance. Read every affected section first, preserve anything still valid, and finish all
replacement wording in the conversation before any write. Put all related final replacements into
one `sense_preview_revision`, show the combined result when review is needed, and send one
`sense_revise_batch` with a unique idempotency key. If the same section appears more than once,
keep only its last final replacement. Do not call `sense_revise` sentence by sentence, create a
second write while approval is pending, or retry a revision conflict in the same response. The saved
revision must store no conversation text, hidden reasoning, or project facts.

Use `sense_overview` when the user asks to review Sense. Use `sense_control` only for an explicit
request to inspect, export, or remove Sense data.
