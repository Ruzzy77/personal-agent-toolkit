---
name: use-flow
description: Read and update Flow work, HTML artifacts and reusable material through the owner's existing Toolkit connection. Use for work in Flow, not to start a new agent runtime.
---

# Flow

Flow has one primary work surface. The agent composes the requested artifact; the user does not assemble a page from a tool catalog. Continue using the current platform's agent connection.

## Select the work

Use `flow_workspace_list` for the registered workspace and permission, then `flow_work_list` and `flow_work_read` for the work ID, current artifact revisions and references. Conversation IDs are not Flow work IDs. Never invent a service URL or infer registration from a folder name.

For a new work, `flow_work_create` accepts an optional complete artifact. Omitting it creates a blank surface. `linked_resources` connects verified original references without putting them in the body. Use `source_id` only for an explicitly requested copy of an existing saved artifact; do not combine it with `artifact`.

## Compose and revise

Use HTML for new agent-authored work. Apply the installed UIKit and the project design guidance; inline its CSS and scripts rather than loading a CDN. Preserve existing legacy artifacts when no conversion was requested. The exact HTML contract is in `apps/flow/src/work-surface/README.md`.

- `flow_change_submit` with `mode: "replace"` applies a requested revision immediately to the same artifact ID. Include `artifact_id`, current `base_revision`, the complete artifact and an idempotency key. Read and reconcile a conflict before resubmitting; never merely advance the expected version.
- `initialize` fills an existing blank artifact under the same ID.
- `proposal` leaves an optional or exploratory alternative separate until the user chooses to apply it. A requested edit does not need another approval solely because it changes layout.
- `selection` changes only the explicitly selected document text, image region or diagram object. It does not apply to HTML.
- `add` creates a separate result only when the task needs one. Do not accumulate blank artifacts or repeat the work name above the artifact's own heading.

For assets, read the file version with `host_files` and call `flow_asset_import` with the registered Flow root and exact `expected_version`. Bind its immutable `src` to an asset name in the HTML manifest. Original files remain unchanged. The sandbox allows local interaction but not account access, filesystem access or network communication. Bundle or omit unsupported external dependencies instead of granting extra permissions.

`flow_work_update` changes specified work metadata or the primary artifact with a work revision guard. `flow_change_action` supports applying a selected proposal or undoing an identified change; follow the user's request and recheck its revision.

## Curate and reuse

Search `flow_library_list` before adding material; read an exact entry with `flow_library_read`. `work_id` narrows search to that work's scoped material plus shared entries. Omit it only when looking across work contexts.

`flow_library_upsert` creates or revises an entry with an explicit scope, optional original reference and useful body. Use `expected_revision: 0` for a new entry and the current revision for an edit. Keep task-specific scope unless broader reuse is established. Reuse an existing reference instead of duplicating it. Do not store execution logs, transcripts or internal review records as material.

Sense criteria and Hypes relationships remain distinct original references; curation does not adopt a criterion. Read and change canonical content through its existing service and authorization procedure. Corpus originals retain their access policies. Journal summaries and publication drafts are ordinary work; publication remains a separate authorized action.

`flow_snapshot_list`, `flow_snapshot_read` and `flow_snapshot_create` retain exact artifact versions. A snapshot is reusable material, not a published issue. File export or movement is never an automatic next stage.

Reuse the same idempotency key only for the same logical retry. Return the tool's Flow link after verifying the saved result. Do not put credentials, request summaries, execution records or internal review notes in the artifact.
