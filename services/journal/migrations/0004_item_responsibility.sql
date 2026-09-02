ALTER TABLE items ADD COLUMN responsibility TEXT;

UPDATE items
SET responsibility = CASE
  WHEN lane = 'waiting' THEN 'counterparty'
  ELSE 'user'
END
WHERE responsibility IS NULL;

CREATE TRIGGER items_responsibility_required
BEFORE INSERT ON items
WHEN NEW.responsibility IS NULL
  OR NEW.responsibility NOT IN ('user', 'counterparty', 'system')
BEGIN
  SELECT RAISE(ABORT, 'valid responsibility is required');
END;

CREATE TRIGGER items_responsibility_update_required
BEFORE UPDATE OF responsibility ON items
WHEN NEW.responsibility IS NULL
  OR NEW.responsibility NOT IN ('user', 'counterparty', 'system')
BEGIN
  SELECT RAISE(ABORT, 'valid responsibility is required');
END;

CREATE INDEX idx_items_responsibility_week
  ON items(responsibility, week_id);

PRAGMA optimize;
