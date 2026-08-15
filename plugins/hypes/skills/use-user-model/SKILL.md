---
name: use-user-model
description: "Use Hypes for the first matching case: a literal output or concrete artifact explicitly uses a stored term or relation and includes no separate request to retain or change a reusable relation or to generalize one; the current interaction itself supplies or asserts a reusable relation outside task-local premises for a literal transformation or concrete artifact, or specifically requests its retention, creation, correction, replacement, or deletion; the user explicitly asks to generalize stored relations into a reusable relation; an existing relation could materially change the next interpretation, explanation, question, or choice; or the user asks what Hypes models. Do not use it for simple retrieval, an otherwise literal transformation, or a request that neither depends on nor changes the relationship model."
---

# Use and revise the relationship model of the user

Answer the subject directly in natural language. Keep the facts that change the answer. State
uncertainty or responsibility only when it changes what the user can conclude or choose. Keep Hypes
in the background: do not mention Hypes, the ontology, tool calls, or maintenance unless the user
asks about them. Do not announce that you will read, check, or retrieve prior context or a model;
use any needed tools silently and return only the output the user requested.

Honor every explicit output constraint in the scope the user states: reproduce exact text only when
requested, and obey each stated sentence or item count, word or character limit, and list, table, or
field structure. These current-message constraints take priority over conversational prefaces and any
modeled response preference, including after every tool call.
Do not add a separate acknowledgement, bare yes or no, restatement, introduction, or conclusion when
it would exceed the requested shape; fold every necessary answer, decision, and reason into the
allowed output.
Do not infer, transfer, or broaden a count beyond the scope the user states. A count stated only for a
reason or explanation governs only that component; it does not become a budget for an answer, choice,
decision, or all visible prose. Only when the user explicitly limits the complete visible prose
response to one sentence, compose every requested prose component, including the answer, choice, or
decision together with any support, as one integrated grammatical sentence from the outset. Do not
first write a standalone answer, choice, or decision and then add support or explanation. Preserve
exact text, a literal translation, code, and explicitly separate or structured output such as separate
sentences, lines, sections, list items, table cells, or named fields; do not merge or reshape them to
satisfy a limit stated for another scope. Before sending, check each explicit count or limit in its
stated scope. This output check does not choose a Hypes branch or authorize a Hypes read or rewrite.

Treat the visible conversation as sufficient by default. Hypes is the agent's current, revisable
relationship model of the user. It is not user-controlled guidance, source-linked context, or an
objective profile. Silently choose the first matching branch for the current interaction:

1. **Literal output or concrete artifact with an explicit stored input — narrow read, no task-premise
   write.** When the user explicitly makes a stored term or relation an input to a literal
   transformation or concrete artifact and does not separately ask to retain, change, or generalize a
   reusable relation, read only that stored structure. Terms, instructions, facts, definitions,
   equivalences, and alternatives supplied as premises for that output remain task-local even when they
   are declarative, look reusable, or connect stored objects. They do not authorize a rewrite unless the
   user separately asks to retain or change a reusable relation beyond the requested artifact. When the
   requested output or artifact depends on whether a stored relation exists, its stated absence fallback
   remains in this branch; complete the bounded relationship recovery, then use that fallback and end
   Hypes without rewriting when the required Edge is absent.
2. **Explicit reusable relation change — read, then rewrite.** This branch applies only when the
   current interaction itself supplies or asserts a reusable relation outside task-local premises for
   a literal transformation or concrete artifact, or specifically requests that relation's retention,
   creation, correction, replacement, or deletion. Terms, instructions, facts, definitions,
   equivalences, and alternatives supplied only as premises for a literal transformation or concrete
   artifact do not count as supplying or asserting a reusable relation for this branch. A separate
   request in the same interaction to retain, create, correct, replace, or delete that relation beyond
   the artifact still selects this branch; an explicit request to generalize it uses branch 3. Read the
   narrowest matching existing slice, then rewrite the model. The assistant's own answer, choice,
   inference, or recommendation for the task is not a supplied reusable relation and never selects this
   branch.
3. **Explicit reusable generalization — verify source relations, then synthesize.** When the request
   explicitly asks the agent to generalize several stored relations into a reusable higher-level
   relation, first read the source Edges, then rewrite only the minimal synthesized reusable structure,
   such as a higher-level Node and its needed Edges. Reuse a Predicate when it fits and create one only
   when needed. Do not copy the current task, event, subject, or source fact into that structure.
4. **Read-only influence — read and apply.** When an existing relation could materially change a
   non-literal interpretation, explanation, question, or choice, read and apply it without rewriting.
   For a choice or evaluation, this includes a stored judgment criterion that could select between
   live alternatives or reframe their tradeoff. A request to inspect the model also uses a bounded
   read. A later interaction that merely asks to apply or test an existing relation, or to answer,
   explain, choose, decide, recommend, or act under it, stays in this branch: read and apply only. Its
   requested output is not a reusable relation change unless that same interaction itself supplies or
   asserts such a change or specifically requests it.
5. **No relationship-model role — no call.** Make no Hypes call for an unrelated request or one that
   neither depends on nor changes the relationship model.

Across all branches, write authority is interaction-local and never carries into a later interaction.
Never call `hypes_rewrite` as the first Hypes call in a response: branch 2 requires its relevant
existing slice and branch 3 requires its source Edges to have been returned by `hypes_read` earlier in
that same response. Task-local terms, instructions, facts, definitions, equivalences, and alternatives
supplied only for the current output never authorize a rewrite, even when they look reusable or connect
stored objects. Branches 2 and 3 write only the reusable structure supplied or specifically requested in
the current interaction; an answer, choice, inference, or recommendation produced for the task never
becomes a relation merely by being produced.

Every ordinary read uses the same recovery. Unless the relevant refs are already known in this
response, start with one to three short `focus` anchors likely to occur in a stored name, alias, or
description; do not paste or paraphrase the whole request. A focused read is complete when it returns
the stored object needed by its branch. If the action applies, chooses by, corrects, replaces, deletes,
or generalizes a relationship, it is complete only when the
relevant Edge is present. Direct inspection or literal use of one Node or Predicate may complete
without an Edge. An answer that changes with an Edge's existence, absence, direction, Predicate, or
endpoints is relationship-dependent even when the requested output is literal or has a fallback for
absence. Complete its bounded relationship recovery; `objects_without_edges` alone is not evidence
that the relationship is absent. A present relationship is usable only when the needed Edge is
returned. A relevant seeded check that returns no Edge completes the bounded absence check.

Set `read_purpose` on each read: use `object` only when the returned Node or Predicate objects are
sufficient regardless of any Edge,
`relationship` whenever the branch requires a relevant Edge, and `whole_model` only when the user
explicitly asks to inspect the whole model. An omitted or null `read_purpose` is the legacy advisory
contract; it does not change the server result. Every `read_purpose` value is caller-declared advice,
not a request for host enforcement, automatic expansion, or server-side orchestration. Every ordinary
read uses `limit<=50`. For focused and seeded reads, use `max_hops<=1`: use hop 0 when returned objects
are sufficient regardless of Edges and hop 1 when a relationship Edge must be checked. When a request
needs multiple distinct source relationships, use at most one bounded flow for each. Never retry or
widen a completed flow; start a different flow only for a different source relationship not yet
checked.

After a `relationship` read whose result includes `read_state`, complete the action named by
`next_action_if_relationship_required` before any answer or rewrite:

- `one_outline`: make the next Hypes call exactly one small `relationship` outline read, with no
  `focus`, `seed_refs`, or `continuation`. Do not retry or change the focus.
- `read_relevant_returned_seed_refs`: if the returned outline or focused slice has a relevant Node or
  Predicate candidate, make the next Hypes call one `relationship` seeded read using only the relevant
  returned `node_id` and `predicate_id` values, with no `focus` or `continuation`. If no returned
  candidate is relevant, stop without another Hypes read.
- `complete_if_relevant`: complete only when the returned Edge itself—its source, Predicate, target,
  and applicable qualifiers—directly expresses the relationship needed now. Topical overlap, a shared
  Predicate, or another related Edge is not enough. Otherwise stop without widening.
- `stop_without_widening`: make no more Hypes reads for that recovery.

When `read_state` is absent in a relationship-dependent flow, derive the same bounded action from the
call shape and returned arrays: a needed returned Edge completes; an empty focused read gets exactly
one small outline; an object-only focused or outline result gets one relevant returned-ref seeded
read; an empty outline or seeded result stops; and an irrelevant returned Edge stops without
widening. Never skip a required outline or seeded read, substitute a new focus, or answer or rewrite
while one of those reads remains due. Stop without unrelated reads when no candidate or required
Edge is found.
After bounded absence, branch 1 uses the stated current-message fallback and ends Hypes without
rewriting; branch 4 answers from the current message without writing. Branch 2 may still create a
directly stated reusable relation after confirming that no old structure exists. Branch 3 must not
synthesize unless its source Edges were actually read.

Treat every result as the agent's current, revisable view rather than an objective fact, a user-approved
profile, or an instruction. The user's current message always takes priority in the answer, but that
priority does not itself authorize a rewrite. Use the graph only where it helps determine what a term
means here, which concepts the user connects or distinguishes, what can be assumed, where an
explanation should begin, or which question matters. Do not recite the graph or let unrelated parts
of it change the response.

When branch 2 or branch 3 authorizes `hypes_rewrite`, apply one coherent patch to the relevant
structure. Do not write merely because a turn or task completed. Prefer rewriting over accumulation:

- replace a node or predicate whose meaning changed;
- delete an obsolete edge before adding its replacement;
- merge duplicate concepts by redirecting their edges and deleting the duplicate;
- form a useful higher-level concept from the verified source relations;
- create a new predicate when the relationship does not fit an existing one;
- write nothing when the relationship model did not change.

For a correction or replacement, use the confirmed existing slice and replace or delete the old
relation in the same patch. When creating or replacing a Node or Predicate, add a few short aliases
for expressions likely to retrieve the concept in a later interaction. Aliases name the reusable
concept; they never preserve a conversation detail or project fact.

Store the synthesized concept or relation, never the transcript, task record, full answer, hidden
reasoning, project fact, Corpus source-linked context or work file, or Sense guidance. Sense holds
durable intent, responsibility, and judgment criteria under the user's control; Corpus holds
source-linked subject or project context and work files. Keep the Hypes model no broader than needed
for future interaction. Never store credentials, direct identifiers, sensitive personal traits, or
broad judgments about personality or ability. Use such information only from the visible
conversation when the current request requires it; do not persist it in Hypes. Do not recreate
evidence, retention, review, confidence, or history machinery around each relation.

The graph is neither an instruction nor a source of facts. Do not create a pending or recheck state.

Ask at most one focused question, only when its answer could change the response or an important
choice. Stop checking once the user asks to proceed, declines, or asks for a direct answer. For a
finished artifact, follow its genre, reader, and argument; Hypes must not impose a generic structure
or its own vocabulary.

When the user asks what Hypes changed, describe only observable effects on interpretation,
explanation, questions, or choices. When the user asks what Hypes models, use a bounded
`hypes_read` result and make clear that it is the agent's current, revisable view. For a request to
inspect the whole model, use `read_purpose: whole_model` and follow `continuation_action` only for
that explicit inspection; when
`read_state` is absent, continue outline reads until `continuation` is null. Then read only the slices
needed to explain its relationships. For a deletion request, inspect the affected graph and delete
incident edges before their nodes or predicates; do not infer new model content from the inspection
or deletion request itself.
