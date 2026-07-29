# Third-party dependencies

Sense & Corpus is distributed under Apache-2.0. It does not vendor the runtime dependencies below;
the launchers install them from Python package indexes according to the committed lockfiles. Each
dependency remains subject to its own license.

Direct runtime dependencies:

| Dependency | Used by | License |
| --- | --- | --- |
| `defusedxml` | Corpus | Python Software Foundation License |
| `mcp` | Sense, Corpus | MIT |
| `olefile` | Corpus | BSD |
| `openpyxl` | Corpus | MIT |
| `pypdf` | Corpus | BSD-3-Clause |
| `python-docx` | Corpus | MIT |
| `python-pptx` | Corpus | MIT |

Transitive dependencies in the current lockfiles use permissive licenses or MPL-2.0. The exact
resolved package names, versions, source hashes, and platform markers are recorded in
`plugins/sense/uv.lock` and `plugins/corpus/uv.lock`.

This file is informational and does not replace the license text supplied by any dependency.
