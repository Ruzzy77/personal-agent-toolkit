PRAGMA foreign_keys = ON;

CREATE TABLE sense_profiles (
  owner_id TEXT PRIMARY KEY NOT NULL,
  profile_json TEXT NOT NULL,
  profile_sha256 TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE sense_section_skills (
  owner_id TEXT NOT NULL,
  section_id TEXT NOT NULL,
  name TEXT NOT NULL,
  description TEXT NOT NULL,
  instructions TEXT NOT NULL,
  version TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (owner_id, section_id),
  FOREIGN KEY (owner_id) REFERENCES sense_profiles(owner_id) ON DELETE CASCADE
);

CREATE TABLE hypes_nodes (
  owner_id TEXT NOT NULL,
  node_id TEXT NOT NULL,
  labels_json TEXT NOT NULL,
  name TEXT NOT NULL,
  description TEXT NOT NULL,
  aliases_json TEXT NOT NULL,
  attributes_json TEXT NOT NULL,
  PRIMARY KEY (owner_id, node_id)
);

CREATE TABLE hypes_predicates (
  owner_id TEXT NOT NULL,
  predicate_id TEXT NOT NULL,
  name TEXT NOT NULL,
  description TEXT NOT NULL,
  aliases_json TEXT NOT NULL,
  PRIMARY KEY (owner_id, predicate_id)
);

CREATE TABLE hypes_edges (
  owner_id TEXT NOT NULL,
  edge_id TEXT NOT NULL,
  source_id TEXT NOT NULL,
  predicate_id TEXT NOT NULL,
  target_id TEXT NOT NULL,
  qualifiers_json TEXT NOT NULL,
  PRIMARY KEY (owner_id, edge_id),
  FOREIGN KEY (owner_id, source_id)
    REFERENCES hypes_nodes(owner_id, node_id) ON DELETE CASCADE,
  FOREIGN KEY (owner_id, predicate_id)
    REFERENCES hypes_predicates(owner_id, predicate_id) ON DELETE CASCADE,
  FOREIGN KEY (owner_id, target_id)
    REFERENCES hypes_nodes(owner_id, node_id) ON DELETE CASCADE
);

CREATE INDEX idx_hypes_edges_source
  ON hypes_edges(owner_id, source_id, edge_id);
CREATE INDEX idx_hypes_edges_target
  ON hypes_edges(owner_id, target_id, edge_id);
CREATE INDEX idx_hypes_edges_predicate
  ON hypes_edges(owner_id, predicate_id, edge_id);

CREATE VIRTUAL TABLE hypes_nodes_fts USING fts5(
  owner_id UNINDEXED,
  ref UNINDEXED,
  name,
  aliases,
  description,
  tokenize = 'unicode61'
);

CREATE VIRTUAL TABLE hypes_predicates_fts USING fts5(
  owner_id UNINDEXED,
  ref UNINDEXED,
  name,
  aliases,
  description,
  tokenize = 'unicode61'
);

CREATE TRIGGER hypes_nodes_fts_insert AFTER INSERT ON hypes_nodes BEGIN
  INSERT INTO hypes_nodes_fts(owner_id, ref, name, aliases, description)
  VALUES (new.owner_id, new.node_id, new.name, new.aliases_json, new.description);
END;

CREATE TRIGGER hypes_nodes_fts_update
AFTER UPDATE OF node_id, name, aliases_json, description ON hypes_nodes BEGIN
  DELETE FROM hypes_nodes_fts
  WHERE owner_id = old.owner_id AND ref = old.node_id;
  INSERT INTO hypes_nodes_fts(owner_id, ref, name, aliases, description)
  VALUES (new.owner_id, new.node_id, new.name, new.aliases_json, new.description);
END;

CREATE TRIGGER hypes_nodes_fts_delete AFTER DELETE ON hypes_nodes BEGIN
  DELETE FROM hypes_nodes_fts
  WHERE owner_id = old.owner_id AND ref = old.node_id;
END;

CREATE TRIGGER hypes_predicates_fts_insert AFTER INSERT ON hypes_predicates BEGIN
  INSERT INTO hypes_predicates_fts(owner_id, ref, name, aliases, description)
  VALUES (
    new.owner_id,
    new.predicate_id,
    new.name,
    new.aliases_json,
    new.description
  );
END;

CREATE TRIGGER hypes_predicates_fts_update
AFTER UPDATE OF predicate_id, name, aliases_json, description
ON hypes_predicates BEGIN
  DELETE FROM hypes_predicates_fts
  WHERE owner_id = old.owner_id AND ref = old.predicate_id;
  INSERT INTO hypes_predicates_fts(owner_id, ref, name, aliases, description)
  VALUES (
    new.owner_id,
    new.predicate_id,
    new.name,
    new.aliases_json,
    new.description
  );
END;

CREATE TRIGGER hypes_predicates_fts_delete AFTER DELETE ON hypes_predicates BEGIN
  DELETE FROM hypes_predicates_fts
  WHERE owner_id = old.owner_id AND ref = old.predicate_id;
END;

CREATE TABLE corpus_spaces (
  owner_id TEXT NOT NULL,
  space_id TEXT NOT NULL,
  display_name TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('active', 'archived')),
  access_scope TEXT NOT NULL CHECK (access_scope IN ('remote_allowed', 'local_only')),
  primary_work_connection_id TEXT,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (owner_id, space_id)
);

CREATE TABLE corpus_contexts (
  owner_id TEXT NOT NULL,
  space_id TEXT NOT NULL,
  title TEXT NOT NULL,
  purpose TEXT NOT NULL,
  scope_json TEXT NOT NULL,
  version INTEGER NOT NULL CHECK (version >= 1),
  updated_at TEXT NOT NULL,
  PRIMARY KEY (owner_id, space_id),
  FOREIGN KEY (owner_id, space_id)
    REFERENCES corpus_spaces(owner_id, space_id) ON DELETE CASCADE
);

CREATE TABLE corpus_context_items (
  owner_id TEXT NOT NULL,
  space_id TEXT NOT NULL,
  item_id TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (
    kind IN ('finding', 'relationship', 'difference', 'question', 'gap')
  ),
  body_text TEXT NOT NULL,
  attributes_json TEXT NOT NULL,
  disclosure_state TEXT NOT NULL DEFAULT 'restricted',
  lifecycle_state TEXT NOT NULL DEFAULT 'active',
  supersedes_item_id TEXT,
  created_at TEXT NOT NULL,
  PRIMARY KEY (owner_id, item_id),
  FOREIGN KEY (owner_id, space_id)
    REFERENCES corpus_contexts(owner_id, space_id) ON DELETE CASCADE
);

CREATE INDEX idx_context_items_space
  ON corpus_context_items(owner_id, space_id, created_at, item_id);

CREATE TABLE corpus_context_sources (
  owner_id TEXT NOT NULL,
  source_ref_id TEXT NOT NULL,
  item_id TEXT NOT NULL,
  corpus_id TEXT,
  snapshot_id TEXT,
  document_id TEXT,
  revision_id TEXT,
  projection_id TEXT,
  source_unit_id TEXT,
  provider_kind TEXT,
  provider_record_id TEXT,
  link_role TEXT NOT NULL,
  source_span_json TEXT NOT NULL,
  PRIMARY KEY (owner_id, source_ref_id),
  FOREIGN KEY (owner_id, item_id)
    REFERENCES corpus_context_items(owner_id, item_id) ON DELETE CASCADE
);

CREATE INDEX idx_context_sources_item
  ON corpus_context_sources(owner_id, item_id, source_ref_id);

CREATE TABLE corpus_context_skills (
  owner_id TEXT NOT NULL,
  space_id TEXT NOT NULL,
  name TEXT NOT NULL,
  description TEXT NOT NULL,
  instructions TEXT NOT NULL,
  version TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (owner_id, space_id),
  FOREIGN KEY (owner_id, space_id)
    REFERENCES corpus_contexts(owner_id, space_id) ON DELETE CASCADE
);

CREATE TABLE corpus_connections (
  owner_id TEXT NOT NULL,
  space_id TEXT NOT NULL,
  connection_id TEXT NOT NULL,
  display_name TEXT NOT NULL,
  roles_json TEXT NOT NULL,
  access_scope TEXT NOT NULL CHECK (
    access_scope IN ('remote_allowed', 'local_only')
  ),
  permission TEXT NOT NULL CHECK (permission IN ('read_only', 'read_write')),
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

CREATE TABLE sync_devices (
  owner_id TEXT NOT NULL,
  device_id TEXT NOT NULL,
  display_name TEXT NOT NULL,
  credential_id TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('active', 'revoked')),
  capabilities_json TEXT NOT NULL,
  last_seen_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (owner_id, device_id),
  UNIQUE (credential_id)
);

CREATE TABLE sync_jobs (
  owner_id TEXT NOT NULL,
  job_id TEXT NOT NULL,
  device_id TEXT NOT NULL,
  operation TEXT NOT NULL,
  scope_json TEXT NOT NULL,
  request_json TEXT NOT NULL,
  response_json TEXT,
  idempotency_key TEXT NOT NULL,
  state TEXT NOT NULL CHECK (
    state IN ('queued', 'dispatched', 'succeeded', 'failed', 'expired', 'canceled')
  ),
  maximum_response_bytes INTEGER NOT NULL,
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  completed_at TEXT,
  PRIMARY KEY (owner_id, job_id),
  UNIQUE (owner_id, idempotency_key),
  FOREIGN KEY (owner_id, device_id)
    REFERENCES sync_devices(owner_id, device_id) ON DELETE RESTRICT
);

CREATE INDEX idx_sync_jobs_device_state
  ON sync_jobs(owner_id, device_id, state, created_at);

CREATE TABLE sync_job_events (
  owner_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  job_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  details_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (owner_id, event_id),
  FOREIGN KEY (owner_id, job_id)
    REFERENCES sync_jobs(owner_id, job_id) ON DELETE CASCADE
);

CREATE TABLE migration_receipts (
  owner_id TEXT NOT NULL,
  product TEXT NOT NULL,
  source_digest TEXT NOT NULL,
  source_schema_version INTEGER NOT NULL,
  counts_json TEXT NOT NULL,
  imported_at TEXT NOT NULL,
  PRIMARY KEY (owner_id, product, source_digest)
);

PRAGMA optimize;

