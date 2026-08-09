# Personal Agent Toolkit

![Personal Agent Toolkit](./assets/personal-agent-toolkit-banner.png)

A local-first toolkit for carrying personal context, tracing work back to sources, and adjusting
how AI helps. The marketplace currently contains:

- **Sense** carries one private, user-controlled work profile across AI tools.
- **Corpus** finds exact source passages and preserves reusable context with source links.
- **Hypes** adapts the depth and form of help when substantive work needs it, while keeping a
  recommendation-only control and creating no personal database.

This repository contains plugin code, manifests, assets, and dependency locks, but no user data. It
does not contain a Sense profile, Corpus catalog or index, Hypes conversation state, source
documents, saved context, provider conversations, credentials, or runtime databases.

## When the plugins run

The user does not need to name a plugin on every request. Once installed and enabled:

- Sense is eligible automatically when substantive work depends on the user's intent, working
  style, priorities, responsibility, or learning across completed work.
- Corpus is eligible automatically when a task requires locating, comparing, or verifying source
  material from registered collections.
- Hypes is eligible automatically when substantive work may need a different depth or form of help.

Simple retrieval, literal transformations, and direct one-step actions should not load unrelated
personal context. Skill selection is performed by the host and is not a guaranteed operating-system
hook, so a new task or session is required after plugin installation or update.

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
claude plugin install hypes@personal-agent-toolkit --scope user
```

Restart the host after installing or updating a plugin.

### Claude Cowork

The repository is also a Cowork plugin marketplace. In Claude, open
`Customize → Plugins → Add marketplace` and enter:

```text
Ruzzy77/personal-agent-toolkit
```

Install Sense, Corpus, and Hypes from that marketplace, then start a new local
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

The Hypes release has two task-local paths. `recommend-help` echoes the sealed baseline unchanged,
returns a separate recommendation with `applied: false`, and remains the control. `run-hypes-task`
is eligible automatically for substantive design, research, planning, review, writing,
implementation, and handoff work when a different depth or form of help may affect understanding,
judgment, or responsibility. It keeps the normal baseline and Hypes proposal separate, requires
the user to choose a differing proposal, and can use confirmed corrections or outcomes bound to an
earlier caller-attested delivery on the next turn.

Start a new task or session after installation. The host can select Hypes without a named request.
To test explicit invocation, use:

```text
Use Hypes whenever this task needs a different depth or form of help, and ask only when its proposal differs.
```

The returned task state must remain visible in the same conversation and is discarded when the
task closes. Simple retrieval, literal transformations, and direct one-step actions do not trigger
the field loop. Automatic selection remains a host decision rather than a guaranteed per-response
hook. The current release does not learn across conversations, and its delivery receipt is a caller
attestation rather than independent platform proof.

## Data boundary

By default on macOS:

- Sense stores its private profile under `~/Library/Application Support/Sense/`.
- Corpus stores its catalog, indexes, and context under
  `~/Library/Application Support/Corpus/`.
- Provider-linked records remain with their original providers. Corpus stores bounded metadata and
  reads exact visible content only when explicitly requested.
- Hypes keeps no personal database. Recommendation overlays and task-local field-session receipts
  are supplied by the caller in the same task and discarded when that task ends.

See [PRIVACY.md](./PRIVACY.md) for the full boundary.

## Validate the release

```sh
python3 scripts/validate_release.py
```

The validation checks manifests, license copies, package versions, file permissions, private-data
and credential patterns, empty-state behavior, Sense preview activation, Corpus first registration,
Hypes baseline preservation, its implicit-selection metadata, the field-loop selection and
next-turn update, and real MCP `initialize` plus `tools/list` handshakes.

## License

Personal Agent Toolkit is licensed under the [Apache License 2.0](./LICENSE). Runtime dependencies
are installed separately and retain their own licenses; see
[THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md).

---

이 저장소는 Sense, Corpus, Hypes와 이후 추가될 독립 플러그인의 공통 배포 채널입니다.
플러그인 코드와 설치 정보만 들어 있으며 개인 작업 프로필, 원문, 색인, 대화 상태,
credential, runtime database는 포함하지 않습니다. 각 플러그인의 데이터와 실행 경계는
분리되고 새 설치는 빈 상태로 시작합니다.
