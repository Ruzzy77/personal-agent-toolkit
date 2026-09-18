-- One owner connection can serve every product, so the owner keeps a switch per
-- product instead of registering and authorizing each surface separately.
CREATE TABLE toolkit_product_state (
  owner_id TEXT NOT NULL,
  product TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (owner_id, product)
);
