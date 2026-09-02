PRAGMA foreign_keys = ON;

CREATE TABLE weeks (
  id TEXT PRIMARY KEY NOT NULL,
  starts_on TEXT NOT NULL,
  ends_on TEXT NOT NULL,
  timezone TEXT NOT NULL DEFAULT 'Asia/Seoul',
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
  revision INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  closed_at TEXT
);

CREATE TABLE items (
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
  UNIQUE (source_kind, source_key)
);

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

CREATE TABLE ingest_receipts (
  idempotency_key TEXT PRIMARY KEY NOT NULL,
  source_kind TEXT NOT NULL,
  source_key TEXT NOT NULL,
  source_version TEXT,
  item_id TEXT NOT NULL REFERENCES items(id) ON DELETE RESTRICT,
  created_at TEXT NOT NULL
);

CREATE TABLE week_closures (
  week_id TEXT PRIMARY KEY NOT NULL REFERENCES weeks(id) ON DELETE RESTRICT,
  summary_json TEXT NOT NULL,
  corpus_candidates_json TEXT NOT NULL,
  closed_by TEXT NOT NULL,
  closed_at TEXT NOT NULL
);

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
