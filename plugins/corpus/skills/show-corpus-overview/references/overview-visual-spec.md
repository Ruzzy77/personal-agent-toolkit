# Corpus overview visual

The overview is a temporary personal view, not a stored report or a new source.

## What to show

Keep three layers distinct:

1. **Saved contexts** — readable titles, purposes, relationships, and open questions.
2. **Connected material** — file collections and provider records that point to originals.
3. **Readable coverage** — indexed, local but not indexed, and remote documents.

Partial extraction is a condition on indexed documents, not a separate category. Source-unit count
shows only how much text is indexed; it says nothing about whether the material was interpreted.

Group saved items as:

| Stored kind | Label |
|---|---|
| `finding` | 현재 해석 |
| `relationship`, `difference` | 관계와 변화 |
| `question`, `gap` | 확인할 것 |

Keep the original wording. An empty group means nothing has been saved in that group; it does not
mean the sources contain nothing.

## Layout

Use one responsive view in this order:

1. A compact list of active contexts with title, purpose, and connected material types.
2. The selected context and anything that needs a fresh read.
3. The three saved-item groups above.
4. A collapsed coverage section with exact counts and last completed scan.
5. A collapsed context section with saved-item, changed-source, and archived-context counts.
6. One plainly labelled action to verify the selected item against exact current sources.

Do not add health scores, percentage gauges, leaderboards, decorative graphs, or a second dashboard.
Do not imply that every indexed source has been interpreted.

Label timestamps as the last saved scan or observation, never live. Mark incomplete inventory,
truncation, and lower-bound counts precisely. Preserve omitted local-only collections and private
identifiers. Use semantic controls, visible labels, exact values, and no horizontal scrolling from
736 px down to 320 px. A text fallback should use one summary table and the same three item groups,
not raw JSON.
