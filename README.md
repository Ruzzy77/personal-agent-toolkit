# Sense & Corpus

![Sense and Corpus](./assets/sense-corpus-banner.png)

Two local-first plugins for working with AI tools without shipping personal data in the plugin.

- **Sense** keeps one private work profile for collaboration preferences and lessons that matter
  across different work.
- **Corpus** indexes user-selected folders and links reusable context back to exact sources.

This repository is an empty distribution: it contains plugin code, manifests, assets, and dependency
locks only. It does not contain a Sense profile, Corpus catalog or index, source documents, saved
context, provider conversations, credentials, or runtime databases.

## Requirements

- macOS
- [uv](https://docs.astral.sh/uv/)
- Codex, Claude Code, or a local Claude Cowork session with plugin support

The launchers provision isolated Python environments from the committed lockfiles. Python 3.11 or
newer is required.

## Install from a checkout

Clone this repository and run the commands from its root.

### Codex

```sh
codex plugin marketplace add .
codex plugin add sense@sense-corpus
codex plugin add corpus@sense-corpus
```

### Claude Code

```sh
claude plugin marketplace add .
claude plugin install sense@sense-corpus --scope user
claude plugin install corpus@sense-corpus --scope user
```

Restart the host after installing or updating a plugin.

### Claude Cowork

The repository is also a Cowork plugin marketplace. In Claude, open
`Customize → Plugins → Add marketplace` and enter:

```text
Ruzzy77/sense-corpus
```

Install both Sense and Corpus from that marketplace, then start a new local
Cowork session. Full local MCP functionality is intended for Cowork and Code;
ordinary Chat sessions are not a supported runtime target for this release.

## First use

### Sense

Sense intentionally starts without a profile. Copy the example, replace its placeholder text with
your own reviewed preferences, and import it as a read-only preview.

```sh
cp examples/sense-profile.example.json /tmp/my-sense-profile.json
# Edit /tmp/my-sense-profile.json before continuing.

./plugins/sense/launchers/sense import-profile \
  --input /tmp/my-sense-profile.json
./plugins/sense/launchers/sense read --view full
./plugins/sense/launchers/sense status
```

After reviewing the exact preview, activate the revision and digest returned by `status`:

```sh
./plugins/sense/launchers/sense activate \
  --expected-revision 1 \
  --confirm-profile-digest PROFILE_SHA256_FROM_STATUS \
  --confirm-reviewed-profile
```

Activation is deliberately a local user action. An AI tool cannot activate, forget, or remove the
profile on the user's behalf.

### Corpus

Corpus also starts empty. Register only a folder you want Corpus to observe:

```sh
./plugins/corpus/launchers/corpus corpus add \
  --id my-work \
  --root /absolute/path/to/my-work \
  --execution-policy local_only
```

Then ask the AI tool to show the Corpus overview, scan the selected corpus, or find exact source
passages. Source files remain read-only. Indexes and reusable context are private runtime data
outside this repository.

## Data boundary

By default on macOS:

- Sense stores its private profile under `~/Library/Application Support/Sense/`.
- Corpus stores its catalog, indexes, and context under
  `~/Library/Application Support/Corpus/`.
- Provider-linked records remain with their original providers. Corpus stores bounded metadata and
  reads exact visible content only when explicitly requested.

See [PRIVACY.md](./PRIVACY.md) for the full boundary.

## Validate the release

```sh
python3 scripts/validate_release.py
```

The validation checks manifests, license copies, package versions, file permissions, private-data
and credential patterns, empty-state behavior, Sense preview activation, Corpus first registration,
and real MCP `initialize` plus `tools/list` handshakes.

## License

Sense & Corpus is licensed under the [Apache License 2.0](./LICENSE). Runtime dependencies are
installed separately and retain their own licenses; see
[THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md).

---

이 저장소에는 플러그인 코드와 설치 정보만 들어 있습니다. 개인 작업 프로필, 원문,
색인, 대화 기록, credential, runtime database는 포함하지 않으며 새 설치는 빈 상태로
시작합니다.
