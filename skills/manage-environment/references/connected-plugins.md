# Connected plugins

Inspect the requested app or plugin through the current host's exposed management interface. Do not substitute ChatGPT app state for a Codex installation, or infer an unexposed tool from an old guide.

When available, `get_plugin_dependencies` accepts one exact plugin ID or `name@marketplace`. Its public-catalog result describes dependencies, not installation or authentication. A private marketplace returning `plugin_not_found` calls for that marketplace's manifest and a supported local installation query, not a reinstall.

Use a permissions query for the exact requested plugin. OAuth scope, execution approval, disabled state, and removal are different controls. Follow the current tool's target and authorization contract; a diagnostic failure is not permission to broaden access or remove a plugin. A request to exclude a Skill does not imply removing its app connection.

Inspect structured results as well as the outer tool status. After a requested change, distinguish stored configuration, the current task's exposed tools, and an actual authorized read. Use an appropriate read rather than sending, publishing or deleting content to test access. Never edit plugin caches or generated distribution copies to simulate an update.

Installation suggestions use the currently available installation tool under its own conditions. A missing tool, authentication failure and a request limit need different follow-up; do not label every failure as a need to reconnect.
