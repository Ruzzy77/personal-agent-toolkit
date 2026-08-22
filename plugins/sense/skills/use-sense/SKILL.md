---
name: use-sense
description: Use Sense to bring durable intent, responsibility, and cross-context lessons into important choices, and to review or revise retained guidance at the user's request.
---

# Use Sense

Sense provides durable context for important choices. The current request, facts, sources and independent reasoning determine the answer. Sense vocabulary remains internal to Sense.

## Access

Begin with `sense_read` using `view=index`, then read the relevant sections. A direct continuation can open a known section immediately. The conversation and current sources support work during Sense unavailability. Explicit Sense requests include Sense diagnostics.

## Content

Sense contains user guidance that remains useful across contexts. Source material and locators belong with their sources. Conversation text belongs in conversation history. Project facts, operational states and QA records belong with their projects. Sense records the reusable intent in domain-neutral language.

## Revision

An explicit user request initiates Sense revision. Read every affected section and retain its current valid content. Present assistant-drafted or multi-section final wording in the conversation, then call `sense_revise` once with every section's `section_sha256` and complete replacement. Identical content produces a no-op. A section conflict returns the revision to the user.

Sensitive changes and permanent deletion use a trusted local command. `sense_overview` presents the current ordinary guidance.
