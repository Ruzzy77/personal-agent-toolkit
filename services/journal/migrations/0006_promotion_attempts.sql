-- Check completed candidates before rebuilding; never choose a winner or delete history.
CREATE UNIQUE INDEX IF NOT EXISTS idx_promotion_completed_item
  ON corpus_promotion_receipts(week_id, item_id, target_space, content_hash)
  WHERE item_id IS NOT NULL AND status IN ('applied', 'skipped');

CREATE UNIQUE INDEX IF NOT EXISTS idx_promotion_completed_week
  ON corpus_promotion_receipts(week_id, target_space, content_hash)
  WHERE item_id IS NULL AND status IN ('applied', 'skipped');

CREATE TABLE corpus_promotion_receipts_v6 (
  id TEXT PRIMARY KEY NOT NULL,
  week_id TEXT NOT NULL REFERENCES weeks(id) ON DELETE RESTRICT,
  item_id TEXT REFERENCES items(id) ON DELETE RESTRICT,
  target_space TEXT NOT NULL,
  source_path TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('applied', 'skipped', 'failed')),
  details TEXT,
  created_at TEXT NOT NULL
);

INSERT INTO corpus_promotion_receipts_v6 (
  id, week_id, item_id, target_space, source_path, content_hash,
  status, details, created_at
)
SELECT
  id, week_id, item_id, target_space, source_path, content_hash,
  status, details, created_at
FROM corpus_promotion_receipts;

DROP TABLE corpus_promotion_receipts;
ALTER TABLE corpus_promotion_receipts_v6 RENAME TO corpus_promotion_receipts;

CREATE INDEX idx_promotion_week
  ON corpus_promotion_receipts(week_id, created_at);

CREATE UNIQUE INDEX idx_promotion_completed_item
  ON corpus_promotion_receipts(week_id, item_id, target_space, content_hash)
  WHERE item_id IS NOT NULL AND status IN ('applied', 'skipped');

CREATE UNIQUE INDEX idx_promotion_completed_week
  ON corpus_promotion_receipts(week_id, target_space, content_hash)
  WHERE item_id IS NULL AND status IN ('applied', 'skipped');

PRAGMA optimize;
