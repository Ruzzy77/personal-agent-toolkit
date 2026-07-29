# Privacy boundary

Sense & Corpus is local-first and ships with no user data.

## Sense

Sense stores one private work profile on the user's machine. The profile contains durable ways of
working and cross-work learning, not project files or raw conversation history.

- The plugin package contains no default or active profile.
- Import creates a read-only preview.
- Activation requires an explicit local command with the reviewed revision and digest.
- Sensitive persistence and broader profile use require explicit user confirmation.
- Plugin updates do not replace the private profile database.

## Corpus

Corpus indexes only sources the user explicitly registers.

- Source bytes remain in their original locations and are not written by Corpus.
- Local indexes and reusable context stay in the private Corpus runtime directory.
- Gmail bodies remain with Gmail. Completed Codex and Claude turns remain with their providers.
- Provider links persist only bounded metadata, a locator, and a freshness identity.
- Exact provider content is read at request time and is not copied into the Corpus index.
- Archived context remains readable. Destructive context purge is not currently provided.

Some explicitly requested extraction operations may use an external host when the corpus execution
policy allows it. Optional hydration of remote placeholders may use network, disk, and local
residency. These actions are separate from ordinary local reads.

## Repository contents

The release repository must not contain:

- Sense or Corpus runtime databases;
- registered source contents or provider messages;
- `.env` files, credentials, tokens, or private keys;
- absolute paths from the maintainer's machine;
- generated caches, virtual environments, or staging files.

`scripts/validate_release.py` enforces these boundaries for the tracked release.
