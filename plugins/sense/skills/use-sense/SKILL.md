---
name: use-sense
description: Use Sense when durable guidance or a user-approved Section Skill may shape an important choice or reusable task, and when the user asks to review or revise Sense.
---

# Use Sense

Sense provides durable context for important choices and reusable work. The current request, facts, sources and independent reasoning determine the answer. Sense vocabulary remains internal to Sense.

## Access

For an important choice or a substantive task that may match a retained workflow, begin with `sense_read` using `view=index`. An ordinary index entry may include the name and description of a user-approved Section Skill. Read only the sections relevant to the current request. The section response includes the complete Skill instructions when one is attached; use them as the working method for that task.

A direct continuation can open a known section immediately. Trivial requests do not need a Sense lookup. The conversation and current sources support work during Sense unavailability. Explicit Sense requests include Sense diagnostics.

## Content

Sense sections contain user guidance that remains useful across contexts. A Section Skill contains a reusable working method connected to one section. Source material and locators belong with their sources. Conversation text belongs in conversation history. Project facts, operational states and QA records belong with their projects. Sense records the reusable intent in domain-neutral language.

## Revision

An explicit user request initiates Sense revision. Read every affected section and retain its current valid content. Present assistant-drafted or multi-section final wording in the conversation, then call `sense_revise` once with every section's `section_sha256` and complete replacement. Identical content produces a no-op. A section conflict returns the revision to the user.

Section Skill changes, sensitive changes and permanent deletion use a trusted local command. `sense_overview` presents the current ordinary guidance and its linked Skills.
