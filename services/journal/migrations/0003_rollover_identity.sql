ALTER TABLE items ADD COLUMN logical_item_id TEXT;
UPDATE items SET logical_item_id = id WHERE logical_item_id IS NULL;

CREATE TRIGGER items_logical_item_required
BEFORE INSERT ON items
WHEN NEW.logical_item_id IS NULL OR length(trim(NEW.logical_item_id)) = 0
BEGIN
  SELECT RAISE(ABORT, 'logical_item_id is required');
END;

CREATE INDEX idx_items_logical_week
  ON items(logical_item_id, week_id);

CREATE TABLE journal_events_v3 (
  id TEXT PRIMARY KEY NOT NULL,
  week_id TEXT NOT NULL REFERENCES weeks(id) ON DELETE RESTRICT,
  item_id TEXT REFERENCES items(id) ON DELETE RESTRICT,
  event_type TEXT NOT NULL CHECK (
    event_type IN (
      'item_created',
      'item_rolled_over',
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

INSERT INTO journal_events_v3 (
  id, week_id, item_id, event_type, actor_kind, actor_ref,
  payload_json, idempotency_key, occurred_at, created_at
)
SELECT
  id, week_id, item_id, event_type, actor_kind, actor_ref,
  payload_json, idempotency_key, occurred_at, created_at
FROM journal_events;

DROP TABLE journal_events;
ALTER TABLE journal_events_v3 RENAME TO journal_events;

CREATE INDEX idx_events_week_occurred
  ON journal_events(week_id, occurred_at);

CREATE INDEX idx_events_item_occurred
  ON journal_events(item_id, occurred_at)
  WHERE item_id IS NOT NULL;

PRAGMA optimize;
