ALTER TABLE design_recipes ADD COLUMN trash_group_id TEXT;
ALTER TABLE design_files ADD COLUMN trash_group_id TEXT;
CREATE TABLE design_assets(object_key TEXT PRIMARY KEY,state TEXT NOT NULL DEFAULT 'live',owned INTEGER NOT NULL DEFAULT 1,content_type TEXT NOT NULL,sha256 TEXT NOT NULL);
INSERT INTO design_assets(object_key,owned,content_type,sha256)
 SELECT object_key,CASE WHEN count(DISTINCT content_type)=1 AND count(DISTINCT sha256)=1 THEN 1 ELSE 0 END,min(content_type),min(sha256)
 FROM design_files GROUP BY object_key;
CREATE TABLE design_asset_uploads(upload_id TEXT PRIMARY KEY,object_key TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE design_trash_groups(deletion_group_id TEXT PRIMARY KEY,root_kind TEXT NOT NULL,recipe_id TEXT NOT NULL,path TEXT,version INTEGER NOT NULL,state TEXT NOT NULL,trashed_at TEXT NOT NULL,purge_after TEXT NOT NULL,updated_at TEXT NOT NULL,blockers_json TEXT NOT NULL DEFAULT '[]');
CREATE TABLE design_trash_members(deletion_group_id TEXT NOT NULL,kind TEXT NOT NULL,path TEXT NOT NULL,revision INTEGER NOT NULL,PRIMARY KEY(deletion_group_id,kind,path));
CREATE TABLE design_trash_assets(deletion_group_id TEXT NOT NULL,object_key TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'pending',PRIMARY KEY(deletion_group_id,object_key));
CREATE TABLE design_operation_receipts(request_key TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,result_json TEXT NOT NULL);
CREATE TRIGGER design_recipe_write_guard BEFORE UPDATE OF metadata_json ON design_recipes
WHEN OLD.trash_group_id IS NOT NULL BEGIN SELECT RAISE(ABORT,'lifecycle_conflict'); END;
CREATE TRIGGER design_file_insert_guard BEFORE INSERT ON design_files
WHEN EXISTS(SELECT 1 FROM design_recipes WHERE id=NEW.recipe_id AND trash_group_id IS NOT NULL)
 OR NOT EXISTS(SELECT 1 FROM design_assets WHERE object_key=NEW.object_key AND state='live')
BEGIN SELECT RAISE(ABORT,'lifecycle_conflict'); END;
CREATE TRIGGER design_file_update_guard BEFORE UPDATE OF object_key,content_type,sha256 ON design_files
WHEN OLD.trash_group_id IS NOT NULL OR EXISTS(SELECT 1 FROM design_recipes WHERE id=NEW.recipe_id AND trash_group_id IS NOT NULL)
 OR NOT EXISTS(SELECT 1 FROM design_assets WHERE object_key=NEW.object_key AND state='live')
BEGIN SELECT RAISE(ABORT,'lifecycle_conflict'); END;

CREATE TABLE design_maintenance_runs(run_id TEXT PRIMARY KEY,started_at TEXT NOT NULL,state TEXT NOT NULL,result_json TEXT NOT NULL);
