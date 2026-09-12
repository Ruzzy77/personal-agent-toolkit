ALTER TABLE documents ADD COLUMN trash_group_id TEXT;
ALTER TABLE documents ADD COLUMN write_nonce TEXT;
CREATE TABLE library_trash_groups (
 deletion_group_id TEXT PRIMARY KEY, issue_id TEXT NOT NULL, version INTEGER NOT NULL, state TEXT NOT NULL,
 trashed_at TEXT NOT NULL,purge_after TEXT NOT NULL,updated_at TEXT NOT NULL,blockers_json TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE library_operation_receipts(request_key TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,result_json TEXT NOT NULL);
CREATE TABLE library_assets(object_key TEXT PRIMARY KEY,state TEXT NOT NULL DEFAULT 'live',owned INTEGER NOT NULL DEFAULT 0,content_type TEXT,sha256 TEXT);
CREATE TABLE library_asset_uploads(upload_id TEXT PRIMARY KEY,object_key TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE library_asset_inventory(issue_id TEXT PRIMARY KEY,version INTEGER NOT NULL,complete INTEGER NOT NULL,
 FOREIGN KEY(issue_id) REFERENCES documents(id) ON DELETE CASCADE);
CREATE TABLE library_asset_refs(issue_id TEXT NOT NULL,object_key TEXT NOT NULL,PRIMARY KEY(issue_id,object_key),
 FOREIGN KEY(issue_id) REFERENCES documents(id) ON DELETE CASCADE);
CREATE INDEX library_asset_reverse ON library_asset_refs(object_key,issue_id);
CREATE TABLE library_trash_assets(deletion_group_id TEXT NOT NULL,object_key TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'pending',PRIMARY KEY(deletion_group_id,object_key));
CREATE TRIGGER library_current_write BEFORE UPDATE OF source_html,cover_path,references_json ON documents
BEGIN
 SELECT RAISE(ABORT,'lifecycle_conflict') WHERE OLD.trash_group_id IS NOT NULL;
 SELECT RAISE(ABORT,'writer_upgrade_required') WHERE NEW.write_nonce IS NULL OR NEW.write_nonce=OLD.write_nonce;
END;
CREATE TRIGGER library_new_write BEFORE INSERT ON documents
WHEN NEW.write_nonce IS NULL BEGIN SELECT RAISE(ABORT,'writer_upgrade_required'); END;
CREATE TRIGGER library_reference_guard BEFORE INSERT ON library_asset_refs
WHEN EXISTS(SELECT 1 FROM library_assets WHERE object_key=NEW.object_key AND state<>'live')
BEGIN SELECT RAISE(ABORT,'asset_deleting'); END;
CREATE TRIGGER library_inventory_invalidate AFTER UPDATE OF source_html,cover_path ON documents
BEGIN DELETE FROM library_asset_inventory WHERE issue_id=NEW.id; END;

CREATE TRIGGER library_incoming_reference_insert BEFORE INSERT ON documents
WHEN EXISTS(SELECT 1 FROM json_each(NEW.references_json) r JOIN documents d ON d.canonical_path=r.value
 JOIN library_trash_groups g ON g.deletion_group_id=d.trash_group_id WHERE g.state='purging')
BEGIN SELECT RAISE(ABORT,'lifecycle_conflict'); END;
CREATE TRIGGER library_incoming_reference_update BEFORE UPDATE OF references_json ON documents
WHEN EXISTS(SELECT 1 FROM json_each(NEW.references_json) r JOIN documents d ON d.canonical_path=r.value
 JOIN library_trash_groups g ON g.deletion_group_id=d.trash_group_id WHERE g.state='purging')
BEGIN SELECT RAISE(ABORT,'lifecycle_conflict'); END;

CREATE TABLE library_maintenance_runs(run_id TEXT PRIMARY KEY,started_at TEXT NOT NULL,state TEXT NOT NULL,result_json TEXT NOT NULL);
