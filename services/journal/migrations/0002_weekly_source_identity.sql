CREATE TABLE items_v2 (
  id TEXT PRIMARY KEY NOT NULL,
  week_id TEXT NOT NULL REFERENCES weeks(id) ON DELETE RESTRICT,
  source_kind TEXT NOT NULL,
  source_key TEXT NOT NULL,
  source_ref TEXT,
  source_version TEXT,
  project_key TEXT,
  title TEXT NOT NULL,
  summary TEXT NOT NULL,
  lane TEXT NOT NULL CHECK (lane IN ('today', 'direct', 'waiting', 'attention')),
  resolution TEXT NOT NULL DEFAULT 'active' CHECK (resolution IN ('active', 'held', 'completed', 'canceled')),
  due_at TEXT,
  durable_outcome TEXT,
  corpus_target_space TEXT,
  version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (week_id, source_kind, source_key)
);

INSERT INTO items_v2 (
  id, week_id, source_kind, source_key, source_ref, source_version,
  project_key, title, summary, lane, resolution, due_at,
  durable_outcome, corpus_target_space, version, created_at, updated_at
)
SELECT
  id, week_id, source_kind, source_key, source_ref, source_version,
  project_key, title, summary, lane, resolution, due_at,
  durable_outcome, corpus_target_space, version, created_at, updated_at
FROM items;

CREATE TABLE journal_events_backup AS SELECT * FROM journal_events;
CREATE TABLE ingest_receipts_backup AS SELECT * FROM ingest_receipts;
CREATE TABLE corpus_promotion_receipts_backup AS
  SELECT * FROM corpus_promotion_receipts;

DROP TABLE corpus_promotion_receipts;
DROP TABLE ingest_receipts;
DROP TABLE journal_events;
DROP TABLE items;
ALTER TABLE items_v2 RENAME TO items;

CREATE TABLE journal_events (
  id TEXT PRIMARY KEY NOT NULL,
  week_id TEXT NOT NULL REFERENCES weeks(id) ON DELETE RESTRICT,
  item_id TEXT REFERENCES items(id) ON DELETE RESTRICT,
  event_type TEXT NOT NULL CHECK (
    event_type IN (
      'item_created',
      'observation_updated',
      'resolution_changed',
      'week_closed',
      'correction_added',
      'corpus_promoted'
    )
  ),
  actor_kind TEXT NOT NULL CHECK (actor_kind IN ('owner', 'automation', 'source')),
  actor_ref TEXT,
  payload_json TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  occurred_at TEXT NOT NULL,
  created_at TEXT NOT NULL
);

INSERT INTO journal_events (
  id, week_id, item_id, event_type, actor_kind, actor_ref,
  payload_json, idempotency_key, occurred_at, created_at
)
SELECT
  id, week_id, item_id, event_type, actor_kind, actor_ref,
  payload_json, idempotency_key, occurred_at, created_at
FROM journal_events_backup;

CREATE TABLE ingest_receipts (
  idempotency_key TEXT PRIMARY KEY NOT NULL,
  source_kind TEXT NOT NULL,
  source_key TEXT NOT NULL,
  source_version TEXT,
  item_id TEXT NOT NULL REFERENCES items(id) ON DELETE RESTRICT,
  created_at TEXT NOT NULL
);

INSERT INTO ingest_receipts (
  idempotency_key, source_kind, source_key, source_version, item_id, created_at
)
SELECT
  idempotency_key, source_kind, source_key, source_version, item_id, created_at
FROM ingest_receipts_backup;

CREATE TABLE corpus_promotion_receipts (
  id TEXT PRIMARY KEY NOT NULL,
  week_id TEXT NOT NULL REFERENCES weeks(id) ON DELETE RESTRICT,
  item_id TEXT REFERENCES items(id) ON DELETE RESTRICT,
  target_space TEXT NOT NULL,
  source_path TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('applied', 'skipped', 'failed')),
  details TEXT,
  created_at TEXT NOT NULL,
  UNIQUE (week_id, item_id, target_space, content_hash)
);

INSERT INTO corpus_promotion_receipts (
  id, week_id, item_id, target_space, source_path, content_hash,
  status, details, created_at
)
SELECT
  id, week_id, item_id, target_space, source_path, content_hash,
  status, details, created_at
FROM corpus_promotion_receipts_backup;

DROP TABLE journal_events_backup;
DROP TABLE ingest_receipts_backup;
DROP TABLE corpus_promotion_receipts_backup;

CREATE INDEX idx_items_week_resolution_lane
  ON items(week_id, resolution, lane);

CREATE INDEX idx_items_project_week
  ON items(project_key, week_id)
  WHERE project_key IS NOT NULL;

CREATE INDEX idx_events_week_occurred
  ON journal_events(week_id, occurred_at);

CREATE INDEX idx_events_item_occurred
  ON journal_events(item_id, occurred_at)
  WHERE item_id IS NOT NULL;

CREATE INDEX idx_promotion_week
  ON corpus_promotion_receipts(week_id, created_at);

PRAGMA optimize;
