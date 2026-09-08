---
name: use-sense
description: Use Sense before substantive writing, planning, analysis, design, implementation, or other work when durable guidance or a section-linked Skill may shape the result, and when the user asks to review or revise Sense.
---

# Use Sense

Sense provides durable context for important choices and reusable work. The current request, facts, sources and independent reasoning determine the answer. Sense vocabulary remains internal to Sense.

## Access

For substantive work whose relevant guidance is not already available, use `sense_read` with `view="index"` to select relevant section IDs. Read their bodies with `view="sections"` and `section_ids` (an array of those IDs); omitting `view` returns only the index. A known relevant section can be opened directly; this is not a mandatory two-call sequence. Consider adjacent guidance when it materially affects the task rather than treating the first match as exclusive.

An ordinary index entry may include a user-approved Section Skill's name and description. When the exposed schema supports `include_skill`, use `false` when only the section's criteria are needed and `true` when its method is needed. Omission keeps the legacy complete-Skill response. Apply the selected method only to its matching activity and target; an optional procedure or output convention does not redefine the user's purpose or editing scope.

A direct continuation can open a known section immediately. Reuse already-read guidance of the same version within the task. Read again when the purpose changes, the user requests a reread, or an update or freshness check matters. Do not reread an already-injected common instruction body just to route back here. Trivial requests do not need a Sense lookup. The conversation and current sources support work during Sense unavailability. Explicit Sense requests include Sense diagnostics.

## Content

Sense sections contain user guidance that remains useful across contexts. A Section Skill contains a reusable working method connected to one section. Source material and locators belong with their sources. Conversation text belongs in conversation history. Project facts, operational states and QA records belong with their projects. Sense records the reusable intent in domain-neutral language.

## Revision

An explicit user request initiates a conversational Sense revision. Read every affected section and retain its current valid content. Present assistant-drafted or multi-section final wording in the conversation, then call `sense_revise` once with every section's `section_sha256` and complete replacement. Identical content produces a no-op. A section conflict preserves the existing content for reconciliation.

An explicit request can also replace one ordinary Section Skill. Read the linked section and its current Skill, present the complete final Skill wording, then call `sense_skill_revise` with the current Skill `version` and complete name, description and instructions. Use `expected_version="absent"` only when the section has no Skill. A conflict preserves the current Skill.

Sensitive section changes, sensitive Skill storage, Skill removal, and permanent deletion are outside the public remote MCP. A local command changes only the local development or migration store, so never report it as a change to the remote canonical profile. `sense_overview` presents the current ordinary guidance and its linked Skills.

The workbench can browse the same authorized canon and accept a user's direct save under the same version checks. It is optional for ordinary retrieval and conversational edits. A remote save does not update a host's base projection or prove application in the current model; an explicitly requested host connection or update uses `manage-environment`.
