-- Tombstones outlive their Space; legacy publish/import must not resurrect authority.
CREATE TABLE corpus_registration_detachments (
  owner_id TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('connection','workspace')),
  registration_key TEXT NOT NULL,
  space_id TEXT NOT NULL,
  device_id TEXT NOT NULL,
  expected_version INTEGER NOT NULL,
  request_key TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  job_id TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('pending','detached','blocked')),
  result_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(owner_id,kind,registration_key),
  UNIQUE(owner_id,request_key), UNIQUE(owner_id,job_id)
);
CREATE TRIGGER corpus_no_retired_connection_insert BEFORE INSERT ON corpus_connections
WHEN EXISTS(SELECT 1 FROM corpus_registration_detachments d WHERE d.owner_id=NEW.owner_id AND d.kind='connection'
  AND d.registration_key=NEW.space_id||':'||NEW.connection_id)
BEGIN SELECT RAISE(ABORT,'registration_detached'); END;
CREATE TRIGGER corpus_no_retired_connection_publish BEFORE UPDATE ON corpus_connections
WHEN NEW.configuration_state='ready' AND EXISTS(SELECT 1 FROM corpus_registration_detachments d WHERE d.owner_id=NEW.owner_id
  AND d.kind='connection' AND d.registration_key=NEW.space_id||':'||NEW.connection_id)
BEGIN SELECT RAISE(ABORT,'registration_detached'); END;
CREATE TRIGGER corpus_no_retired_workspace_insert BEFORE INSERT ON corpus_workspace_bindings
WHEN EXISTS(SELECT 1 FROM corpus_registration_detachments d WHERE d.owner_id=NEW.owner_id AND d.kind='workspace'
  AND d.registration_key=NEW.host_id||':'||NEW.workspace_id)
BEGIN SELECT RAISE(ABORT,'registration_detached'); END;
CREATE TRIGGER corpus_no_retired_workspace_update BEFORE UPDATE ON corpus_workspace_bindings
WHEN EXISTS(SELECT 1 FROM corpus_registration_detachments d WHERE d.owner_id=NEW.owner_id AND d.kind='workspace'
  AND d.registration_key=NEW.host_id||':'||NEW.workspace_id)
BEGIN SELECT RAISE(ABORT,'registration_detached'); END;

CREATE TRIGGER corpus_retired_authority_guard BEFORE UPDATE OF roles_json,access_scope,permission,index_mode,corpus_id,device_id,local_connection_key,generation,space_id,connection_id ON corpus_connections
WHEN EXISTS(SELECT 1 FROM corpus_registration_detachments d WHERE d.owner_id=OLD.owner_id AND d.kind='connection' AND d.registration_key=OLD.space_id||':'||OLD.connection_id)
BEGIN SELECT RAISE(ABORT,'registration_detached'); END;
