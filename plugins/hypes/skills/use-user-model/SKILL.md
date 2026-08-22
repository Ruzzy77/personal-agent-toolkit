---
name: use-user-model
description: "Use Hypes when a stored relationship could change the answer, when the user asks what Hypes models, or when an interaction reveals a reusable relationship worth refining. Hypes may maintain this revisable model in the background without a separate save request."
---

# Use the user relationship model

Answer the user directly and keep Hypes in the background unless the user asks about it. The current
message always takes priority over stored relationships.

Use Hypes when one of these conditions holds:

- a stored term or relationship is an explicit input to the requested result;
- an existing relationship could change an interpretation, explanation, question, or choice;
- the user asks what Hypes currently models;
- the current interaction reveals, corrects or weakens a relationship that could help later.

Do not call Hypes for an unrelated request, ordinary retrieval, or a literal transformation that does
not depend on a stored relationship.

## Read

Read only the smallest relevant graph slice when the answer depends on a stored relationship or an
existing object must be found before replacement. Start with a few short `focus` terms, or use
returned Node or Predicate refs when they are already known. Use the outline and `continuation` only
when the user asks to inspect the whole model.

When the answer depends on a relationship, check the relevant Edge rather than inferring it from a
Node name. Stop when the needed relation is found or the bounded relevant slice shows no match; do
not widen into unrelated graph areas.

## Rewrite

Refine Hypes in the background when the interaction gives a useful basis for a reusable
relationship. A separate save request, preview or confirmation is not required. Read first when an
existing relation may need replacement or deletion; a clearly new relation can be written directly.
Applying an existing relation or merely completing a task is not by itself a reason to write.

Do not model operational cautions, verification thresholds, completion states, evidence taxonomies,
stop conditions or one-task QA procedures as broad user preferences. When a correction reveals a
reusable style or interpretation preference, store that preference without carrying over the audit
vocabulary of the task that revealed it. Stored relationships guide interpretation; their names,
labels and qualifiers are not headings or a required structure for the answer.

Apply one atomic patch:

- replace existing Node, Predicate, or Edge values instead of accumulating duplicates;
- delete a Node or Predicate directly when that concept should disappear; incident Edges are removed atomically;
- use short reusable aliases that describe the concept rather than the conversation;
- revise or remove a relation when the current interaction no longer supports it.

Never store transcripts, task or project facts, source text, credentials, direct identifiers,
sensitive traits, broad personality judgments, Sense guidance, Corpus context, hidden reasoning, or
the assistant’s own answer. Hypes is the agent's revisable model, not an objective fact or
user-approved instruction. Do not report ordinary background maintenance unless the user asks.

When the user asks what Hypes models, describe only the relevant relationships and make clear that
they are the agent’s current revisable view. When deleting, inspect the affected slice so the semantic effect is understood; deleting a Node or Predicate removes its incident Edges atomically.
