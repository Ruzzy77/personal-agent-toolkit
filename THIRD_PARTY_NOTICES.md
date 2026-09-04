# Third-party dependencies

Personal Agent Toolkit is distributed under Apache-2.0. Package managers install the runtime
dependencies below according to the committed lockfiles; their source is not vendored. Library
includes selected webfont files with their license texts. Each dependency and font remains subject
to its own license.

Notable direct runtime dependencies:

| Dependency | Used by | License |
| --- | --- | --- |
| `@cloudflare/workers-oauth-provider` | Optional Personal Agent Auth | MIT |
| `@modelcontextprotocol/server` | Personal Agent Context, Journal, Library, and Design remote MCP | MIT |
| `defusedxml` | Document Files | Python Software Foundation License |
| `httpx` | Personal Agent Sync | BSD-3-Clause |
| `jose` | Optional Personal Agent Auth | MIT |
| `mcp` | Sense, Corpus, Document Files, and Hypes local MCP | MIT |
| `next` | Journal, Design, and Library Sites | MIT |
| `olefile` 0.47 (dependency and vendored host fallback) | Document Files | BSD |
| `openpyxl` | Document Files | MIT |
| `pydantic` | Sense, Corpus, Document Files, and Hypes | MIT |
| `pypdf` | Document Files | BSD-3-Clause |
| `python-docx` | Document Files | MIT |
| `python-hwpx`, `python-hwpx-automation` | Document Files | Apache-2.0 |
| `python-pptx` | Document Files | MIT |
| `reportlab` | Document Files PDF creation | BSD-3-Clause |
| `react`, `react-dom` | Journal, Design, and Library Sites | MIT |
| `rhwp` 0.8.6 (pre-provisioned optional command-line backend) | Document Files | MIT |
| `watchdog` | Corpus | Apache-2.0 |
| `vinext`, `@cloudflare/vite-plugin` | Journal, Design, and Library Sites | MIT |
| `zod` | Personal Agent Context, Journal, Library, and Design request validation | MIT |

Bundled Library webfonts:

| Font | Source | License |
| --- | --- | --- |
| Noto Sans KR | Google Fonts / Fontsource | SIL Open Font License 1.1 |
| Noto Serif KR | Google Fonts / Fontsource | SIL Open Font License 1.1 |
| Barlow Condensed | Jeremy Tribby / Google Fonts | SIL Open Font License 1.1 |

Except for the documented `olefile` host fallback, dependency source is not vendored. Transitive
dependencies in the current lockfiles use permissive licenses or MPL-2.0. The exact
resolved package names, versions, source hashes, and platform markers are recorded in
`engines/sense/uv.lock`, `engines/corpus/uv.lock`, `engines/hypes/uv.lock`,
`plugins/document-files/uv.lock`, and `apps/sync/uv.lock`. JavaScript dependencies for the authentication
template, remote services, and Sites are recorded in `auth/package-lock.json`,
`services/remote-context/package-lock.json`,
`services/design/package-lock.json`, `services/journal/package-lock.json`,
`services/library/package-lock.json`, `sites/journal/package-lock.json`,
`sites/design/package-lock.json`, and `sites/library/package-lock.json`.

This file is informational and does not replace the license text supplied by any dependency.
