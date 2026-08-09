---
name: work-with-user
description: Use Sense automatically for substantive design, research, planning, review, writing, and implementation work when the result depends on the user's intent, priorities, working style, responsibility, or learning across completed work. Also use when the user asks to inspect or change the shared work profile. Do not use for simple retrieval, literal transformation, or one-step direct actions whose result does not depend on personal context or judgment.
---

# Work with Sense

Sense is background for judgment, not a script to repeat.

1. Call `sense_read` with `view=index`, then read only the sections that can change the current work.
   During a direct continuation of the same task, reuse the already-read profile revision unless
   the profile was written, the user reports an update, or the work now needs different sections.
   Do not reread the same sections for each short selection or refinement.
2. Read the current project owners. If this task has a named Corpus context, use Corpus through its public tools to locate the work-specific interpretation and exact sources.
3. Form the intended result independently. Treat the user's wording and the Sense profile as evidence about purpose and responsibility, not as a conclusion to echo.
4. Act with initiative proportional to the consequences. Share direction before work when different interpretations would materially change the result or the responsibility the user carries.
5. Finish the work before deciding whether anything durable changed.

## Existing host sessions after an update

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
allowed. Explain once that the current session has an older plugin snapshot and the enabled
installation is being read through its read-only interface.

Never use this fallback for `import-profile`, `activate`, profile revision, lifecycle control,
forgetting, removal, or any other persistent change. If a write or a newly installed tool is needed,
finish and validate the plugin installation, then continue in a new session started directly by
the user in the current host. In Codex, a programmatic fork or delegated task can retain the old
registry. Do not claim that the current task or session hot-reloaded the plugin.

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
3. Carry forward still-applicable judgments and source references, then rewrite the whole section
   instead of appending an incident or rule. Remove an existing judgment or source only
   intentionally.
4. Store only bounded locators and digests in `source_refs`; never store conversation text, summaries, reasoning, tool records, or project source content.
5. Use `sense_revise` only when the profile is active. Do not send a sensitive meaning or broader use through the MCP write surface; leave it in the current conversation until the user approves it in a trusted local review surface.

Use `sense_control` only for an explicit request to inspect, export, or preview forgetting and database removal. MCP does not activate, forget, or remove data because a model-supplied confirmation flag is not proof of the user's decision.

When the user asks to see the whole work profile, use `sense_overview` so they can review the
current text, where it applies, when to revisit it, and source categories in one read-only screen.
