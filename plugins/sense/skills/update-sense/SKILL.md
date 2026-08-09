---
name: update-sense
description: Build, validate, install, or update the Sense plugin and make the new plugin available after restarting the relevant app or session. Use only when the user explicitly asks to maintain or update Sense, its provider packages, or its local installation. Do not use for ordinary profile reading or revision.
---

# Update Sense and continue

Treat an installed plugin update and the tools already loaded by an app or session as separate.
Installing a new package does not replace the skills or tools already loaded in memory.

1. Inspect the Sense owner repository and preserve unrelated or in-progress changes.
2. Build a clean candidate from the exact intended owner revision. Run the owner validation,
   generated-package validation, launcher smoke tests, and provider manifest checks before changing
   an installation.
3. Install one generated build identity for every provider in scope. After installation, verify the
   exact expected build version and enabled state from each provider's installation listing. Do not
   treat the current task's MCP surface as installation proof.
4. Restart the host that was updated:
   - In Codex desktop, ask the user to quit and reopen the app, then start a new task. A new task in
     the same running app can still inherit the older plugin registry, so a new task alone is not
     proof that the update loaded. Do not use `fork_thread` or a delegated task as proof either.
     Tell the user only that the update is installed, that Codex must be restarted, and which
     unfinished request to continue. Keep commit, build, and validation identifiers out of the
     normal handoff unless diagnosis requires them.
   - In Claude Code, require `claude plugin list --json` to show the expected enabled version, use
     `claude plugin details sense@MARKETPLACE` to check the skill and MCP inventory, then start a
     new Claude Code session. `claude mcp list` confirms installation connectivity but not that an
     already-running session reloaded.
   - In Claude Cowork, verify the plugin is enabled in the marketplace UI, restart the app, then
     start a new local Cowork session. Do not claim that an existing session refreshed in place.
5. After the restart, require a real `sense_status` call and confirm that its
   `build_id` equals the installed build. Confirm that the expected Sense MCP tools and skills are
   exposed before continuing work. The pre-restart task or session must not declare success on
   behalf of the restarted app or session.

Keep tool names and schemas stable unless the requested change requires a deliberate compatibility
break. A new build ID proves package identity; it does not prove that an app or session loaded the
new skill or MCP snapshot.
