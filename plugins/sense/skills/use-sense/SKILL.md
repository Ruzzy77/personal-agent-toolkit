---
name: use-sense
description: Use Sense when an important choice may depend on durable intent, responsibility, or a lesson that remains useful in different contexts. Also use it when the user asks to see or change Sense. Do not use it for simple retrieval, literal transformation, or a direct one-step request.
---

# Use Sense

Sense is background for a choice, not a template for the answer.

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
- narrow clues about understanding or helpful explanations stay in Hypes;
- Sense keeps only guidance that should affect important choices in different contexts.

Revise Sense only after an explicit correction or observed result establishes such guidance. Read
the exact section first, preserve anything still valid, replace the whole section, and store no
conversation text, hidden reasoning, or project facts.

Use `sense_overview` when the user asks to review Sense. Use `sense_control` only for an explicit
request to inspect, export, or remove Sense data.
