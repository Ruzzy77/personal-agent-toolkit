# Corpus overview visual specification

Use this specification with the current `visualize` skill. The overview is a temporary personal
workspace view, not a stored report or a new source of truth.

## Information model

Keep three layers legible without presenting them as a quality ladder:

1. **Source inventory** — supported documents split into:
   - indexed;
   - local but not indexed: `supported_documents - indexed_documents -
     remote_supported_documents`, never below zero;
   - remote: `remote_supported_documents`.
2. **Linked sources** — provider records such as Gmail observations. These are locators and bounded
   metadata, not locally copied message bodies.
3. **Reusable understanding** — named context items written by an agent and linked to sources.

Show partial extraction as a condition on indexed documents, not as a fourth mutually exclusive
inventory segment. Show active source-unit count as scale, not as a completion score.

Group context item kinds for reading:

| Saved kind | User-facing group |
|---|---|
| `finding` | 현재 이해 |
| `relationship`, `difference` | 관계와 변화 |
| `question`, `gap` | 확인할 것 |

Keep the original item wording. A group with no items should say that no reusable item has been
saved; do not infer that the underlying corpus lacks the information.

## Composition

Use one responsive surface with this reading order:

1. At most three workspace totals: corpus count, indexed/supported documents, and reusable context
   items. Put linked-record count beside its source mark rather than adding a fourth summary card.
2. A compact corpus selector. Each choice shows one readable corpus name and a small coverage
   indicator; selection must be keyboard accessible.
3. A selected-corpus strip with purpose, last completed scan, and inventory-completeness state.
4. One directly labelled horizontal inventory bar for indexed, local-unindexed, and remote
   documents. Pair color with labels and exact counts. Mark the partial-extraction count separately.
5. A small source-to-context relation:

   `indexed file sources + linked provider records → reusable context items`

   This explains provenance; it must not imply that every source has been interpreted.
6. Three semantic groups: current understanding, relations and changes, and questions or gaps.
   Prefer readable text over badges. Show source counts only when they help choose what to verify.
7. A compact interpretation-history strip:
   - `active_item_count`: 현재 해석;
   - `stale_item_count`: 재검토 필요;
   - `superseded_item_count`: 교체 이력;
   - `archived_context_count`: 보관한 맥락.
   Treat stale as a review state inside active items, not a separate total to add to them. Show
   archived context titles only when at least one exists; keep replaced item text collapsed unless
   the user asks for history.
8. One labelled drill-down action for the selected corpus. It may ask Codex to investigate exact
   current sources through `window.openai.sendFollowUpMessage`.

Avoid a second dashboard, a document-count leaderboard, invented health scores, generic progress
labels, and decorative graphs. The selected corpus should remain the dominant object.

## Freshness and boundaries

- Label timestamps as the last saved scan or observation, not as “live”.
- If `inventory_complete=false`, say “목록 확인 미완료”.
- If context items are truncated, show the available count and say more items exist.
- If stale counts are truncated, label them as a lower bound rather than an exact total.
- Superseded history and archived contexts are provenance, not current understanding.
- Preserve the response audience. Do not invent omitted local-only corpora, local paths, or private
  IDs.
- Personal view may show the returned names, people, dates, amounts, and context text. Do not add
  extra masking. General-view rules apply only when the user explicitly requests external reuse.

## Accessibility and fallback

Use semantic buttons, visible labels, direct values, and a screen-reader summary for the inventory
bar. Support 736 px down to 320 px without horizontal scrolling. In a non-visual fallback, show one
summary table and the same three semantic groups; do not dump raw JSON.
