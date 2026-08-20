---
name: use-sense
description: Use Sense when an important choice may depend on durable intent, responsibility, or a lesson that remains useful in different contexts. Also use it when the user asks to see or change Sense. Do not use it for simple retrieval, literal transformation, or a direct one-step request.
---

# Use Sense

Sense is background for a choice, not a template for the answer. Do not turn its section names or
wording into headings, lists, or policy language in the answer.

Read `sense_read` with `view=index`, then only the sections that could change the choice. When the
relevant section is already known in a direct continuation, read it without reopening the index.
Current requests, facts and sources take priority. Reach your own conclusion and do not mention
Sense or its internal categories unless the user asks.

If Sense is unavailable, continue from the conversation and current sources. Diagnose it only when
the user asks about Sense.

Sense keeps only guidance that should affect important choices in different contexts. Do not copy
source material, source locators, conversation text, hidden reasoning or one project's facts into it.

Revise Sense only when the user asks to save durable guidance. Read every affected section first and
preserve anything still valid. If the assistant drafted the wording or several sections change, show
the complete final wording in the conversation before writing. Then call `sense_revise` once with
each section's `section_sha256` and complete replacement. An exact repeated final state is a no-op.
Do not retry a section conflict in the same response.

Sensitive changes and permanent deletion require a trusted local command. Use `sense_overview` when
the user asks to review the current ordinary guidance.
