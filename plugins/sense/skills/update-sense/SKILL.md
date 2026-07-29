---
name: update-sense
description: Build, validate, install, or update the Sense plugin and prepare continuity in a manually started new Codex task with the new plugin snapshot. Use only when the user explicitly asks to maintain or update Sense, its provider packages, or its local installation. Do not use for ordinary profile reading or revision.
---

# Update Sense and continue

Treat an installed plugin update and a task's available tools as separate states. Codex tasks do not
hot-reload skills or MCP tools after installation.

1. Inspect the Sense owner repository and preserve unrelated or in-progress changes.
2. Build a clean candidate from the exact intended owner revision. Run the owner validation,
   generated-package validation, launcher smoke tests, and provider manifest checks before changing
   an installation.
3. Install one generated build identity for every provider in scope. After installation, verify the
   exact expected build version and enabled state from each provider's installation listing. Do not
   treat the current task's MCP surface as installation proof.
4. Do not use `fork_thread` or a programmatically delegated task as proof of refresh; those tasks can
   retain the previous plugin registry. Ask the user to start a new task directly from the Codex UI.
   Give them a concise handoff containing the current `thread://` reference, owner commit, installed
   build version, completed checks, and unresolved request.
5. In the manually started task, require a real `sense_status` call and confirm that its `build_id`
   equals the installed build. Confirm that the expected Sense MCP tools and skills are exposed
   before continuing work. The old task must not declare success on behalf of the new task.

The old task may use the bounded read-only fallback in `work-with-user`; it must not perform profile
writes or claim that the new plugin surface was loaded. Installation-list output proves disk state,
not a running task's registry.

Keep tool names and schemas stable unless the requested change requires a deliberate compatibility
break. A new build ID proves package identity; it does not prove that an already-running task loaded
the new skill or MCP snapshot.
