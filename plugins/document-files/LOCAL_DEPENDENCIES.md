# Document Files: what keeps it local

Every other toolkit product is reached through the one remote connection. This
one is still a separate local server on each machine, so the single-connection
goal is not finished. This note records only what the eleven tools take, what
they return, and what they need from the machine they run on. It proposes no
design.

## The eleven tools

`path`, `input_path` and `output_path` are filesystem paths on the machine that
runs the server. Nothing is uploaded or returned as bytes except the text and
structure listed below.

| tool | reads | writes | returns |
|---|---|---|---|
| `document_capabilities` | nothing | nothing | supported formats, coverage behaviour, backend versions, whether headless rendering is available |
| `document_inspect_file` | `path` | nothing | bounded text, structure counts, coverage, issues, format metadata |
| `document_extract_file` | `path` | nothing | bounded plain text or Markdown, coverage, issues |
| `document_extract_structure` | `path` | nothing | paged units with source locators, typed spreadsheet values, table-cell coordinates, field metadata |
| `document_extract_schema` | `path` | retains the result privately for later reads | schema, semantics and values from its own AI call |
| `document_get_extraction` | a retained extraction by `job_id` | nothing | nodes or evidence, paged |
| `document_convert_file` | `input_path` | `output_path` | the written path and conversion report |
| `document_create_hwpx` | nothing | `output_path` | package, document and reopen check results |
| `document_edit_hwpx` | `input_path` | `output_path` (or a dry run) | preflight and reopen verification |
| `document_verify_hwpx` | `path`, optional `reference_path` | nothing | package integrity, reopen, required/forbidden text, table geometry |
| `document_render_file` | `path` | `output_path` | SVG pages, PDF or an HTML preview |

Six of the eleven only read. Five write a file the caller names.

## What ties it to the machine

- **Filesystem paths in and out.** Ten tools take a path; five write one. A
  remote server would need the bytes moved both ways and a place to put the
  result, which is a different contract from the current one.
- **The pinned `rhwp` backend.** HWP and HWPX work resolves a native binary
  through `DOCUMENT_FILES_RHWP` or the provisioned cache
  (`~/.cache/document-files/rhwp/<version>/<platform>/bin/rhwp`). It is a
  platform build: the Mac and Spark copies are different binaries.
- **A provisioned Python environment.** The launcher uses
  `<plugin>/.venv/bin/python` when present, otherwise `uv run` against
  `DOCUMENT_FILES_PYTHON_ENV` (default `~/Library/Caches/Document Files/python-env`).
  It carries native extensions such as lxml and cryptography, so the
  environment belongs to one machine and one architecture.
- **Subprocess work.** Rendering and HWP adapters shell out; PDF and SVG output
  is staged in a temporary directory before being moved to `output_path`.
- **Its own AI call.** `document_extract_schema` uses
  `DOCUMENT_FILES_AI_ENDPOINT`, `DOCUMENT_FILES_AI_MODEL` and
  `DOCUMENT_FILES_AI_API_KEY`, and retains the result for
  `document_get_extraction`. That retention lives with the server process.
- **A pinned release.** The package is version-locked and checked by
  `scripts/document_files_release.py`; its Skill ships inside the package rather
  than from the shared `skills/` source.

## Current placement

| machine | how it runs | used by |
|---|---|---|
| Mac | plugin launcher, provisions its own env | Claude Code, Aside |
| Spark | inside the Host runtime (`personal-agent-sync` installs it) | Corpus Source extraction |
| ChatGPT | uploaded personal Skill with its own runtime | ChatGPT |

So the same engine already runs in three places with three provisioning paths.
