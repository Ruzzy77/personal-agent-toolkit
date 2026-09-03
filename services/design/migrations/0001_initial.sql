CREATE TABLE design_library (
  id TEXT PRIMARY KEY NOT NULL,
  metadata_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE design_patterns (
  id TEXT PRIMARY KEY NOT NULL,
  pattern_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE design_recipes (
  id TEXT PRIMARY KEY NOT NULL,
  name TEXT NOT NULL,
  description TEXT NOT NULL,
  recipe_version TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('draft', 'candidate', 'validated', 'deprecated')),
  selection_ready INTEGER NOT NULL DEFAULT 0 CHECK (selection_ready IN (0, 1)),
  metadata_json TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE design_files (
  recipe_id TEXT NOT NULL,
  path TEXT NOT NULL,
  object_key TEXT NOT NULL UNIQUE,
  content_type TEXT NOT NULL,
  byte_size INTEGER NOT NULL,
  sha256 TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (recipe_id, path),
  FOREIGN KEY (recipe_id) REFERENCES design_recipes(id) ON DELETE CASCADE
);

CREATE INDEX idx_design_recipes_status_updated
  ON design_recipes(status, updated_at DESC);

PRAGMA optimize;
