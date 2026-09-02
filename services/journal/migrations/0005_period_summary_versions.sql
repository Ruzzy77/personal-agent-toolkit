CREATE TABLE period_summary_versions (
  id TEXT PRIMARY KEY NOT NULL,
  period_kind TEXT NOT NULL CHECK (
    period_kind IN ('day', 'week', 'month', 'quarter', 'year')
  ),
  anchor TEXT NOT NULL,
  starts_on TEXT NOT NULL,
  ends_on TEXT NOT NULL,
  body TEXT NOT NULL,
  version INTEGER NOT NULL,
  source_event_ids_json TEXT NOT NULL,
  created_by TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  UNIQUE (period_kind, anchor, version)
);

CREATE INDEX idx_period_summary_range
  ON period_summary_versions(period_kind, anchor, version);

PRAGMA optimize;
