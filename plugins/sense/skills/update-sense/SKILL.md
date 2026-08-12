---
name: update-sense
description: Validate, install, or update Sense and make the new version available after the relevant app restarts. Use only when the user explicitly asks to maintain Sense or its installation.
---

# Update Sense

An installed package and the copy already loaded by an open app can differ.

1. Preserve unrelated changes in the Sense owner repository.
2. Validate the intended owner files, build the package, and validate the generated package before
   replacing an installation.
3. Install the same generated version for every requested provider and verify the installed version.
4. Restart the affected app. Codex desktop must be quit and reopened; opening another conversation
   in the same app is not enough. Claude Code and Claude Cowork also need a new session after their
   installation is verified.
5. After restart, call `sense_status` and compare its `build_id` with the installed version.

Keep tool names and data shapes compatible unless the requested change requires otherwise. Tell the
user only what they need to do next; include diagnostic identifiers only when diagnosis needs them.
