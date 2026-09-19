# Host connection and staged migration

## Canonical base document

The private base document belongs to Corpus; the plugin contains no personal base text. Use the exposed `corpus_document_list` and `corpus_document_read` schemas to locate and read the explicitly selected current document. Confirm its Space, document ID, `kind=guidance`, `provenance=user_approved_guidance`, approval and current version. The native read nests identity and `body_markdown` under `document`, while `start_char`, `has_more` and pagination fields are outside it. Read all pages with the same `expected_version` and `snapshot=current`; verify the assembled code-point count against `document.body_chars`. A captured Source excerpt, previous snapshot, summary or local development store is not a substitute for the current complete native document.

Only connect or update the host when the user has requested that operation. Ordinary runs reuse the last verified local projection: no startup hook, full-body tool injection or network request on every run. If the current API does not expose native documents, keep the last good projection and report the missing capability instead of importing or rewriting the private store.

## Codex projection helper

Run `scripts/project_base_instructions.py` from this Skill with Python 3.11 or newer. It has no network access code. The caller reads the authorized canonical document and supplies the verified complete payload on standard input; do not put the document body into shell arguments or logs.

The input is a JSON object:

```json
{
  "space_id": "the selected Space ID",
  "document_id": "the selected document ID",
  "kind": "guidance",
  "version": "the current canonical version",
  "body_markdown": "the exact complete canonical body",
  "body_sha256": "SHA-256 of that exact body encoded as UTF-8",
  "start_char": 0,
  "has_more": false,
  "complete": true
}
```

`version` may also be an integer returned by the service. Preserve its exact value. If the read response is paged or nested, assemble this object deliberately from one verified current version. Set `complete=true` only after the entire body has been obtained. Compute `body_sha256` from those exact bytes if the service does not provide it; a document version is not a body hash. The helper verifies consistency, not remote authentication or the user's authority.

Supply `--expected-space-id`, `--expected-document-id`, `--expected-version`, and `--expected-body-sha256` from that verification. For `--mode apply`, also supply the freshly read `--expected-old-sha256` of the managed projection and `--expected-config-sha256` of `config.toml`; use `absent` only when that file is absent. `--mode dry-run` computes changes without creating files. The default `--mode check` reports whether the supplied current document is already connected and exits with status 1 if it is not.

The only output targets are:

- `${CODEX_HOME:-~/.codex}/managed/personal-agent-toolkit/base-instructions.md`
- the top-level `model_instructions_file` setting in that Codex home's `config.toml`

The helper validates and stages both files under an exclusive temporary lock, checks the old hashes again, and atomically replaces each file. A normal commit failure restores the previous projection when it is still the helper's own write; a concurrent external edit is not overwritten during recovery. If recovery itself fails, it reports the retained recovery path rather than deleting the previous copy. Two files are not a filesystem-wide transaction, so after an interrupted process use `check` against the current canonical document before retrying. If the interruption left `.projection.lock`, confirm that no update is running before removing that empty lock directory. Existing model, effort, profiles and other settings are preserved. Source files, caches and private runtime stores are not edited.

## Two-phase transition

First install or update the Toolkit distribution through the host's normal mechanism, project the verified base when requested, and inspect the resulting configuration. In a new task, verify that the current Toolkit Skills and required tools are exposed and that the configured projection is supplied. An existing task's inherited instructions cannot prove that transition.

Only after validation, and under explicit cleanup authorization, remove the superseded local `manage-connected-plugins`, `openai-guidance` or `exa-search` Skills that this installation actually replaces. Inspect their current contents before cleanup so unique user changes are not lost. Do not delete them as part of projection, silently disable unrelated Skills, or erase the original base file.

## Resolve a registered local Workspace

When the task location must be matched to a host registration, run the installed `personal-agent-sync workspace-resolve --path "$PWD"` from the actual task directory. Add `--config "$SYNC_CONFIG"` before `workspace-resolve` only for an explicitly selected configuration. This read-only command matches registered local roots and returns path-free binding IDs; it does not register or publish anything. Pass the returned `host_id` and `workspace_id` to `corpus_workspace_resolve` and confirm the same `space_id`. Missing or ambiguous registration is not permission to infer a binding. If the installed Sync version lacks this command, report that limitation rather than changing configuration during retrieval.

Workspace bindings come from `corpus_workspace_resolve` using the verified `host_id` and `workspace_id` matching the task's current local registration. Register or change bindings only through `corpus_workspace_bind` when authorized. The remote binding associates a Workspace with Context and explicitly has no filesystem authority. Local host permissions and Sync enforce their own file-access boundaries; this is not a native OS sandbox. Internal task-owned work can be created or revised under the delegated task. Business originals remain `read_only`, and approved exports through a `create_only` Connection may create new files without replacing existing ones. Do not duplicate absolute paths into shared guidance, infer a connection from a similarly named folder, or bind another machine's registration.
