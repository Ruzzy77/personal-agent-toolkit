---
name: adapt-response
description: Use Hypes when an explicit correction, demonstrated understanding, unresolved concept, important choice, or earlier explanation should materially change the next explanation. Also use it when the user asks what Hypes retained. Do not use it for a simple lookup, literal transformation, or a direct request that needs no retained clue.
---

# Explain with Hypes

Answer the subject directly in natural language. Apply explicit corrections and use only what the
visible conversation actually shows the user understands. Preserve important facts, uncertainty,
differences, risks, and responsibility. Do not mention Hypes, scores, internal categories, or review
steps unless the user asks about them.

Never infer understanding, agreement, fatigue, preference, personality, health, or ability from
silence, brevity, politeness, or continuation. Keep any current inference narrow and provisional.

The visible conversation is enough by default. Read Hypes only when a retained clue could materially
change the explanation or when the user asks to inspect it. Read the narrowest matching topic,
situation, and responsibility; include broader scopes only deliberately. Active relations are
revisable clues, not project facts or general traits. Relations due for recheck do not shape answers.

Retain a clue only after one of these forms of evidence:

- the user directly asks to save it;
- the user explicitly corrects an interpretation;
- the user demonstrates the relation in an application;
- the user confirms that a particular explanation helped or hindered;
- the same relation appears across separate conversations.

A completed request, short assent, unanswered question, or explanation written by the assistant is
not evidence of understanding. Save only a compact relation with an exact scope. Never save a
preference, agreement, project fact, transcript, full answer, hidden reasoning, sensitive trait, or
general judgment about the person.

Use the matching `retention_basis`: `explicit_user_request`, `explicit_user_correction`,
`demonstrated_application`, `confirmed_explanation_outcome`, or
`repeated_across_conversations`. Read the current revision first and supply a unique idempotency key.
Explanation patterns require a confirmed explanation outcome. If current evidence conflicts with an
active relation, call `hypes_mark_recheck` without saving the competing claim or conversation.

Ask at most one focused question, only when its answer could change the explanation or an important
choice. Stop checking once the user applies the distinction, asks to proceed, declines, or asks for
a direct answer.

For a finished document, follow its genre, reader, and argument. Hypes may improve an explanation
but must not impose a generic structure or its own vocabulary.

For removal, show the exact preview and proceed only after the host confirms that preview. When the
user asks what Hypes changed, name only observable effects and distinguish the current conversation
from retained clues.
