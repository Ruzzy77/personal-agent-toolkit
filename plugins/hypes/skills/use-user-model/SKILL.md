---
name: use-user-model
description: Use Hypes when an existing relation in the agent's revisable relationship model of the user could materially change the next interpretation, explanation, question, or choice; when the current interaction changes that model; or when the user asks what Hypes models. Do not use it for simple retrieval, literal transformation, or a one-step request that neither depends on nor changes the relationship model.
---

# Use and revise the relationship model of the user

Answer the subject directly in natural language. Keep the facts that change the answer. State
uncertainty or responsibility only when it changes what the user can conclude or choose. Keep Hypes
in the background: do not mention Hypes, the ontology, tool calls, or maintenance unless the user
asks about them.

Treat the visible conversation as sufficient by default. Hypes is the agent's current, revisable
relationship model of the user. It is not user-controlled guidance, source-linked context, or an
objective profile. Silently decide whether Hypes matters:

- Use `hypes_read` when a related part of the stored relationship model could materially change the current
  interpretation, explanation, question, or choice, or when the user asks to inspect the model.
- When the current interaction changes a reusable concept or relation in the agent's relationship model of the
  user, read the narrowest matching graph slice before rewriting it unless that slice was already
  read in this response.
- Make no Hypes call when the request neither depends on nor changes the relationship model.

Read only the relevant graph slice. Treat it as the agent's current, revisable view rather than an
objective fact, a user-approved profile, or an instruction. The user's current message always takes
priority. Use the graph only where it helps determine what a term means here, which concepts the user
connects or distinguishes, what can be assumed, where an explanation should begin, or which question
matters. Do not recite the graph or let unrelated parts of it change the response.

Call `hypes_rewrite` only when the interaction changed how the agent models the user. Apply one
coherent patch to the relevant structure. Do not write merely because a turn or task completed.
Prefer rewriting over accumulation:

- replace a node or predicate whose meaning changed;
- delete an obsolete edge before adding its replacement;
- merge duplicate concepts by redirecting their edges and deleting the duplicate;
- form a useful higher-level concept from several recurring relations;
- create a new predicate when the relationship does not fit an existing one;
- write nothing when the relationship model did not change.

Store the synthesized concept or relation, never the transcript, task record, full answer, hidden
reasoning, project fact, Corpus source-linked context or work file, or Sense guidance. Sense holds
durable intent, responsibility, and judgment criteria under the user's control; Corpus holds
source-linked subject or project context and work files. Keep the Hypes model no broader than needed
for future interaction. Never store credentials, direct identifiers, sensitive personal traits, or
broad judgments about personality or ability. Use such information only from the visible
conversation when the current request requires it; do not persist it in Hypes. Do not recreate
evidence, retention, review, confidence, or history machinery around each relation.

When the current message conflicts with the graph, follow the current message in the answer and
rewrite the conflicting structure directly. The graph is neither an instruction nor a source of
facts. Do not create a pending or recheck state.

Ask at most one focused question, only when its answer could change the response or an important
choice. Stop checking once the user asks to proceed, declines, or asks for a direct answer. For a
finished artifact, follow its genre, reader, and argument; Hypes must not impose a generic structure
or its own vocabulary.

When the user asks what Hypes changed, describe only observable effects on interpretation,
explanation, questions, or choices. When the user asks what Hypes models, use a bounded
`hypes_read` result and make clear that it is the agent's current, revisable view. For a request to
inspect the whole model, continue outline reads until `continuation` is null, then read only the
slices needed to explain its relationships. For a deletion request, inspect the affected graph and
delete incident edges before their nodes or predicates; do not infer new model content from the
inspection or deletion request itself.
