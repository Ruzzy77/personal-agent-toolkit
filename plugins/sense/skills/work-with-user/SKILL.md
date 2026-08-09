---
name: work-with-user
description: Use Sense automatically for substantive design, research, planning, review, writing, and implementation work when the result depends on the user's intent, priorities, working style, responsibility, or lessons from completed work. Also use when the user asks to inspect or change the shared work profile. Do not use for simple retrieval, literal transformation, or one-step direct actions whose result does not depend on personal context or a meaningful choice.
---

# Work with Sense

Sense helps choose how to work with the user; it is not a script to repeat.

1. When `sense_read` is available in the current task, call it with `view=index`, then read only the
   sections that can change the current work. During a direct continuation of the same task, reuse
   the already-read profile revision unless the profile was written, the user reports an update, or
   the work now needs different sections. Do not reread the same sections for each short selection
   or refinement. If the Sense tools are unavailable, continue from the visible conversation and
   current project sources. Do not inspect installations, search plugin paths, run a launcher, or
   interrupt the user's task to explain that Sense is unavailable unless the user asked about it.
2. Read the current project files and results that establish the relevant facts. If this task has a
   named Corpus context, use Corpus through its public tools to find the work-specific interpretation
   and exact sources.
3. Decide what the result should be. Use the user's wording and the Sense profile to understand
   purpose and responsibility; do not repeat either one as the conclusion.
   Treat project drafts and artifacts as agent-authored unless the user supplied the exact wording
   or explicitly adopted it. A request, direction, selection, edit, or permission to proceed does
   not make every generated sentence the user's wording, belief, or line-by-line approval.
4. Take initiative in proportion to the consequences. State the direction first when different
   interpretations would materially change the result or the responsibility the user carries.
5. Finish the work before deciding whether anything durable changed.

For visual artifacts, follow the current project's brand, template, and content sources. When they
leave visual direction open and a user-owned design library exists, read its current manifest and
source materials and treat only a task- and medium-compatible design as a candidate.
For a cross-medium request, use a non-native design only as a reference and follow the native
artifact path without claiming template compatibility. Do not copy library assets or registration
metadata into Sense or Corpus; the applicable artifact skill owns production, rendering,
accessibility, and format validation.

After finishing the work or receiving a correction, decide where any lasting change belongs:

- Project fact, code, result, or execution method: leave it with the project.
- A question, relationship, or direction useful within one body of work: revise the selected Corpus
  context if persistent context writes are authorized.
- Guidance that should affect choices in different kinds of work: revise the nearest Sense section.
- If it will not change future choices, store nothing.

For a Sense revision:

1. Read the exact section and keep its revision and section digest.
2. State what the section previously said and how future choices should now differ.
3. Carry forward still-applicable guidance and source references, then rewrite the whole section
   instead of appending an incident or rule. Remove existing guidance or a source only
   intentionally.
4. Store only bounded locators and digests in `source_refs`; never store conversation text,
   summaries, reasoning, tool records, or project source content.
5. Use `sense_revise` only when the profile is active. Do not send sensitive meaning or broader use
   through the MCP write surface. Leave it in the current conversation until the user approves it in
   a trusted local review surface.

Use `sense_control` only when the user explicitly asks to inspect or export the profile, or to preview
what forgetting a section or removing the database would do. MCP does not activate, forget, or remove
data because a confirmation flag supplied by a model does not prove the user's decision.

When the user asks to review the work profile, use `sense_overview` for one read-only screen of all
non-sensitive sections. If the user explicitly asks for a complete structured inspection that also
includes sensitive sections, use `sense_control(action="inspect")` instead.
