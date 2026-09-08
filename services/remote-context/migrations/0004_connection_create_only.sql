PRAGMA defer_foreign_keys = ON;
CREATE TABLE corpus_connections_saved AS SELECT * FROM corpus_connections;
CREATE TABLE corpus_current_files_saved AS SELECT * FROM corpus_current_files;
DROP TABLE corpus_current_files;
DROP TABLE corpus_connections;
CREATE TABLE corpus_connections (
  owner_id TEXT NOT NULL,
  space_id TEXT NOT NULL,
  connection_id TEXT NOT NULL,
  display_name TEXT NOT NULL,
  roles_json TEXT NOT NULL,
  access_scope TEXT NOT NULL CHECK (
    access_scope IN ('remote_allowed', 'local_only')
  ),
  permission TEXT NOT NULL CHECK (permission IN ('read_only', 'create_only', 'read_write')),
  index_mode TEXT NOT NULL CHECK (index_mode IN ('indexed', 'not_indexed')),
  corpus_id TEXT,
  device_id TEXT,
  local_connection_key TEXT,
  generation INTEGER NOT NULL DEFAULT 1,
  configuration_state TEXT NOT NULL DEFAULT 'ready',
  source_state TEXT,
  record_state TEXT,
  captured_at TEXT,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (owner_id, space_id, connection_id),
  FOREIGN KEY (owner_id, space_id)
    REFERENCES corpus_spaces(owner_id, space_id) ON DELETE CASCADE
);

CREATE INDEX idx_connections_corpus
  ON corpus_connections(owner_id, corpus_id);
CREATE INDEX idx_connections_device
  ON corpus_connections(owner_id, device_id);

CREATE TABLE corpus_current_files (
  owner_id TEXT NOT NULL,
  space_id TEXT NOT NULL,
  connection_id TEXT NOT NULL,
  relative_path TEXT NOT NULL,
  version_token TEXT,
  state TEXT NOT NULL,
  reason TEXT,
  residency_state TEXT,
  size INTEGER,
  modified_ns TEXT,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (owner_id, space_id, connection_id),
  FOREIGN KEY (owner_id, space_id, connection_id)
    REFERENCES corpus_connections(owner_id, space_id, connection_id)
    ON DELETE CASCADE
);


INSERT INTO corpus_connections SELECT * FROM corpus_connections_saved;
INSERT INTO corpus_current_files SELECT * FROM corpus_current_files_saved;
DROP TABLE corpus_current_files_saved;
DROP TABLE corpus_connections_saved;
