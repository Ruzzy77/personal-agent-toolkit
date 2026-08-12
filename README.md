# Personal Agent Toolkit

![Personal Agent Toolkit](./assets/personal-agent-toolkit-banner.png)

A local-first toolkit that helps AI work with the user's preferences, return to original sources,
and present each response in a form that suits the task. The marketplace currently contains:

- **Sense** carries one private, user-controlled work profile across AI tools.
- **Corpus** connects a task to the files, email, and completed AI work that belong with it, then
  opens the original sources needed now.
- **Hypes** adapts substantive responses and questions to what the user understands, needs to
  decide, and is trying to do, and carries forward only stable, scoped explanation clues at
  natural commitment points.

This repository contains plugin code, manifests, assets, and dependency locks, but no user data. It
does not contain a Sense profile, Corpus catalog or index, Hypes cognitive-model database, source
documents, saved context, provider conversations, credentials, or runtime databases.

## When the plugins run

The user does not need to name a plugin on every request. Once installed and enabled, Codex or
Claude can select them when a task calls for them:

- Sense can be selected when substantive work depends on the user's intent, working
  style, priorities, responsibility, or learning across completed work.
- Corpus can be selected when a task needs to understand an ongoing body of work or locate,
  compare, and verify its original sources.
- Hypes can be selected when an explicit correction, confirmed understanding, unresolved concept,
  or consequential choice should materially change the response. It should not load merely because
  a response is substantive.

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

## Choose the smallest installation that fits

- **Local only:** install any Sense, Corpus, or Hypes plugins you want. No gateway, tunnel, ChatGPT
  registration, hosted server, or cloud bill is needed.
- **Personal ChatGPT:** keep those installed products and add the optional `gateway/` component.
  Create one OpenAI Secure MCP Tunnel and one developer connection for each product you want in
  Chat. One macOS LaunchAgent supervises all selected products and tunnels. Product data remains on
  that Mac; no public inbound server or AWS deployment is needed.
- **Hosted or multi-user:** use the separately reviewed authenticated runtime when the service must
  stay available independently of a personal device or serve multiple identities. This is an
  advanced deployment, not a prerequisite for personal use.

The gateway is toolkit infrastructure, not a fourth plugin. It never creates a combined tool
namespace. A user may select Sense only, Corpus plus Hypes, or all three; local and Chat surfaces
continue to register and call each selected product independently.

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

### Personal ChatGPT through the optional gateway

The local product packages intentionally contain no `.app.json` or maintainer-owned ChatGPT
connection. To use selected products in a private Chat, create your own Platform tunnels and
developer connections, then use the optional [gateway guide](./gateway/GUIDE.md). The gateway:

- discovers selected products already installed and enabled in this Codex marketplace, or accepts
  an equivalent exact local package root;
- starts their packaged MCP launchers against the same existing local data;
- exposes fixed loopback paths through one gateway process;
- keeps one tunnel and one ChatGPT registration per selected product; and
- installs one supervising macOS LaunchAgent, regardless of whether one, two, or three products are
  selected.

Enabling it does not reinstall a product or migrate its data. Stopping the one gateway service
returns to local-only use without removing Sense, Corpus, Hypes, or their state. After a product
update, rerun the gateway installer and restart that LaunchAgent so the selected installed roots and
reviewed tool surface are refreshed.

The package builder creates a private marketplace containing only the selected product entries and
their own connection ids. It does not create a gateway plugin or a model-selected router. Test each
developer connection in a fresh Chat. Separately test the full skill-plus-app package on a ChatGPT
surface that supports local marketplace plugins; a working MCP connection alone does not prove that
ordinary Chat loads the local skill bundle.

Private developer registration is not public submission. Public directory distribution remains a
separate review process for each independently installable product.

See OpenAI's documentation for
[private tunnels](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels),
[developer testing](https://developers.openai.com/plugins/deploy/connect-chatgpt),
[plugin packaging](https://developers.openai.com/plugins/build/plugins), and
[public submission](https://developers.openai.com/plugins/deploy/submission).

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
When the user proposes a direction, Hypes compares its value, risks, reversibility, and likely
failure costs instead of opening with unsupported agreement or praise.

During the conversation, Hypes follows what the user has established they understand, what remains
unclear, the current decision, and how earlier explanations were received. It may ask one brief
question when a foundational concept will shape later work, a misunderstanding could change an
important choice, or a short probe can replace a long explanation. The question can ask the user
to choose a distinction, apply the idea, or explain it briefly in their own words.

The response to that question changes the next explanation. Hypes moves on when the user can apply
the idea, explains only the missing connection when needed, and stops asking when the user wants to
proceed or has limited attention. Native choices or a canvas may be used when they genuinely reduce
review effort, but simple requests stay in the conversation.

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

Hypes adds no fixed panel or separate writing-style choice. When its MCP is available, it keeps a
small private cognitive-model database outside the plugin package. The database contains compact
concept relationships and explanation clues with an exact topic, task, and responsibility scope;
it does not contain raw conversations, a general ability score, personality, or sensitive traits.

Provisional understanding stays in the visible conversation while work is underway. At task
completion, handoff, a material conclusion, or topic change, the calling agent may use
`hypes_revise` automatically when one compact relation is stable, reusable, exactly scoped,
non-sensitive, and likely to change a future explanation. It does not ask whether to save and does
not accumulate intermediate candidates. Silence, brief assent, preferences, agreement, project
facts, health, ability, personality, transcripts, full answers, and hidden reasoning are not
retention evidence. Agent-selected conversation conclusions are reviewed after 90 days by default;
relations the user explicitly asks Hypes to retain are reviewed after 180 days by default.

If current evidence conflicts with an active relation, `hypes_mark_recheck` suspends the old
relation without storing the competing claim or conversation. Conflicting or due-for-review
relations remain visible for inspection but do not silently influence an answer.

The MCP transport is sessionless. Reads carry their exact scope; relation retention, recheck, and
deletion writes carry the expected active-model revision and idempotency information. No call relies
on a previous connection or server process. Only relations that pass the retention gate persist
across processes.
If the MCP is unavailable, Hypes falls back to what is established in the visible conversation.
Simple retrieval, literal transformations, and direct one-step actions do not need it, and Codex or
Claude may not select it on every relevant response.

To check what Hypes took into account, ask “What do you currently believe I understand, and what
still seems unclear?” or “What did Hypes change in this response?” It will distinguish the visible
conversation from active stored clues, show recheck items when relevant, and will not invent an
earlier draft or display a score, personality profile, checklist, or internal process report.

## Data boundary

By default on macOS:

- Sense stores its private profile under `~/Library/Application Support/Sense/`.
- Corpus stores its catalog, indexes, and context under
  `~/Library/Application Support/Corpus/`.
- Provider-linked records remain with their original providers. Corpus stores limited record
  details and reads exact visible content only when explicitly requested.
- Hypes stores its compact private cognitive model under
  `~/Library/Application Support/Hypes/`. It stores no raw conversation, and all retained model
  state is available to the overview and deletion flow.

See [PRIVACY.md](./PRIVACY.md) for the full boundary.

## Validate the release

```sh
python3 scripts/validate_release.py
```

The validation checks manifests, license copies, package versions, file permissions, private-data
and credential patterns, empty-state behavior, Sense preview activation, Corpus first registration,
Hypes private-store permissions, skill structure and implicit-selection metadata, and real MCP
`initialize` plus `tools/list` handshakes for all three plugins. It also checks that product HTTP
listeners remain sessionless and loopback-only and that the optional gateway contains no product
runtime, connection id, credential, or user data.

## License

Personal Agent Toolkit is licensed under the [Apache License 2.0](./LICENSE). Runtime dependencies
are installed separately and retain their own licenses; see
[THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md).

---

이 저장소는 Sense, Corpus, Hypes와 이후 추가될 독립 플러그인의 공통 배포 채널입니다.
플러그인 코드와 설치 정보만 들어 있으며 개인 작업 프로필, 원문, 색인, 대화 상태,
자격 증명, 실행 중 생성되는 데이터베이스는 포함하지 않습니다. 각 플러그인의 데이터와
실행 경계는 분리되고 새 설치는 빈 상태로 시작합니다.
