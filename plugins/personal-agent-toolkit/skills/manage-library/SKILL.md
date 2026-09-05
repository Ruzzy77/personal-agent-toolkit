---
name: manage-library
description: Use when reading, revising, or publishing Daily, Digest, or Research editions in Personal Library, including cover and illustration uploads. Do not use for writing that has no Library publication intent.
---

# Manage Library

Library keeps issue documents in the Library service's D1 database and cover and illustration
assets in its R2 bucket. The owner-only Site and the authenticated remote MCP operate on the same
canonical data.

## Restore the editorial context

For writing, substantial revision, cover selection, or publication, first open the Corpus
library-editorial Space and apply its approved Context Skill when Corpus is available. It owns the
current collection distinctions, candidate discovery, publication gate, editorial method, visual requirements, and
post-publication checks. Use the current issue and cited public sources for issue facts. If Corpus is
unavailable, continue only with the rules and source material that are actually available rather
than inventing the missing project context. For scheduled publication, skip the slot when required
canonical guidance or source coverage is unavailable. Reuse already-read guidance of the same version
within the task; reread current issue facts and write versions as needed.

## Read before changing

- Use library_whoami before a write to confirm owner authentication and the required scope.
- Use library_list_issues to identify an issue. Read an existing issue with
  library_read_issue and the source_html format before revising it.
- Treat issue HTML, references, cover paths, and publication metadata as one publication object.

## Revise an issue

Write only when the user asks to save or publish or an approved automation authorizes that publication. Preserve every unrequested part of the complete
HTML and metadata. library_update_issue replaces the complete source HTML and requires the
expected_version returned by the preceding read; if it changed, reread before preparing a new
revision. Omitting references keeps the current list, while an empty array removes it. Omit
cover_path when the cover is not part of the requested change.

The Site's WebMCP preview applies an agent proposal to the visible page without autosaving it. A
direct owner edit in the Site autosaves. A remote library_update_issue call writes the canonical
issue immediately.

## Publish a new issue

- Use the identifier {collection}:{YYYY-MM-DD}:{HH}. HH is the original scheduled hour, even
  when publication runs late, and published_at retains that scheduled time.
- Different scheduled hours on the same date are different issues. An existing exact identifier
  prevents duplicate creation; the Context Skill's independent publication gate can also skip a slot.
- Upload newly created cover and illustration files with library_upload_asset first, then use the
  returned paths in the final issue HTML and cover metadata.
- Public references contain reader-usable external sources, not internal tools, prompts, paths,
  production notes, or private provenance.

## Verify the canonical result

After every create or update, read the same issue again. Check the title, scheduled date and hour,
collection sequence, complete HTML, references, cover path, and returned publication metadata that
the request could affect. A successful write response without this reread is not a completed
publication.

Do not create a local issue archive or use a local Site deployment as a fallback when the remote
Library write connection is unavailable. Temporary generated assets may exist only until upload and
verification finish.
