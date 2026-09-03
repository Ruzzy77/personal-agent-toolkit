# Third-party dependencies

Personal Agent Toolkit is distributed under Apache-2.0. It does not vendor the runtime dependencies below;
the launchers install them from Python package indexes according to the committed lockfiles. Each
dependency remains subject to its own license.

Direct runtime dependencies:

| Dependency | Used by | License |
| --- | --- | --- |
| `@cloudflare/workers-oauth-provider` | Optional Personal Agent Auth | MIT |
| `@modelcontextprotocol/server` | Journal remote MCP | MIT |
| `defusedxml` | Corpus | Python Software Foundation License |
| `httpx` | Optional personal gateway | BSD-3-Clause |
| `jose` | Optional Personal Agent Auth | MIT |
| `mcp` | Sense, Corpus, Hypes | MIT |
| `next`, `react`, `react-dom` | Journal and Design Sites | MIT |
| `olefile` | Corpus | BSD |
| `openpyxl` | Corpus | MIT |
| `pydantic` | Sense, Corpus, Hypes | MIT |
| `pypdf` | Corpus | BSD-3-Clause |
| `python-docx` | Corpus | MIT |
| `python-pptx` | Corpus | MIT |
| `rhwp` 0.8.2 (optionally provisioned command-line backend) | Corpus | MIT |
| `starlette` | Optional personal gateway | BSD-3-Clause |
| `uvicorn` | Optional personal gateway | BSD-3-Clause |
| `vinext`, `@cloudflare/vite-plugin` | Journal and Design Sites | MIT |
| `zod` | Journal request validation | MIT |

Transitive dependencies in the current lockfiles use permissive licenses or MPL-2.0. The exact
resolved package names, versions, source hashes, and platform markers are recorded in
`plugins/sense/uv.lock`, `plugins/corpus/uv.lock`, `plugins/hypes/uv.lock`, and
`gateway/uv.lock`. JavaScript dependencies for Personal Agent Auth are recorded in
`auth/package-lock.json`, `services/journal/package-lock.json`, `sites/journal/package-lock.json`,
and `sites/design/package-lock.json`.

This file is informational and does not replace the license text supplied by any dependency.
