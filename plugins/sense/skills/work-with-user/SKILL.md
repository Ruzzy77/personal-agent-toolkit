---
name: work-with-user
description: Use Sense automatically for substantive design, research, planning, review, writing, and implementation work when the result depends on the user's intent, priorities, working style, responsibility, or lessons from completed work. Also use when the user asks to inspect or change the shared work profile. Do not use for simple retrieval, literal transformation, or one-step direct actions whose result does not depend on personal context or a meaningful choice.
---

# Work with Sense

Sense helps choose how to work with the user; it is not a script to repeat.

1. Call `sense_read` with `view=index`, then read only the sections that can change the current work.
   During a direct continuation of the same task, reuse the already-read profile revision unless
   the profile was written, the user reports an update, or the work now needs different sections.
   Do not reread the same sections for each short selection or refinement.
2. Read the current project files and results that establish the relevant facts. If this task has a
   named Corpus context, use Corpus through its public tools to find the work-specific interpretation
   and exact sources.
3. Decide what the result should be. Use the user's wording and the Sense profile to understand
   purpose and responsibility; do not repeat either one as the conclusion.
4. Take initiative in proportion to the consequences. State the direction first when different
   interpretations would materially change the result or the responsibility the user carries.
5. Finish the work before deciding whether anything durable changed.

## Running sessions after an update

An already-running Codex task or Claude session can keep the skill and MCP snapshot it received at
startup even after Sense is updated. If `sense_read` is not callable after normal tool discovery,
do not report that Sense or its profile is unavailable.

Resolve one enabled installation only when the current host has local shell access:

- In Codex, run `codex plugin list --json` and select exactly one installed, enabled entry whose
  `name` is `sense`. Use only its absolute `source.path`.
- In Claude Code, run `claude plugin list --json` and select exactly one enabled entry whose `id`
  starts with `sense@`. Use only its absolute `installPath`, and require the version in
  `.claude-plugin/plugin.json` to equal the listed version.
- In Claude Cowork or another host without local shell access, do not use a launcher fallback.
  Continue only after the user starts a new local session that exposes the Sense MCP tools.

If there is no unique match, stop instead of guessing a cache directory or version. At the exact
resolved path, require an executable `launchers/sense-readonly`; do not invoke the general lifecycle
launcher as a fallback. Use `launchers/sense-readonly` only for its `read` and `status` commands.
`read --view index`, `read --view sections --section-id SECTION_ID`, and `read --view full` are
allowed. Explain once, in plain language, that this session loaded an older Sense version and is
reading the installed profile through its read-only command.

Never use this fallback for `import-profile`, `activate`, profile revision, lifecycle control,
forgetting, removal, or any other persistent change. If a write or a newly installed tool is needed,
finish and validate the plugin installation, then continue in a new session started directly by
the user in the current host. In Codex, a programmatic fork or delegated task can retain the old
registry. Do not claim that the current task or session hot-reloaded the plugin.

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
