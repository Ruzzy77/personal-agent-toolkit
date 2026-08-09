---
name: update-sense
description: Build, validate, install, or update the Sense plugin and prepare continuity in a manually started new Codex task or Claude session with the new plugin snapshot. Use only when the user explicitly asks to maintain or update Sense, its provider packages, or its local installation. Do not use for ordinary profile reading or revision.
---

# Update Sense and continue

Treat an installed plugin update and a running host's available tools as separate states. Codex
tasks and Claude sessions do not hot-reload skills or MCP tools after installation.

1. Inspect the Sense owner repository and preserve unrelated or in-progress changes.
2. Build a clean candidate from the exact intended owner revision. Run the owner validation,
   generated-package validation, launcher smoke tests, and provider manifest checks before changing
   an installation.
3. Install one generated build identity for every provider in scope. After installation, verify the
   exact expected build version and enabled state from each provider's installation listing. Do not
   treat the current task's MCP surface as installation proof.
4. Cross the refresh boundary for the host that was updated:
   - In Codex, do not use `fork_thread` or a programmatically delegated task as proof of refresh;
     ask the user to start a new task directly from the Codex UI. Include the current `thread://`
     reference, owner commit, installed build version, completed checks, and unresolved request.
   - In Claude Code, require `claude plugin list --json` to show the expected enabled version, use
     `claude plugin details sense@MARKETPLACE` to check the skill and MCP inventory, then start a
     new Claude Code session. `claude mcp list` confirms installation connectivity but not that an
     already-running session reloaded.
   - In Claude Cowork, verify the plugin is enabled in the marketplace UI, then start a new local
     Cowork session. Do not claim that an existing session refreshed in place.
5. In the manually started task or session, require a real `sense_status` call and confirm that its
   `build_id` equals the installed build. Confirm that the expected Sense MCP tools and skills are
   exposed before continuing work. The old task or session must not declare success on behalf of
   the new one.

The old task or session may use the bounded read-only fallback in `work-with-user` when its host has
local shell access; it must not perform profile writes or claim that the new plugin surface was
loaded. Installation-list output proves disk state, not a running host registry.

Keep tool names and schemas stable unless the requested change requires a deliberate compatibility
break. A new build ID proves package identity; it does not prove that an already-running task loaded
the new skill or MCP snapshot.
