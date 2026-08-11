---
name: work-with-user
description: Use Sense automatically when substantive work requires a consequential choice that depends on the user's durable intent, priorities, responsibility, or lessons across completed work. Also use when the user asks to inspect or change the shared work profile, or asks which guidance currently affects the task. Do not use for simple retrieval, literal transformation, one-step actions, or merely to choose the voice or structure of a paper, report, essay, or other finished artifact; finished artifacts follow their current project and genre guidance.
---

# Work with Sense

Use Sense to make better choices with the user. Do not use the profile as a prose template.

1. When `sense_read` is available, call it with `view=index`, then read only the sections that can
   materially change the current work. During a direct continuation, reuse the already-read profile
   revision. Do not reread the same sections for each short selection or refinement.
2. Read the current project files and results that establish the facts. Use a named Corpus context
   only when the task needs its sources or work-specific interpretation.
3. Reach an independent conclusion. Use the profile only where it changes a choice, responsibility,
   or way of working. Do not repeat its wording as the answer or impose it on a finished artifact's
   argument, voice, or structure.
4. Complete the requested work before deciding whether anything durable changed.

If the Sense tools are unavailable, continue from the visible conversation and current project
sources. Do not inspect installations, search plugin paths, run a launcher, or interrupt the user's
task to explain that Sense is unavailable unless the user asked about it.

Treat drafts and artifacts as agent-authored unless the user supplied the exact wording or explicitly
adopted it. Direction, selection, editing, or permission to proceed does not establish authorship or
line-by-line approval.

When the user asks what currently affects the task, give one short view that separates:

- instructions that apply across tasks,
- the relevant parts of Sense,
- instructions from the current project,
- local memory entries actually used for this task.

Read only the instructions that Codex or Claude makes available for the current task. Do not search
unrelated projects, dump memory contents, or copy these sources into Sense. Mention a conflict or
outdated instruction only when it could change the work.

After completed work or an explicit correction, keep any lasting change with its nearest owner:

- Leave project facts, code, results, and execution methods with the project.
- Put unresolved questions, missing evidence, and source-linked relationships about one body of work
  in its selected Corpus context only when persistent context writes are authorized.
- Put the user's topic-, task-, or responsibility-specific concept relationships, unclear points, and
  helpful explanation patterns in Hypes only when Hypes is available and its own observation,
  confirmation, and persistence contract permits the write. A completed task or Sense revision is
  not such authorization. If Hypes is unavailable, keep this understanding only in the current
  conversation. Do not copy concept mastery or explanation effects into Sense or Corpus.
- Revise Sense only for guidance that should change choices in different kinds of work.
- Store nothing when future choices should not change.

For a Sense revision, read the exact section and retain its revision and digest. State the previous
understanding and what should differ next time. Carry forward still-applicable guidance and source references,
rewrite the whole section, and remove material only intentionally. Keep `source_refs`
to bounded locators and digests; never store conversation text, summaries, reasoning, or project
content. Use `sense_revise` only for an active profile.

Use `sense_control` only when the user explicitly asks to inspect, export, forget, or remove profile
data. Use `sense_overview` for an ordinary read-only review of the work profile.
