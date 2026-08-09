# Personal Agent Toolkit

![Sense and Corpus](./assets/sense-corpus-banner.png)

A local-first distribution for independent personal-agent plugins. The marketplace currently
contains:

- **Sense** keeps one private work profile for collaboration preferences and lessons that matter
  across different work.
- **Corpus** indexes user-selected folders and links reusable context back to exact sources.
- **Hypes** calculates a current-conversation help recommendation without applying it or creating
  a personal store.

This repository is an empty distribution: it contains plugin code, manifests, assets, and dependency
locks only. It does not contain a Sense profile, Corpus catalog or index, Hypes conversation state,
source documents, saved context, provider conversations, credentials, or runtime databases.

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
codex plugin add sense@personal-agent-toolkit
codex plugin add corpus@personal-agent-toolkit
codex plugin add hypes@personal-agent-toolkit
```

### Claude Code

```sh
claude plugin marketplace add .
claude plugin install sense@personal-agent-toolkit --scope user
claude plugin install corpus@personal-agent-toolkit --scope user
```

Hypes is Codex-only in this release. It will not be added to the Claude marketplace until its
selection and recommendation-only behavior are validated there.

Restart the host after installing or updating a plugin.

### Migrating from `sense-corpus`

The repository and marketplace were renamed to `personal-agent-toolkit` when Hypes became the third
independent plugin. Existing Codex installations can move to the new marketplace identity with:

```sh
codex plugin remove sense@sense-corpus
codex plugin remove corpus@sense-corpus
codex plugin marketplace remove sense-corpus
codex plugin marketplace add Ruzzy77/personal-agent-toolkit
codex plugin add sense@personal-agent-toolkit
codex plugin add corpus@personal-agent-toolkit
```

Private Sense and Corpus data lives outside the plugin packages and is not removed by this package
migration. Start a new Codex task after reinstalling so it receives the new plugin snapshot.

### Claude Cowork

The repository is also a Cowork plugin marketplace. In Claude, open
`Customize → Plugins → Add marketplace` and enter:

```text
Ruzzy77/personal-agent-toolkit
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

### Hypes

Hypes is a recommendation-only prototype for the current Codex task. It keeps the actual response
unchanged, returns a separate recommendation marked `applied: false`, and writes no profile or
database. Start a new Codex task after installation and invoke it explicitly first:

```text
Use Hypes to calculate the help mode separately without applying it to the response.
```

Automatic selection is a host choice, not a guaranteed per-response hook. The current release does
not learn across conversations.

## Data boundary

By default on macOS:

- Sense stores its private profile under `~/Library/Application Support/Sense/`.
- Corpus stores its catalog, indexes, and context under
  `~/Library/Application Support/Corpus/`.
- Provider-linked records remain with their original providers. Corpus stores bounded metadata and
  reads exact visible content only when explicitly requested.
- Hypes keeps no personal database. A same-task structured overlay is supplied by the caller and is
  discarded when that task ends.

See [PRIVACY.md](./PRIVACY.md) for the full boundary.

## Validate the release

```sh
python3 scripts/validate_release.py
```

The validation checks manifests, license copies, package versions, file permissions, private-data
and credential patterns, empty-state behavior, Sense preview activation, Corpus first registration,
Hypes baseline preservation, and real MCP `initialize` plus `tools/list` handshakes.

## License

Personal Agent Toolkit is licensed under the [Apache License 2.0](./LICENSE). Runtime dependencies
are installed separately and retain their own licenses; see
[THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md).

---

이 저장소는 Sense, Corpus, Hypes와 이후 추가될 독립 플러그인의 공통 배포 채널입니다.
플러그인 코드와 설치 정보만 들어 있으며 개인 작업 프로필, 원문, 색인, 대화 상태,
credential, runtime database는 포함하지 않습니다. 각 플러그인의 데이터와 실행 경계는
분리되고 새 설치는 빈 상태로 시작합니다.
