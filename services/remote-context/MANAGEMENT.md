# Shared management contracts

## Ownership and public adapters

`packages/remote-runtime` owns operation identity, schemas, actor/scope checks,
effects, retry classification, support/authorization reporting and safe conflict
errors. The existing Context, Library and Design operation registries supply both
MCP and HTTP. Legacy tool inputs and response envelopes remain compatibility
adapters; HTTP-only binary downloads and imports remain explicit operations, not
additional public MCP tools. Journal consumes the common scope/error contract
without changing its event, closing or correction model. Hypes keeps graph deletion.
Product services, not the runtime, own D1 transactions and policy decisions.

Ordinary saves retain their existing compare-and-swap behavior and do not create
permanent history. Moves, deletion groups, purge and their resumable coordination
use exact request keys. A reused key with a different payload conflicts; an absent
receipt means unconfirmed, not failed. Capability status, rollout activation and
caller authorization are separate fields.

## Corpus identity and lifecycle

Migration 0005 adds a stable owner-scoped native UID and read-only old-locator
aliases. Current and previous snapshots move with the UID, not as copies. Legacy
Context items keep `item_id`. Both Space Context versions are checked before a move.
Document-name or alias collisions are refused without merging. The old
`corpus_document_restore` remains previous-body restoration.

Source references retain their original Space and Connection plus exact document,
revision, projection and unit. Unambiguous legacy origins are backfilled; ambiguous
origins remain unresolved and block a move. Scoped writes require a reader capable
of preserving `source_scope_version=2`. Source protection reservations precede D1
commit; they are not a distributed transaction. A confirmed receipt permits safe
reservation release on replay. Uncertain outcomes remain protected.

A Space owns its native documents, Context items and Context Skill, not Connections,
Workspace registrations, original files or shared Source data. Trashing is one
logical D1 transition. Earlier deletion groups retain their own members and dates.
Restore validates all members and destinations before any write; it does not revive
members from another deletion. A Space in trash rejects new Work and Source refresh
admissions. Existing structured evidence keeps its previous access boundary.

Migration 0007 adds durable registration retirement. The remote service checks the
current generation and an upgraded Sync device, blocks new work, then queues the
formal `registration.detach` operation. Sync serializes it behind active local
operations and Source maintenance, records a durable tombstone, and never changes
original files. Only the exact device acknowledgement completes remote retirement.
Offline or uncertain execution stays pending/blocked. Tombstones survive Space
purge and stop an older configuration from resurrecting registrations.

## Sense and immutable media

Migration 0006 supplies ordinary Sense section/Skill deletion groups. Structure
changes guard the whole profile token and every affected Skill version. Sensitive
sections are neither changed nor added to the ordinary trash surface.

Library and Design keep separate D1 ownership/reference indexes and lifecycle
state. `packages/immutable-assets` supplies only immutable R2 identity/storage:
identical bytes and MIME can be reused; a different MIME is a different identity.
Library classification/date and Design validation status are not lifecycle states.
Library indexes supported `/media/` references; unknown ownership or incomplete
inventories block cleanup. Design indexes its file manifest and checks all included
file revisions. Old unreferenced objects are never swept as a side effect.

Before physical deletion, a guarded D1 batch marks selected objects as deleting,
blocking new references and uploads. R2 deletion is a separately resumable step.
DB references are removed only after the relevant object deletions are confirmed.
Shared objects are excluded. A failed/uncertain R2 operation is not completion and
cannot be restored once physical purge has started.

## Retention and maintenance

Only new trash groups receive a deadline: the original server timestamp plus
30×24 hours. A duplicate request never restarts it; restore followed by a new trash
operation creates a new group/deadline. Existing archived/deprecated records and
Source retention are unchanged. Manual purge additionally requires explicit owner
confirmation. Both manual and automatic purge block on structured external
references, registrations or uncertainty; prose mentions are not inferred links.

The integrated Worker's cron is `0 19 * * *` (04:00 Asia/Seoul). It considers at most
5 groups per product per run, ordered by last attempt; object steps handle 20 keys
and large member deletion resumes in batches of 100. A conditional state transition
arbitrates restore versus cleanup. The internal sweeper can only process due trash,
not edit ordinary content or permissions. Latest run and group blockers stay in the
product stores, without a central audit or notification service.

## Release sequence and rollback floor

1. Check the affected service contracts, Sync, Site and repository consistency.
   Refresh `file:` dependencies with `npm ci`; never edit copied node_modules.
2. Deploy compatible readers to individual Library/Design/Journal Workers and the
   integrated Context Worker with all management writes and sweep disabled. Record
   those compatible deployments as the rollback floor.
3. Obtain recovery material for only changed D1 stores. Compare original IDs, bodies,
   approvals, current/previous evidence and asset manifests using an isolated copy;
   apply versioned D1 migrations and compare the live data without deleting it.
4. Enable Corpus independently, then Sense/Space and Library/Design after each
   product's checks. Deploy corresponding private management screens. Preserve
   Context's explicit save and Library's autosave; no editor state is auto-merged.
5. Upgrade Sync through `apps/sync/scripts/install-runtimes.sh`, preserving existing
   configuration, credentials, runtime records and Document Files 1.7.0. Verify the
   live device capability before enabling a real detach request.
6. Keep `TRASH_SWEEP_ENABLED=false` for a scheduled dry run. Inspect candidates,
   blockers and latest execution, then activate the same bounded daily handler.
   Dry mode does not change group lifecycle or touch R2.
7. Publish matching product Skills/manifests and refresh supported clients through
   their normal update paths. A local build or installation listing is not fresh
   tool exposure. Verify an authorized read, not a real-material deletion test.
8. After migration/restore validation, remove owned temporary exports and staging
   artifacts. Never roll back below the UID/origin/lifecycle-aware reader; disabling
   new writes does not authorize an old writer or an owner-wide import.

The production KIRIA, Sense, Hypes and other business records must not be used as
deletion fixtures. Time boundaries, concurrency and failures belong in isolated
Cloudflare/SQLite tests. Deployment and client refresh are separate completion gates.
