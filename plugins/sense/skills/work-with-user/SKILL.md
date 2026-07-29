---
name: work-with-user
description: Use Sense automatically for substantive design, research, planning, review, writing, and implementation work when the result depends on the user's intent, priorities, working style, responsibility, or learning across completed work. Also use when the user asks to inspect or change the shared work profile. Do not use for simple retrieval, literal transformation, or one-step direct actions whose result does not depend on personal context or judgment.
---

# Work with Sense

Sense is background for judgment, not a script to repeat.

1. Call `sense_read` with `view=index`, then read only the sections that can change the current work.
2. Read the current project owners. If this task has a named Corpus context, use Corpus through its public tools to locate the work-specific interpretation and exact sources.
3. Form the intended result independently. Treat the user's wording and the Sense profile as evidence about purpose and responsibility, not as a conclusion to echo.
4. Act with initiative proportional to the consequences. Share direction before work when different interpretations would materially change the result or the responsibility the user carries.
5. Finish the work before deciding whether anything durable changed.

## Task-local plugin snapshots

A Codex task can keep the skill and MCP snapshot it received when the task started even after Sense
is updated. If `sense_read` is not callable after normal tool discovery in a local Codex task, do
not report that Sense or its profile is unavailable:

1. Run `codex plugin list --json` and select exactly one installed, enabled entry whose `name` is
   `sense`. If there is no unique match, stop instead of guessing a cache directory or version.
2. Use the selected entry's `source.path` only when it contains an executable
   `bin/sense-readonly`; do not invoke the general lifecycle launcher as a fallback.
3. Use `bin/sense-readonly` only for its `read` and `status` commands. `read --view index`,
   `read --view sections --section-id SECTION_ID`, and `read --view full` are allowed.
4. Explain once that the current task has an older plugin snapshot and that the enabled installation
   is being read through its read-only interface.

Never use this fallback for `import-profile`, `activate`, profile revision, lifecycle control,
forgetting, removal, or any other persistent change. If a write or a newly installed tool is needed,
finish and validate the plugin installation, then continue in a new task started directly by the
user from the Codex UI. A programmatic fork or delegated task can retain the old registry. Do not
claim that the current task hot-reloaded the plugin.

For visual artifacts, keep the current project's brand, template, and content owners authoritative.
When they leave visual direction open and a user-owned design library exists, read its current
manifest and owner materials and treat only a task- and medium-compatible design as a candidate.
For a cross-medium request, use a non-native design only as a reference and follow the native
artifact path without claiming template compatibility. Do not copy library assets or registration
metadata into Sense or Corpus; the applicable artifact skill owns production, rendering,
accessibility, and format validation.

After a completed result or correction, place the change with its actual owner:

- Project fact, code, result, or execution method: the project already owns it.
- A question, relationship, or direction useful within one body of work: revise the selected Corpus context if persistent context writes are authorized.
- A judgment that changes future choices across different work: revise the nearest Sense section.
- No future judgment changes: persist nothing.

For a Sense revision:

1. Read the exact section and keep its revision and section digest.
2. State the prior understanding and the future judgment that will now differ.
3. Rewrite the whole section instead of appending an incident or rule.
4. Store only bounded locators and digests in `source_refs`; never store conversation text, summaries, reasoning, tool records, or project source content.
5. Use `sense_revise` only when the profile is active. Do not send a sensitive meaning or broader use through the MCP write surface; leave it in the current conversation until the user approves it in a trusted local review surface.

Use `sense_control` only for an explicit request to inspect, export, or preview forgetting and database removal. MCP does not activate, forget, or remove data because a model-supplied confirmation flag is not proof of the user's decision.

When the user asks to see the whole work profile, use `sense_overview` so they can review the
current text, where it applies, when to revisit it, and source categories in one read-only screen.
