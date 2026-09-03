CREATE TABLE documents (
  id TEXT PRIMARY KEY NOT NULL,
  collection TEXT NOT NULL CHECK (collection IN ('daily', 'digest', 'research')),
  date TEXT NOT NULL,
  published_at TEXT NOT NULL,
  title TEXT NOT NULL,
  references_json TEXT NOT NULL DEFAULT '[]',
  canonical_path TEXT NOT NULL UNIQUE,
  text_content TEXT NOT NULL,
  source_html TEXT NOT NULL,
  cover_path TEXT,
  version INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL
);

CREATE INDEX documents_published_at_idx
  ON documents(published_at DESC);

CREATE INDEX documents_collection_published_at_idx
  ON documents(collection, published_at DESC);

PRAGMA optimize;
