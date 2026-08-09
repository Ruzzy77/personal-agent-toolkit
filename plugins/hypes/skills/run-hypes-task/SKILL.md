---
name: run-hypes-task
description: Use Hypes automatically for substantive design, research, planning, review, writing, implementation, and handoff work when adapting the depth or form of help may improve the user's understanding, judgment, or responsibility. Start or continue one task-local field loop without requiring the user to invoke Hypes by name. Also use when the user asks to apply or evaluate Hypes. Do not use for simple retrieval, literal transformations, direct one-step actions, or across conversations.
---

# Run Hypes for This Task

Use Hypes as background help adaptation inside one active task. Keep the answer's
facts, required content, and the user's responsibility under the host agent's
control. Hypes may change only the delivery strategy after the user chooses it.

Read [references/contract.md](references/contract.md) before the first call in a
task. Run the calculator with `python3` and the absolute path to
`scripts/run_hypes_task.py`. Send one JSON object on stdin and read one JSON
receipt from stdout.

## Selection boundary

- Start automatically when substantive work depends on how much explanation,
  support, dialogue, or responsibility handling the user needs. The user does
  not need to invoke Hypes by name.
- Skip simple retrieval, literal transformation, status lookup, and direct
  one-step actions whose result does not depend on adaptive help.
- Default to `hypes_proposal`. Use `baseline` or `scope_filter` only for an
  intentionally planned comparison.
- Continue on later substantive turns only when the latest valid
  `field_session` receipt remains visible in this same conversation.
- If the receipt is missing, invalid, compacted away, or belongs to another
  conversation, do not reconstruct prior observations from prose. Start a new
  empty session only if the current turn independently meets the selection
  boundary; otherwise continue normally.
- This skill selection is not a guaranteed host hook. Codex and Claude may
  select skills differently, so do not claim that Hypes ran on every turn.

## Per-turn workflow

1. Establish the normal answer plan first. Seal only bounded identifiers and
   its digest as `baseline_delivery_plan`; never send answer text or reasoning.
2. Call `prepare` with the previous `field_session`, the current relation scope,
   the baseline, and only newly confirmed observations.
3. If the proposal is identical to the baseline, keep it silently. If it
   materially differs, let the user choose the baseline or the Hypes proposal
   through the platform's lightest native interaction. Do not ask merely to
   announce that Hypes ran, and do not add a fixed panel, heading, or repeated
   product label.
4. Call `commit` with the user's choice. Applying a different Hypes strategy
   requires `user_confirmed_hypes_selection: true`; never infer this choice from
   silence, tone, or task continuation.
5. Deliver the committed strategy while preserving the exact required content
   IDs and human responsibility record returned by the calculator.
6. Before preparing the next substantive turn, call `attest_delivery` with the
   committed plan digest and the caller's structured delivery receipt. This is
   a caller attestation, not independent proof of platform delivery.
7. On a later `prepare`, add only a user-confirmed correction or an outcome that
   binds to a recorded delivery. Ordinary replies, apparent agreement, silence,
   completion, and unconfirmed impressions are not observations.
8. Call `close` when the task ends, show the summary only if useful, and discard
   the returned session after the task.

## Preserve these boundaries

- The host agent owns facts, required versus optional content, task actions,
  and the correctness of the baseline answer.
- Hypes changes only four delivery axes: information depth, support mode,
  dialogue move, and responsibility move.
- A high-impact approval always keeps human confirmation. Hypes never grants
  execution authority.
- Keep decision progress, error detection, independent follow-up, and
  responsibility understanding as separate outcome dimensions. Do not replace
  them with one score.
- A correction can mark a relation `unknown` or `likely_gap`; it cannot prove
  demonstrated ability. Demonstrated ability requires a bound, complete,
  independent outcome under this field contract.
- The script creates no file, database, log, network request, profile, or
  cross-conversation state. It reports `persistent_write_count: 0`.
- Do not send conversation text, response text, private reasoning, tool output,
  user identity, Sense data, Corpus content, or sensitive traits.
- Do not copy this session into Sense or Corpus. Any future durable update needs
  a separate user-visible contract and evidence threshold.

## Failure behavior

If the script is missing, rejects input, exits nonzero, or returns a receipt
whose digest or preserved boundaries do not validate, ignore the proposal and
deliver the already established baseline. Do not silently invent or repair
state. Mention diagnostics only when the user asks or when the failure blocks a
requested Hypes evaluation.

## Reporting

Keep ordinary task replies about the task itself. Do not prepend product chrome
such as `Hypes`, `이번 작업`, or `에이전트 결과`. When the user asks for the
field result, report the chosen condition, which turns used Hypes, the confirmed
observations, and the four outcome dimensions without implying learning across
conversations.
