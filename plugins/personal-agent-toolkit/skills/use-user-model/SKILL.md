---
name: use-user-model
description: "Use Hypes to apply and refine reusable relationships that shape interpretation, explanation, questions, and choices."
---

# Use the user relationship model

Hypes is the assistant's revisable model of reusable relationships. The current message has precedence. Hypes operates in the background and becomes visible through an explicit user request.

## Application

A stored relationship can shape interpretation, explanation, questions and choices. A current interaction can add, revise or weaken a relationship that will remain useful. Ordinary retrieval and literal transformation proceed from their direct inputs.

Treat the user's knowledge and understanding as revisable relationships, not fixed traits. Distinguish familiarity, comprehension, application, transfer and adoption only when the distinction changes the interaction; do not impose a fixed ladder or infer broad mastery from one narrow use.

Calibrate from the current interaction. Do not infer the user's knowledge from project files or collaborative outputs. Do not turn calibration into evidence bookkeeping or require source links. Revise a tentative relation promptly when the user reports a gap, corrects the interpretation or shows a different level.

When a relevant epistemic relation exists, use it to choose the explanation's starting point, terminology and step size. If the level is unknown, address the immediate conceptual dependency and ask a small confirmation only when the next choice depends on it.

## Read

Read a focused graph slice using short `focus` terms or known Node and Predicate refs. The relevant Edge supplies the relationship. The outline and `continuation` support a user-requested review of the full model.

The result's `version` covers the owner's whole graph, not only the returned slice. Keep it with the relationships used to prepare a change.

## Rewrite

Reusable relationships evolve from the current interaction through one atomic patch. Existing Nodes, Predicates and Edges receive replacement values. Obsolete concepts are deleted with their incident Edges. Aliases describe the reusable concept in concise language.

Pass the corresponding read's `version` as `expected_version`, including for a create-only patch. A confirmed rewrite returns the resulting version. On `graph_conflict`, reread the relevant relationships and rebuild the patch; do not retry the old patch by replacing only its version. An unknown write outcome also requires rereading before another patch.

Hypes stores nonsensitive relationships about user preferences, interpretations and recurring concepts. Operational records and project facts remain with their projects. Source content remains with its sources. Sense guidance and Corpus context remain in their respective systems.

Node names, labels and qualifiers support retrieval. The requested result receives its own language and structure. A user-requested model review presents the relevant relationships as the assistant's current revisable view.
