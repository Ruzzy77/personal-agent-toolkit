---
name: recommend-help
description: Calculate a task-local, current-conversation shadow recommendation for substantive judgment, review, or handoff work. Use when the host agent should test how much help to provide without changing the actual response, adding Hypes UI, or learning across conversations. Do not use for simple retrieval, literal transformations, or direct one-step actions.
---

# Recommend Help

## Run the shadow pass

1. Form the baseline delivery plan from the request and applicable instructions. Freeze it before consulting Hypes.
2. Build a structured overlay from the current conversation only. Use typed identifiers and allowed effects for the present task and relation scope; never copy conversation text or free-form user constraints into the input.
   Reuse the most recent valid `next_overlay` only when it is still visible in the current task. If it is missing, start an empty overlay; do not reconstruct hidden state from conversation prose.
   Do not hand-edit `relation_states` or `seen_events`. Without a platform-provided prior-delivery record, do not fabricate an attributable outcome.
3. Add an overlay event only for:
   - a correction the user explicitly confirmed; or
   - an outcome attributable to the frozen baseline action that was actually delivered without Hypes assistance.
   Never attribute an outcome to the separate Hypes recommendation because the recommendation-only pass does not apply it.
4. Do not treat ordinary replies, silence, completion, or apparent acceptance as evidence.
5. Before the first invocation, read [references/contract.md](references/contract.md). Resolve `scripts/recommend_help.py` relative to this `SKILL.md`, then invoke it as `python3 <absolute-script-path>` and send the exact request shape as JSON on standard input.
6. Treat stdout as a separate shadow receipt. Require it to echo the baseline, include `shadow_recommendation`, and report `applied: false` and `persistent_write_count: 0`.
7. Deliver the frozen baseline plan. Do not let the receipt change its plan, content, wording, length, tools, or interface.
8. Discard the overlay and receipt when the task ends. The Hypes script must not create its own profile, file, database, log, network request, or cross-conversation store.

If the command is unavailable or rejects the input, continue with the baseline delivery plan and report the failure only when the user asked to inspect the shadow run.

The current release verifies that the script echoes the sealed baseline and records `applied: false`; it does not prove that final wording was causally unaffected after the same host agent saw the receipt. Do not describe implicit skill selection as a guaranteed per-turn hook in Codex, Claude, or another host.

## Keep it invisible

Do not add Hypes labels, panels, explanations, or other fixed chrome to the response. Show a concise receipt only when the user explicitly asks for the recommendation-only result or its diagnostics.
