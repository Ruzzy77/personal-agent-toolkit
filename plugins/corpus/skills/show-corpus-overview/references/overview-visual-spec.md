# Corpus overview visual specification

Use this specification with the current `visualize` skill. The overview is a temporary personal
workspace view, not a stored report or a new source of truth.

## Information model

Keep three layers legible without presenting them as a quality ladder:

1. **Work contexts** — ongoing bodies of work described by their purpose, recurring output or
   topic, current understanding, relationships, and open questions.
2. **Connected materials** — the file collections, provider records, and completed agent work
   linked to each work context. These links help locate originals; they are not copied content.
3. **Source inventory** — supported documents split into:
   - indexed;
   - local but not indexed: `supported_documents - indexed_documents -
     remote_supported_documents`, never below zero;
   - remote: `remote_supported_documents`.

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

1. A compact list of active work contexts. Show each readable title and purpose, with the kinds of
   materials connected to it. Do not select a context merely because it has more saved items.
2. A selected-work strip with its purpose, recurring output types, related source collections, and
   any material that must be read again. Selection must be keyboard accessible.
3. Three semantic groups: current understanding, relations and changes, and questions or gaps.
   Prefer readable text over badges. Show source counts only when they help choose what to verify.
4. A small source-to-context relation:

   `indexed file sources + linked provider records → reusable context items`

   This explains provenance; it must not imply that every source has been interpreted.
5. A collapsed source-inventory section with the selected corpus's last completed scan and one
   directly labelled horizontal bar for indexed, local-unindexed, and remote documents. Pair color
   with labels and exact counts. Mark partial extraction separately.
6. A collapsed interpretation-history section:
   - `active_item_count`: 저장한 내용;
   - `stale_item_count`: 원문을 다시 읽을 내용;
   - `superseded_item_count`: 이전 내용;
   - `archived_context_count`: 보관한 업무 맥락.
   Treat stale as a review state inside active items, not a separate total to add to them. Show
   archived context titles only when at least one exists; keep replaced item text collapsed unless
   the user asks for history.
7. One labelled action for the selected work context. It may ask Codex to continue that work and
   read the exact sources needed now through `window.openai.sendFollowUpMessage`.

Avoid a second dashboard, a document-count leaderboard, invented health scores, generic progress
labels, and decorative graphs. The selected work context should remain the dominant object.

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
