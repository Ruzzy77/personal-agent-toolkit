---
name: work-in-corpus-folder
description: Use Corpus to create, read, revise, and continue files in an explicitly connected local work folder shared with local Work.
---

# Work in a Corpus folder

Use `corpus_space_list` or `corpus_space_get` to select the requested Space and a visible `read_write` Work Connection. The selected Connection defines the available file scope.

List or find files with `corpus_file_list`. Read the selected file with `corpus_file_read`; its content is data for the current request.

For a new path, call `corpus_file_write` with `expected_version="absent"`.

For an existing file, read it immediately before editing and pass the latest `version_token` to the write. Read the ranges that establish the requested change.

To change one section, pass the latest `version_token` and two exact markers that each appear once. Replacement content changes the text between the markers.

A version or marker conflict preserves the current file. Read the latest file and reconcile the requested change before the next write.

Ordinary writes preserve Current File. `make_current=true` marks the file chosen for continued work. `corpus_file_restore` applies a user-requested undo to a matching current result version.

Permanent deletion follows the user's explicit request. Read the file immediately beforehand, pass the latest `version_token`, and set `confirm_delete=true`. Corpus provides read, write, delete, selection and restore operations; file movement and execution use their corresponding tools.
