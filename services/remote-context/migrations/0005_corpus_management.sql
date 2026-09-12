-- Additive identity/lifecycle metadata; no prior archived data enters the trash.
ALTER TABLE corpus_documents ADD COLUMN uid TEXT;
UPDATE corpus_documents SET uid='doc_' || lower(hex(randomblob(16)));
CREATE UNIQUE INDEX corpus_documents_uid ON corpus_documents(owner_id,uid);
CREATE TRIGGER corpus_document_uid_insert AFTER INSERT ON corpus_documents WHEN NEW.uid IS NULL
BEGIN
  UPDATE corpus_documents SET uid='doc_' || lower(hex(randomblob(16)))
    WHERE owner_id=NEW.owner_id AND space_id=NEW.space_id AND document_id=NEW.document_id;
END;

ALTER TABLE corpus_documents ADD COLUMN trash_group_id TEXT;
ALTER TABLE corpus_spaces ADD COLUMN trash_group_id TEXT;
ALTER TABLE corpus_context_items ADD COLUMN trash_group_id TEXT;
ALTER TABLE corpus_context_skills ADD COLUMN trash_group_id TEXT;
ALTER TABLE corpus_context_sources ADD COLUMN source_space_id TEXT;
ALTER TABLE corpus_context_sources ADD COLUMN source_connection_id TEXT;

UPDATE corpus_context_sources SET
  source_space_id=(SELECT space_id FROM corpus_context_items i
    WHERE i.owner_id=corpus_context_sources.owner_id AND i.item_id=corpus_context_sources.item_id);
-- Ambiguous old evidence stays unresolved; never choose an arbitrary origin.
UPDATE corpus_context_sources SET source_connection_id=(SELECT min(connection_id) FROM corpus_connections c
  WHERE c.owner_id=corpus_context_sources.owner_id AND c.space_id=corpus_context_sources.source_space_id
    AND c.corpus_id=corpus_context_sources.corpus_id AND c.index_mode='indexed'
    AND EXISTS (SELECT 1 FROM json_each(c.roles_json) WHERE value='source') HAVING count(*)=1);

-- Parent locators can move; snapshots retain their content and exact evidence.
CREATE TABLE corpus_document_snapshots_v2 (
  owner_id TEXT NOT NULL, space_id TEXT NOT NULL, document_id TEXT NOT NULL,
  snapshot TEXT NOT NULL CHECK (snapshot IN ('current','previous')),
  version INTEGER NOT NULL CHECK(version>=1), title TEXT NOT NULL,
  body_markdown TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('context','guidance')),
  applicability_json TEXT NOT NULL, guidance_approval_json TEXT, source_refs_json TEXT NOT NULL,
  migration_provenance_json TEXT, saved_at TEXT NOT NULL,
  PRIMARY KEY(owner_id,space_id,document_id,snapshot),
  FOREIGN KEY(owner_id,space_id,document_id) REFERENCES corpus_documents(owner_id,space_id,document_id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CHECK ((kind='guidance' AND guidance_approval_json IS NOT NULL) OR (kind='context' AND guidance_approval_json IS NULL))
);
INSERT INTO corpus_document_snapshots_v2
  SELECT owner_id,space_id,document_id,snapshot,version,title,body_markdown,kind,applicability_json,guidance_approval_json,
    COALESCE((SELECT json_group_array(json_set(value,'$.source_space_id',
      COALESCE(json_extract(value,'$.source_space_id'),s.space_id))) FROM json_each(s.source_refs_json)),'[]'),
    CASE WHEN json_extract(migration_provenance_json,'$.source_id') IS NOT NULL
      THEN json_set(migration_provenance_json,'$.source_space_id',COALESCE(json_extract(migration_provenance_json,'$.source_space_id'),space_id)) ELSE migration_provenance_json END,saved_at
  FROM corpus_document_snapshots s;
DROP TABLE corpus_document_snapshots;
ALTER TABLE corpus_document_snapshots_v2 RENAME TO corpus_document_snapshots;
CREATE INDEX idx_corpus_document_snapshots_list ON corpus_document_snapshots(owner_id,space_id,snapshot,kind,document_id);

CREATE TABLE corpus_document_aliases (
  owner_id TEXT NOT NULL, space_id TEXT NOT NULL, document_id TEXT NOT NULL, document_uid TEXT NOT NULL,
  created_at TEXT NOT NULL, PRIMARY KEY(owner_id,space_id,document_id),
  FOREIGN KEY(owner_id,document_uid) REFERENCES corpus_documents(owner_id,uid) ON DELETE CASCADE
);
CREATE TRIGGER corpus_document_alias_collision BEFORE INSERT ON corpus_documents
WHEN EXISTS (SELECT 1 FROM corpus_document_aliases a
  WHERE a.owner_id=NEW.owner_id AND a.space_id=NEW.space_id AND a.document_id=NEW.document_id)
BEGIN SELECT RAISE(ABORT,'document_alias_conflict'); END;

CREATE TABLE corpus_trash_groups (
  owner_id TEXT NOT NULL, deletion_group_id TEXT NOT NULL,
  root_kind TEXT NOT NULL CHECK(root_kind IN ('document','context_item','context_skill','space')),
  root_id TEXT NOT NULL, space_id TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('trashed','blocked','purging','purged','restored')),
  version INTEGER NOT NULL DEFAULT 1, trashed_at TEXT NOT NULL, purge_after TEXT NOT NULL,
  updated_at TEXT NOT NULL, blockers_json TEXT NOT NULL DEFAULT '[]',
  PRIMARY KEY(owner_id,deletion_group_id)
);
CREATE INDEX corpus_trash_due ON corpus_trash_groups(state,purge_after,owner_id,deletion_group_id);
CREATE TABLE corpus_trash_members (
  owner_id TEXT NOT NULL, deletion_group_id TEXT NOT NULL, kind TEXT NOT NULL,
  id TEXT NOT NULL, space_id TEXT NOT NULL, locator TEXT NOT NULL, version TEXT NOT NULL,
  previous_state TEXT NOT NULL DEFAULT 'active',
  PRIMARY KEY(owner_id,deletion_group_id,kind,id),
  FOREIGN KEY(owner_id,deletion_group_id) REFERENCES corpus_trash_groups(owner_id,deletion_group_id)
);
CREATE TABLE corpus_operation_receipts (
  owner_id TEXT NOT NULL, request_key TEXT NOT NULL, operation TEXT NOT NULL, fingerprint TEXT NOT NULL,
  result_json TEXT NOT NULL, committed_at TEXT NOT NULL, PRIMARY KEY(owner_id,request_key)
);
CREATE TABLE corpus_maintenance_runs (
  run_id TEXT PRIMARY KEY NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
  state TEXT NOT NULL, result_json TEXT NOT NULL DEFAULT '{}'
);
-- A lifecycle operation raises the compatibility floor for owner-wide imports.
CREATE TABLE corpus_management_owners (owner_id TEXT PRIMARY KEY NOT NULL);
CREATE TRIGGER corpus_managed_context_delete_guard BEFORE DELETE ON corpus_contexts
WHEN EXISTS (SELECT 1 FROM corpus_management_owners WHERE owner_id=OLD.owner_id)
 AND NOT EXISTS (SELECT 1 FROM corpus_trash_groups WHERE owner_id=OLD.owner_id AND space_id=OLD.space_id AND root_kind='space' AND state='purging')
BEGIN SELECT RAISE(ABORT,'management_canon_present'); END;

-- Only management requests need durable coordination receipts; normal edits do not.
CREATE TABLE corpus_operation_source_pins (
 owner_id TEXT NOT NULL,request_key TEXT NOT NULL,corpus_id TEXT NOT NULL,reservation_id TEXT NOT NULL,
 PRIMARY KEY(owner_id,request_key,corpus_id)
);
