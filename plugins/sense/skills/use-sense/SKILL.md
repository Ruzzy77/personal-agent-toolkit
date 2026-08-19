---
name: use-sense
description: Use Sense when an important choice may depend on durable intent, responsibility, or a lesson that remains useful in different contexts. Also use it when the user asks to see or change Sense. Do not use it for simple retrieval, literal transformation, or a direct one-step request.
---

# Use Sense

Sense is background for a choice, not a template for the answer. Do not turn its section names or
wording into headings, lists, or policy language in the answer.

Read `sense_read` with `view=index`, then only the sections that could change the choice. When the
relevant section is already known in a direct continuation, read that section without reopening the
index. Current requests, facts and sources take priority over retained guidance. Reach your own
conclusion and do not mention Sense or its internal categories unless the user asks.

If Sense is unavailable, continue from the conversation and current sources. Diagnose it only when
the user asks about Sense.

Sense keeps only guidance that should affect important choices in different contexts. Do not copy
source material, conversation text, hidden reasoning or one project's facts into it.

Revise Sense only when the user asks to save a correction or observed result as durable guidance.
Read every affected section first and preserve anything still valid. If the user supplies the final
ordinary wording, call `sense_revise_batch` once with that wording and a unique idempotency key.

Use `sense_preview_revision` before the write when the assistant drafted the replacement, the change
spans several sections, or the user asks to review it. Preview all related replacements together,
then save the reviewed batch once. Do not require a preview for an explicit final ordinary
replacement. Sensitive or scope-expanding persistence remains a trusted local action; do not retry
it through Chat. Do not retry a revision conflict in the same response.

Use `sense_overview` when the user asks to review Sense. Use `sense_control` only for an explicit
request to inspect, export, or remove Sense data.
