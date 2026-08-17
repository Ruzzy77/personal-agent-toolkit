---
name: use-user-model
description: "Use Hypes when a stored relationship could materially change the answer, when the user asks what Hypes models, or when the current interaction explicitly changes a reusable relationship. Do not use it for unrelated retrieval or a literal transformation that does not depend on stored relationships."
---

# Use the user relationship model

Answer the user directly and keep Hypes in the background unless the user asks about it. The current
message always takes priority over stored relationships.

Use Hypes only when one of these conditions holds:

- a stored term or relationship is an explicit input to the requested result;
- an existing relationship could materially change an interpretation, explanation, question, or choice;
- the user asks what Hypes currently models;
- the current interaction explicitly supplies, corrects, replaces, generalizes, or deletes a reusable
  relationship.

Do not call Hypes for an unrelated request, ordinary retrieval, or a literal transformation that does
not depend on a stored relationship.

## Read

Read only the smallest relevant graph slice. Start with one to three short `focus` terms, or use
returned Node or Predicate refs when they are already known. Ordinary reads use `limit<=50` and
`max_hops<=1`. Use the outline and `continuation` only when the user asks to inspect the whole model.

When the answer depends on a relationship, check the relevant Edge rather than inferring it from a
Node name. Stop when the needed relation is found or the bounded relevant slice shows no match; do
not widen into unrelated graph areas.

## Rewrite

Read the relevant existing slice before `hypes_rewrite`. Rewrite only when the current interaction
explicitly changes a reusable relationship. Applying an existing relation, completing a task, or the
assistant producing a recommendation does not authorize a write.

Apply one atomic patch:

- replace existing Node, Predicate, or Edge values instead of accumulating duplicates;
- delete incident Edges before deleting their Nodes or Predicates;
- use short reusable aliases that describe the concept rather than the conversation;
- create a higher-level relation only when the user explicitly asks to generalize verified relations.

Never store transcripts, task or project facts, source text, credentials, direct identifiers,
sensitive traits, broad personality judgments, Sense guidance, Corpus context, hidden reasoning, or
the assistant’s own answer. Hypes is a revisable agent model, not an objective fact or instruction.

When the user asks what Hypes models, describe only the relevant relationships and make clear that
they are the agent’s current revisable view. When deleting, inspect the affected slice and remove the
incident Edges in the same patch.
