---
name: use-flow
description: Read and update Flow work, HTML artifacts and reusable material through the owner's existing Toolkit connection. Use for work in Flow, not to start a new agent runtime.
---

# Flow

Flow has one primary work surface. The agent composes the requested artifact; the user does not assemble a page from a tool catalog. Continue using the current platform's agent connection. Toolkit web is the primary screen; the local address preserves existing browser drafts and provides a continuation link, not another production workspace.

## Select and read work

Use `flow_workspace_list` for the registered workspace, permission, API version and capabilities. Require API version 5 and the relevant capability before writing. Do not send a whole-workspace state to an older endpoint. Conversation IDs are not Flow work IDs. Never invent a service URL or infer registration from a folder name.

`flow_work_list` is paged (default 50, maximum 100). `flow_work_read` returns work metadata, artifact summaries, linked-material summaries and undo availability, not historical bodies. Follow the returned page offsets only when further results are needed.

Read one artifact with `flow_artifact_read`: select exactly one of an artifact ID, a saved source ID or a change ID. Pin the artifact revision; for a change choose `part: "before"` or `"proposal"`. Concatenate `content` using `nextOffset`, retaining the first response's `version` for every following page, then parse the complete JSON. Offsets and limits are UTF-8 bytes; default 64 KiB, maximum 1 MiB. Do not read every old version to open a work. `flow_change_list` and `flow_change_read` return headers; read a chosen comparison body separately.

For a new work, `flow_work_create` accepts an optional complete artifact. Omitting it creates a blank surface. `linked_resources` connects verified original references without putting them in the body. Use `source_id` only for an explicitly requested copy of an existing saved artifact; do not combine it with `artifact`.

## Compose and revise

Use HTML for new agent-authored work. Apply the installed UIKit and the project design guidance. For interactive React artifacts, use the UIKit screen command in the shared Spark authoring environment: `ui_kit.py screen App.jsx output.html --title "Title"`. It owns CSS ordering and the offline bundle; do not create a package project or ad hoc bundler for each artifact. Use `UIKitRoot colorScheme="inherit"` so Flow controls brightness without remounting the artifact. Use official FieldSelect for styled selection, with visibleLabel and description where needed; native HTML select is a different implementation. Keep the existing document authoring tool for editable reports. Preserve existing legacy artifacts when no conversion was requested. The exact HTML contract is in `apps/flow/src/work-surface/README.md`.

- `flow_change_submit` with `mode: "replace"` applies a requested revision immediately to the same artifact ID. Include `artifact_id`, current `base_revision`, the complete artifact and an idempotency key. Read and reconcile a conflict before resubmitting; never merely advance the expected version.
- `initialize` fills an existing blank artifact under the same ID.
- `proposal` leaves an optional or exploratory alternative separate until the user chooses to apply it. A requested edit does not need another approval solely because it changes layout.
- `selection` changes only the explicitly selected document text, image region or diagram object. It does not apply to HTML.
- `add` creates a separate result only when the task needs one. Do not accumulate blank artifacts or repeat the work name above the artifact's own heading.

For assets, read the file version with `host_files` and call `flow_asset_import` with the registered Flow root and exact `expected_version`. Bind its immutable `src` to an asset name in the HTML manifest. Original files remain unchanged. HTML and its manifest belong to the same version; retain assets needed by earlier versions. The sandbox allows local interaction but not account access, filesystem access or network communication. Bundle or omit unsupported external dependencies instead of granting extra permissions.

`flow_work_update` changes specified work metadata or the primary artifact with a work revision guard. `flow_change_action` applies a selected proposal or undoes an identified change. Undo restores the previous body as a new revision; it cannot overwrite a later edit. On an uncertain response, retry the same change ID and action rather than creating another change.

Keep the same idempotency key for a logical mutation after a transport failure. Use a new key only for a new intended mutation. Return the tool's Flow link after verifying the saved result.

## Find and reuse material

`flow_library_list` pages curated material; `work_id` includes that work's scoped material and shared entries. Read an exact entry with `flow_library_read`, which loads only that selected body. A saved artifact is returned as `artifactRef`; read it with `flow_artifact_read`.

`flow_resource_search` also finds accessible original Sense criteria, Corpus project material, Journal records and Library publications through the existing owner connection. Follow `nextCursor` even when a page has no matching items. A partial failure does not invalidate results from other services; retry the query for the failed portion. Sensitive user context is not broadly indexed. An explicit reference still uses its original access policy.

`flow_resource_read` reads an exact original reference. For a paged body, retain `expected_version` and follow `nextStartChar`; these offsets count Unicode characters, unlike artifact byte offsets. If an original service cannot provide the requested historical version, report that mismatch rather than presenting the latest body as the old version. Do not replicate service credentials on Spark.

Opening a search result neither registers it in the library nor inserts it into the artifact. Keep reading, linking to the current work and curating reusable content distinct. Preserve the original reference and source version. Search for an existing entry before creating one; do not merge separately curated content merely because it has the same original.

`flow_library_upsert` creates or revises an entry with an explicit scope, optional original reference and useful body. Use `expected_revision: 0` for a new entry and the current revision for an edit. Keep task-specific scope unless broader reuse is established. Do not store execution logs, transcripts or internal review records as material.

Sense criteria and Hypes relationships remain distinct original references; curation does not adopt a criterion. Read and change canonical content through its existing service and authorization procedure. Corpus originals retain their access policies. Journal summaries and publication drafts are ordinary work; publication remains a separate authorized action.

`flow_snapshot_list`, `flow_snapshot_read` and `flow_snapshot_create` retain exact artifact versions. Snapshot reads return a source header with `artifactRef`, not an unbounded body. A snapshot is reusable material, not a published issue. File export or movement is never an automatic next stage.

Do not clear old browser storage to force the web transition. Recover confirmed local changes against their original base version. Do not put credentials, request summaries, execution records or internal review notes in the artifact.
