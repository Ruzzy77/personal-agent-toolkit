# Hypes field-v0 contract

## Purpose

This contract lets a host agent try Hypes inside one explicitly activated task.
It separates a normal baseline plan, a Hypes proposal, the user's selection,
caller-attested delivery, and later confirmed outcomes. It does not create a
personal database or learn an intervention policy.

## Fixed identifiers

- `schema_version`: `0.1.0`
- `expected_policy_id`: `hypes-field-fixed-v0.1.0`
- `task_contract_id`: `judgment-handoff-v0`
- trial condition: `baseline | scope_filter | hypes_proposal`

Every identifier is lowercase ASCII with digits and `._:-`; every content body
is represented by an identifier and a SHA-256 digest. Inputs are strict: missing
or unknown fields reject the whole request.

## Session lifecycle

```text
start
  -> prepare
  -> commit(baseline | confirmed Hypes proposal)
  -> host delivers answer
  -> attest_delivery
  -> prepare ...
  -> close
```

The caller carries the complete returned `field_session` verbatim. The script
stores nothing. A session is bound to one `conversation_id`, fixed policy,
contract, and condition. It has at most one pending turn. A closed session
cannot reopen.

## Operations

### `start`

Required fields in addition to the shared envelope are `field_session_id`,
`conversation_id`, `task_contract_id`, and `trial_condition`. The returned state
is active, empty, self-digested, and has `persistent_write_count: 0`.

### `prepare`

Required fields are `field_session`, `turn_id`, `relation_scope`,
`assistance_allowed`, `baseline_delivery_plan`, and `observations`.

`relation_scope` is exactly:

```json
{"project_id":"...","task_relation":"...","responsibility":"ordinary|approve_high_impact"}
```

The baseline contains a sealed baseline ID and digest, required and optional
content IDs, the four-axis delivery strategy, and human responsibility. Its
responsibility class must equal the scope responsibility. High-impact approval
requires `responsibility_move: request_confirmation` and remains human-owned.

The result is an unapplied proposal plus exact copies of required content IDs
and human responsibility. The proposal may differ only on:

- `information_depth`: `minimal | standard | expanded`
- `support_mode`: `none | example | scaffold`
- `dialogue_move`: `answer | ask | challenge | defer`
- `responsibility_move`: `deliver | request_confirmation | defer`

### `commit`

Bind `pending_plan_digest` to the prepared plan. Select `baseline` with no Hypes
confirmation, or select a differing `hypes` proposal with explicit confirmation.
The baseline control condition forbids Hypes selection. Commit does not assert
that a response was delivered.

### `attest_delivery`

Bind `pending_commit_digest`, a unique `delivery_id`, and a caller-provided
delivery receipt digest. Both `delivered` and `caller_attested` are exactly true.
The returned immutable delivery record preserves required content, human
responsibility, selected strategy, and whether Hypes help was applied. It opens
the attribution window but does not independently prove platform delivery.

### `close`

Close only with no pending turn. The summary keeps the four outcome dimensions
separate and sets `aggregate_reward` to null. Discard the session after use.

## Observations

`confirmed_correction` requires the same typed relation scope, a monotonic
sequence, `confirmed_by_user: true`, and `effect: unknown | likely_gap`.

`attributable_field_outcome` must bind the exact recorded delivery ID, receipt
digest, and scope. Its source is `user_confirmed | project_evaluator`. The four
dimensions are:

- `decision_progress`
- `error_detection`
- `independent_followup`
- `responsibility_understanding`

Each value is `yes | no | not_observed | not_applicable`. Any `no` marks a likely
gap for the matching relation. Demonstrated ability requires decision progress,
responsibility understanding, and independent follow-up to be `yes`, with error
detection `yes` or `not_applicable`. A result after Hypes help can still inform
the next task-local proposal, but it is not durable independent evidence.

Exact event replay is idempotent. Reusing an event ID with changed content or
adding an older sequence rejects the request atomically. Unrelated scopes remain
stored only in the caller-carried session and do not affect another relation.

## Comparison conditions

- `baseline`: preserve the baseline strategy; Hypes cannot be selected.
- `scope_filter`: confirmed corrections may alter the proposal; outcomes are not
  used for strategy projection.
- `hypes_proposal`: confirmed corrections and bound outcomes may alter the next
  proposal, subject to the user's per-turn selection.

These are mechanism comparisons within one task, not proof of user benefit.

## Trust and failure boundary

The host owns baseline correctness and attests actual delivery. The script can
check hashes and internal consistency, not whether the platform truly displayed
the response or whether an outcome is epistemically correct. Invalid input or an
execution error returns a self-digested error receipt and no persistent write;
the host falls back to the baseline without reconstructing missing state.
