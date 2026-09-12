CREATE TABLE sense_management_owners (owner_id TEXT PRIMARY KEY NOT NULL);
CREATE TABLE sense_trash_groups (
  owner_id TEXT NOT NULL, deletion_group_id TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('section','skill')), section_id TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('trashed','blocked','purged','restored')),
  version INTEGER NOT NULL DEFAULT 1, trashed_at TEXT NOT NULL, purge_after TEXT NOT NULL,
  payload_json TEXT NOT NULL, blockers_json TEXT NOT NULL DEFAULT '[]', updated_at TEXT NOT NULL,
  PRIMARY KEY(owner_id,deletion_group_id)
);
CREATE INDEX sense_trash_due ON sense_trash_groups(state,purge_after);
CREATE TABLE sense_operation_receipts (
  owner_id TEXT NOT NULL, request_key TEXT NOT NULL, fingerprint TEXT NOT NULL, result_json TEXT NOT NULL,
  PRIMARY KEY(owner_id,request_key)
);
