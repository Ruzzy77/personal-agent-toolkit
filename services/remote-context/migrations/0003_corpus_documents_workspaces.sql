CREATE TABLE corpus_documents (
  owner_id TEXT NOT NULL,
  space_id TEXT NOT NULL,
  document_id TEXT NOT NULL,
  version INTEGER NOT NULL CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (owner_id, space_id, document_id),
  FOREIGN KEY (owner_id, space_id)
    REFERENCES corpus_contexts(owner_id, space_id) ON DELETE CASCADE
);

-- A document retains its current value and exactly one previous value, not an
-- unbounded revision archive. Both snapshots retain their exact Source links.
CREATE TABLE corpus_document_snapshots (
  owner_id TEXT NOT NULL,
  space_id TEXT NOT NULL,
  document_id TEXT NOT NULL,
  snapshot TEXT NOT NULL CHECK (snapshot IN ('current', 'previous')),
  version INTEGER NOT NULL CHECK (version >= 1),
  title TEXT NOT NULL,
  body_markdown TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('context', 'guidance')),
  applicability_json TEXT NOT NULL,
  guidance_approval_json TEXT,
  source_refs_json TEXT NOT NULL,
  migration_provenance_json TEXT,
  saved_at TEXT NOT NULL,
  PRIMARY KEY (owner_id, space_id, document_id, snapshot),
  FOREIGN KEY (owner_id, space_id, document_id)
    REFERENCES corpus_documents(owner_id, space_id, document_id) ON DELETE CASCADE,
  CHECK ((kind = 'guidance' AND guidance_approval_json IS NOT NULL)
    OR (kind = 'context' AND guidance_approval_json IS NULL))
);

CREATE INDEX idx_corpus_document_snapshots_list
  ON corpus_document_snapshots(owner_id, space_id, snapshot, kind, document_id);

CREATE TABLE corpus_workspace_bindings (
  owner_id TEXT NOT NULL,
  host_id TEXT NOT NULL,
  workspace_id TEXT NOT NULL,
  space_id TEXT NOT NULL,
  environment_kind TEXT NOT NULL CHECK (environment_kind IN ('local', 'remote')),
  project_id TEXT,
  version INTEGER NOT NULL CHECK (version >= 1),
  updated_at TEXT NOT NULL,
  PRIMARY KEY (owner_id, host_id, workspace_id),
  FOREIGN KEY (owner_id, space_id)
    REFERENCES corpus_contexts(owner_id, space_id) ON DELETE CASCADE
);

-- The legacy owner-wide import deletes Contexts before recreating them. Protect
-- native canon inside that same transaction, including a concurrent create
-- between an import preflight and its batch. An explicit native lifecycle API
-- must deal with these records before it can remove their parent Context.
CREATE TRIGGER corpus_native_context_delete_guard
BEFORE DELETE ON corpus_contexts
WHEN EXISTS (SELECT 1 FROM corpus_documents
  WHERE owner_id=OLD.owner_id AND space_id=OLD.space_id)
  OR EXISTS (SELECT 1 FROM corpus_workspace_bindings
  WHERE owner_id=OLD.owner_id AND space_id=OLD.space_id)
BEGIN
  SELECT RAISE(ABORT, 'native_canon_present');
END;

CREATE TRIGGER corpus_native_space_delete_guard
BEFORE DELETE ON corpus_spaces
WHEN EXISTS (SELECT 1 FROM corpus_documents
  WHERE owner_id=OLD.owner_id AND space_id=OLD.space_id)
  OR EXISTS (SELECT 1 FROM corpus_workspace_bindings
  WHERE owner_id=OLD.owner_id AND space_id=OLD.space_id)
BEGIN
  SELECT RAISE(ABORT, 'native_canon_present');
END;
