# Personal Agent Toolkit

![Personal Agent Toolkit](./assets/personal-agent-toolkit-banner.png)

A local-first toolkit that helps AI work with the user's preferences, return to original sources,
and present each response in a form that suits the task. The marketplace currently contains:

- **Sense** carries one private, user-controlled work profile across AI tools.
- **Corpus** connects a task to the files, email, and completed AI work that belong with it, then
  opens the original sources needed now.
- **Hypes** adapts substantive responses and questions to what the user understands, needs to
  decide, and is trying to do in the current conversation.

This repository contains plugin code, manifests, assets, and dependency locks, but no user data. It
does not contain a Sense profile, Corpus catalog or index, Hypes conversation state, source
documents, saved context, provider conversations, credentials, or runtime databases.

## When the plugins run

The user does not need to name a plugin on every request. Once installed and enabled, Codex or
Claude can select them when a task calls for them:

- Sense can be selected when substantive work depends on the user's intent, working
  style, priorities, responsibility, or learning across completed work.
- Corpus can be selected when a task needs to understand an ongoing body of work or locate,
  compare, and verify its original sources.
- Hypes can be selected when a substantive response should fit the user's current purpose,
  confirmed understanding, unresolved points, or decision responsibility.

Simple retrieval, literal transformations, and direct one-step actions should not load unrelated
personal context. Codex and Claude decide when to use a skill, so a relevant skill may not be used
on every response. Start a new task or session after installing or updating a plugin so the new
version can load.

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

Start a new task or session after installing or updating a plugin.

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

Corpus also starts empty. Register only a folder you want Corpus to search:

```sh
./plugins/corpus/launchers/corpus corpus add \
  --id my-work \
  --root /absolute/path/to/my-work \
  --execution-policy local_only
```

Then ask the AI tool what work contexts Corpus can help continue, or ask it to connect the current
task to its files, email, completed AI work, and exact source passages. Source files remain
read-only. Indexes and saved context are private runtime data outside this repository.

### Hypes

Hypes works in the background on substantive tasks. It writes the actual response with the detail
the task needs, removes repeated context and process-heavy wording, adds explanation only when it
helps, and keeps important facts, uncertainty, and decisions the user must make. Explicit wording
or explanation corrections in the current conversation should affect the next relevant response.

During the conversation, Hypes follows what the user has established they understand, what remains
unclear, the current decision, and how earlier explanations were received. It asks one focused
question only when the missing point would change the answer or action. Native choices or a canvas
may be used when they genuinely reduce review effort, but simple requests stay in the conversation.

It does not replace the result with project-management or engineering narration, turn a bounded
edit into a research or governance program, list routine Git and test details, or weaken every main
point with a reflexive caveat. Necessary limits stay where they materially affect interpretation or
action, and the writing keeps the voice and structure of its actual genre.

Agent-written drafts remain agent-written. A user's direction, selection, edit request, or
permission to proceed is not described as user authorship or line-by-line approval unless the user
explicitly adopts that wording or claim.

Start a new task or session after installation. Codex or Claude can select Hypes without a named
request. To test explicit invocation, use:

```text
Use Hypes to make this response clear, natural, and appropriately detailed.
```

Hypes adds no fixed panel or separate writing-style choice. Its provisional view of understanding
stays in the current conversation; it does not build a profile or learn across conversations.
Simple retrieval, literal transformations, and direct one-step actions do not need it. Codex or
Claude may not select it on every relevant response.

To check what Hypes took into account, ask “What do you currently believe I understand, and what
still seems unclear?” or “What did Hypes change in this response?” It will answer only from the
visible conversation. It will not invent an earlier draft or display a score, profile, checklist,
or internal process report.

## Data boundary

By default on macOS:

- Sense stores its private profile under `~/Library/Application Support/Sense/`.
- Corpus stores its catalog, indexes, and context under
  `~/Library/Application Support/Corpus/`.
- Provider-linked records remain with their original providers. Corpus stores limited record
  details and reads exact visible content only when explicitly requested.
- Hypes keeps no personal database and adds no storage beyond the conversation already available
  in the current task.

See [PRIVACY.md](./PRIVACY.md) for the full boundary.

## Validate the release

```sh
python3 scripts/validate_release.py
```

The validation checks manifests, license copies, package versions, file permissions, private-data
and credential patterns, empty-state behavior, Sense preview activation, Corpus first registration,
Hypes skill structure and implicit-selection metadata, and real MCP `initialize` plus `tools/list`
handshakes.

## License

Personal Agent Toolkit is licensed under the [Apache License 2.0](./LICENSE). Runtime dependencies
are installed separately and retain their own licenses; see
[THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md).

---

이 저장소는 Sense, Corpus, Hypes와 이후 추가될 독립 플러그인의 공통 배포 채널입니다.
플러그인 코드와 설치 정보만 들어 있으며 개인 작업 프로필, 원문, 색인, 대화 상태,
자격 증명, 실행 중 생성되는 데이터베이스는 포함하지 않습니다. 각 플러그인의 데이터와
실행 경계는 분리되고 새 설치는 빈 상태로 시작합니다.
