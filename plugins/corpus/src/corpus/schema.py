"""SQLite schemas for the catalog and per-corpus source fabric."""

CATALOG_SCHEMA_VERSION = 1
CORPUS_SCHEMA_VERSION = 4
EXTRACTION_SCHEMA_VERSION = 4
CONTEXT_SCHEMA_VERSION = 4

PROVENANCE_GUARD_SCHEMA = """
CREATE TRIGGER IF NOT EXISTS guard_documents_current_revision_insert
BEFORE INSERT ON documents
WHEN NEW.current_revision_id IS NOT NULL
 AND NOT EXISTS (
    SELECT 1 FROM revisions
    WHERE revision_id = NEW.current_revision_id
      AND document_id = NEW.document_id
 )
BEGIN
    SELECT RAISE(ABORT, 'current revision does not belong to document');
END;

CREATE TRIGGER IF NOT EXISTS guard_documents_current_revision_update
BEFORE UPDATE OF current_revision_id, document_id ON documents
WHEN NEW.current_revision_id IS NOT NULL
 AND NOT EXISTS (
    SELECT 1 FROM revisions
    WHERE revision_id = NEW.current_revision_id
      AND document_id = NEW.document_id
 )
BEGIN
    SELECT RAISE(ABORT, 'current revision does not belong to document');
END;

CREATE TRIGGER IF NOT EXISTS guard_revisions_predecessor_insert
BEFORE INSERT ON revisions
WHEN NEW.predecessor_revision_id IS NOT NULL
 AND NOT EXISTS (
    SELECT 1 FROM revisions
    WHERE revision_id = NEW.predecessor_revision_id
      AND document_id = NEW.document_id
 )
BEGIN
    SELECT RAISE(ABORT, 'predecessor revision does not belong to document');
END;

CREATE TRIGGER IF NOT EXISTS guard_revisions_predecessor_update
BEFORE UPDATE OF predecessor_revision_id, document_id ON revisions
WHEN NEW.predecessor_revision_id IS NOT NULL
 AND NOT EXISTS (
    SELECT 1 FROM revisions
    WHERE revision_id = NEW.predecessor_revision_id
      AND document_id = NEW.document_id
 )
BEGIN
    SELECT RAISE(ABORT, 'predecessor revision does not belong to document');
END;

CREATE TRIGGER IF NOT EXISTS guard_revisions_document_ownership_update
BEFORE UPDATE OF document_id ON revisions
WHEN NEW.document_id IS NOT OLD.document_id
BEGIN
    SELECT RAISE(ABORT, 'revision document ownership is immutable');
END;
"""

CATALOG_SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS schema_info (
    version INTEGER NOT NULL
);
INSERT INTO schema_info(version)
SELECT 1
WHERE NOT EXISTS (SELECT 1 FROM schema_info);

CREATE TABLE IF NOT EXISTS corpora (
    corpus_id TEXT PRIMARY KEY,
    source_root TEXT NOT NULL UNIQUE,
    source_root_nfc TEXT NOT NULL,
    execution_policy TEXT NOT NULL
        CHECK (execution_policy IN ('local_only', 'external_host_allowed')),
    provider_kind TEXT NOT NULL,
    source_scope_json TEXT NOT NULL
        DEFAULT '{"exclude_directory_names":[],"exclude_path_prefixes":[]}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

CONTEXT_SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS schema_info (
    version INTEGER NOT NULL
);
INSERT INTO schema_info(version)
SELECT 4
WHERE NOT EXISTS (SELECT 1 FROM schema_info);

CREATE TABLE IF NOT EXISTS contexts (
    context_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    purpose TEXT NOT NULL,
    scope_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('active', 'archived')),
    version INTEGER NOT NULL CHECK (version >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS context_corpora (
    context_id TEXT NOT NULL,
    corpus_id TEXT NOT NULL,
    last_checked_scan_id TEXT,
    last_checked_snapshot_id TEXT,
    last_checked_inventory_hash TEXT,
    last_checked_at TEXT,
    PRIMARY KEY(context_id, corpus_id),
    FOREIGN KEY(context_id) REFERENCES contexts(context_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS context_items (
    item_id TEXT PRIMARY KEY,
    context_id TEXT NOT NULL,
    client_ref TEXT NOT NULL,
    input_sha256 TEXT NOT NULL,
    kind TEXT NOT NULL
        CHECK (kind IN ('finding', 'relationship', 'difference', 'question', 'gap')),
    body_text TEXT NOT NULL,
    attributes_json TEXT NOT NULL,
    disclosure_state TEXT NOT NULL DEFAULT 'restricted'
        CHECK (disclosure_state IN ('restricted', 'general_candidate')),
    lifecycle_state TEXT NOT NULL
        CHECK (lifecycle_state IN ('active', 'superseded')),
    supersedes_item_id TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(context_id, client_ref),
    FOREIGN KEY(context_id) REFERENCES contexts(context_id) ON DELETE CASCADE,
    FOREIGN KEY(supersedes_item_id) REFERENCES context_items(item_id)
);

CREATE INDEX IF NOT EXISTS idx_context_items_page
    ON context_items(context_id, lifecycle_state, created_at, item_id);
CREATE INDEX IF NOT EXISTS idx_context_items_supersedes
    ON context_items(supersedes_item_id);

CREATE TABLE IF NOT EXISTS context_sources (
    source_ref_id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL,
    corpus_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    revision_id TEXT NOT NULL,
    projection_id TEXT NOT NULL,
    source_unit_id TEXT NOT NULL,
    link_role TEXT NOT NULL CHECK (link_role IN ('direct', 'context', 'contrast')),
    source_span_json TEXT NOT NULL,
    UNIQUE(item_id, corpus_id, source_unit_id),
    FOREIGN KEY(item_id) REFERENCES context_items(item_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_context_sources_item ON context_sources(item_id);
CREATE INDEX IF NOT EXISTS idx_context_sources_document
    ON context_sources(corpus_id, document_id);

CREATE TABLE IF NOT EXISTS corpus_source_bindings (
    binding_id TEXT PRIMARY KEY,
    corpus_id TEXT NOT NULL,
    provider_kind TEXT NOT NULL,
    selector_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('active', 'archived')),
    last_complete_run_id TEXT,
    last_complete_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(corpus_id, provider_kind, selector_json)
);

CREATE INDEX IF NOT EXISTS idx_corpus_source_bindings_corpus
    ON corpus_source_bindings(corpus_id, state, binding_id);

CREATE TABLE IF NOT EXISTS external_source_runs (
    run_id TEXT PRIMARY KEY,
    binding_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('incomplete', 'complete')),
    started_at TEXT NOT NULL,
    completed_at TEXT,
    FOREIGN KEY(binding_id)
        REFERENCES corpus_source_bindings(binding_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_external_source_runs_binding
    ON external_source_runs(binding_id, started_at);

CREATE TABLE IF NOT EXISTS external_source_records (
    source_record_id TEXT PRIMARY KEY,
    binding_id TEXT NOT NULL,
    external_id TEXT NOT NULL,
    parent_external_id TEXT,
    occurred_at TEXT,
    title TEXT,
    participants_json TEXT NOT NULL,
    label_ids_json TEXT NOT NULL,
    attachments_json TEXT NOT NULL,
    provider_metadata_json TEXT NOT NULL DEFAULT '{}',
    locator_json TEXT NOT NULL DEFAULT '{}',
    freshness_identity TEXT,
    metadata_sha256 TEXT NOT NULL,
    membership_state TEXT NOT NULL CHECK (membership_state IN ('active', 'removed')),
    last_seen_run_id TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    UNIQUE(binding_id, external_id),
    FOREIGN KEY(binding_id)
        REFERENCES corpus_source_bindings(binding_id) ON DELETE CASCADE,
    FOREIGN KEY(last_seen_run_id)
        REFERENCES external_source_runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_external_source_records_binding
    ON external_source_records(binding_id, membership_state, occurred_at, external_id);
CREATE INDEX IF NOT EXISTS idx_external_source_records_parent
    ON external_source_records(binding_id, parent_external_id);

CREATE TABLE IF NOT EXISTS context_external_sources (
    source_ref_id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL,
    corpus_id TEXT NOT NULL,
    binding_id TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    link_role TEXT NOT NULL CHECK (link_role IN ('direct', 'context', 'contrast')),
    observed_metadata_sha256 TEXT NOT NULL,
    UNIQUE(item_id, source_record_id),
    FOREIGN KEY(item_id) REFERENCES context_items(item_id) ON DELETE CASCADE,
    FOREIGN KEY(binding_id)
        REFERENCES corpus_source_bindings(binding_id),
    FOREIGN KEY(source_record_id)
        REFERENCES external_source_records(source_record_id)
);

CREATE INDEX IF NOT EXISTS idx_context_external_sources_item
    ON context_external_sources(item_id);
CREATE INDEX IF NOT EXISTS idx_context_external_sources_record
    ON context_external_sources(source_record_id, item_id);

CREATE TABLE IF NOT EXISTS context_release_manifests (
    release_id TEXT PRIMARY KEY,
    context_id TEXT NOT NULL,
    public_collection_id TEXT NOT NULL,
    input_sha256 TEXT NOT NULL,
    release_number INTEGER NOT NULL CHECK (release_number >= 1),
    public_title TEXT NOT NULL,
    public_purpose TEXT NOT NULL,
    review_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('active', 'superseded')),
    supersedes_release_id TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(context_id) REFERENCES contexts(context_id) ON DELETE CASCADE,
    FOREIGN KEY(supersedes_release_id)
        REFERENCES context_release_manifests(release_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_context_release_active_context
    ON context_release_manifests(context_id)
    WHERE state = 'active';
CREATE UNIQUE INDEX IF NOT EXISTS idx_context_release_active_public_collection
    ON context_release_manifests(public_collection_id)
    WHERE state = 'active';
CREATE INDEX IF NOT EXISTS idx_context_release_history
    ON context_release_manifests(context_id, release_number);

CREATE TABLE IF NOT EXISTS context_release_items (
    release_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    public_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK (position >= 0),
    PRIMARY KEY(release_id, item_id),
    UNIQUE(release_id, public_id),
    UNIQUE(release_id, position),
    FOREIGN KEY(release_id)
        REFERENCES context_release_manifests(release_id) ON DELETE CASCADE,
    FOREIGN KEY(item_id) REFERENCES context_items(item_id)
);

CREATE INDEX IF NOT EXISTS idx_context_release_items_item
    ON context_release_items(item_id, release_id);
"""

CORPUS_SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS schema_info (
    version INTEGER NOT NULL
);
INSERT INTO schema_info(version)
SELECT 4
WHERE NOT EXISTS (SELECT 1 FROM schema_info);

CREATE TABLE IF NOT EXISTS scan_runs (
    scan_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    root_entry_count INTEGER NOT NULL DEFAULT 0,
    directory_count INTEGER NOT NULL DEFAULT 0,
    file_count INTEGER NOT NULL DEFAULT 0,
    dataless_count INTEGER NOT NULL DEFAULT 0,
    logical_bytes INTEGER NOT NULL DEFAULT 0,
    allocated_bytes INTEGER NOT NULL DEFAULT 0,
    issue_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    document_id TEXT PRIMARY KEY,
    relative_path TEXT NOT NULL UNIQUE,
    relative_path_nfc TEXT NOT NULL,
    absolute_path TEXT NOT NULL,
    extension TEXT NOT NULL,
    media_type TEXT,
    adapter TEXT,
    logical_size INTEGER NOT NULL,
    allocated_size INTEGER NOT NULL,
    modified_ns INTEGER NOT NULL,
    changed_ns INTEGER NOT NULL,
    device INTEGER NOT NULL,
    inode INTEGER NOT NULL,
    mode INTEGER NOT NULL,
    flags INTEGER NOT NULL,
    is_dataless INTEGER NOT NULL CHECK (is_dataless IN (0, 1)),
    residency_state TEXT NOT NULL
        CHECK (residency_state IN ('resident', 'remote_only', 'unknown')),
    eligibility_state TEXT NOT NULL
        CHECK (eligibility_state IN ('supported', 'unsupported', 'ignored')),
    current_revision_id TEXT,
    last_seen_scan_id TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    deleted_at TEXT,
    FOREIGN KEY(last_seen_scan_id) REFERENCES scan_runs(scan_id),
    FOREIGN KEY(current_revision_id) REFERENCES revisions(revision_id),
    FOREIGN KEY(current_revision_id, document_id)
        REFERENCES revisions(revision_id, document_id)
);

CREATE INDEX IF NOT EXISTS idx_documents_scan ON documents(last_seen_scan_id);
CREATE INDEX IF NOT EXISTS idx_documents_ingest
    ON documents(eligibility_state, residency_state, logical_size);
CREATE INDEX IF NOT EXISTS idx_documents_path_nfc ON documents(relative_path_nfc);

CREATE TABLE IF NOT EXISTS revisions (
    revision_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    immutable_blob_ref TEXT NOT NULL,
    source_size INTEGER NOT NULL,
    source_modified_ns INTEGER NOT NULL,
    source_changed_ns INTEGER NOT NULL,
    source_device INTEGER NOT NULL,
    source_inode INTEGER NOT NULL,
    observed_at TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    extraction_state TEXT NOT NULL,
    extractor_version TEXT NOT NULL,
    predecessor_revision_id TEXT,
    UNIQUE(document_id, sha256),
    FOREIGN KEY(document_id) REFERENCES documents(document_id),
    FOREIGN KEY(predecessor_revision_id) REFERENCES revisions(revision_id),
    FOREIGN KEY(predecessor_revision_id, document_id)
        REFERENCES revisions(revision_id, document_id)
);

CREATE INDEX IF NOT EXISTS idx_revisions_document ON revisions(document_id, captured_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_revisions_identity
    ON revisions(revision_id, document_id);

CREATE TABLE IF NOT EXISTS extraction_projections (
    projection_id TEXT PRIMARY KEY,
    revision_id TEXT NOT NULL,
    adapter_id TEXT NOT NULL,
    adapter_version TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    result_manifest_hash TEXT NOT NULL,
    completeness_state TEXT NOT NULL
        CHECK (completeness_state IN ('complete', 'partial')),
    capability_manifest_json TEXT NOT NULL,
    assurance_state TEXT NOT NULL
        CHECK (assurance_state IN ('declared', 'legacy_unverified')),
    is_active INTEGER NOT NULL DEFAULT 0 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL,
    FOREIGN KEY(revision_id) REFERENCES revisions(revision_id)
);

CREATE INDEX IF NOT EXISTS idx_projections_revision
    ON extraction_projections(revision_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_projections_active_revision
    ON extraction_projections(revision_id) WHERE is_active = 1;
CREATE UNIQUE INDEX IF NOT EXISTS idx_projections_identity
    ON extraction_projections(projection_id, revision_id);

CREATE TABLE IF NOT EXISTS extraction_attempts (
    attempt_id TEXT PRIMARY KEY,
    revision_id TEXT NOT NULL,
    adapter_id TEXT NOT NULL,
    adapter_version TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('running', 'succeeded', 'failed')),
    projection_id TEXT,
    error_json TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    CHECK (
        (state = 'running' AND projection_id IS NULL
            AND error_json IS NULL AND completed_at IS NULL)
        OR (state = 'succeeded' AND projection_id IS NOT NULL
            AND error_json IS NULL AND completed_at IS NOT NULL)
        OR (state = 'failed' AND projection_id IS NULL
            AND error_json IS NOT NULL AND completed_at IS NOT NULL)
    ),
    FOREIGN KEY(revision_id) REFERENCES revisions(revision_id),
    FOREIGN KEY(projection_id) REFERENCES extraction_projections(projection_id),
    FOREIGN KEY(projection_id, revision_id)
        REFERENCES extraction_projections(projection_id, revision_id)
);

CREATE INDEX IF NOT EXISTS idx_attempts_revision
    ON extraction_attempts(revision_id, started_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_attempts_identity
    ON extraction_attempts(attempt_id, revision_id);

CREATE TABLE IF NOT EXISTS source_units (
    unit_id TEXT PRIMARY KEY,
    revision_id TEXT NOT NULL,
    projection_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    unit_type TEXT NOT NULL,
    structure_path_json TEXT NOT NULL,
    source_anchor_json TEXT NOT NULL,
    normalized_content TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    previous_unit_id TEXT,
    next_unit_id TEXT,
    extraction_issues_json TEXT NOT NULL,
    derivation_method TEXT NOT NULL DEFAULT 'native_text',
    geometry_json TEXT NOT NULL DEFAULT '{}',
    confidence REAL,
    quality_flags_json TEXT NOT NULL DEFAULT '[]',
    trust_lineage TEXT NOT NULL DEFAULT 'untrusted_source_derived',
    UNIQUE(projection_id, ordinal),
    FOREIGN KEY(revision_id) REFERENCES revisions(revision_id),
    FOREIGN KEY(projection_id) REFERENCES extraction_projections(projection_id),
    FOREIGN KEY(projection_id, revision_id)
        REFERENCES extraction_projections(projection_id, revision_id)
);

CREATE INDEX IF NOT EXISTS idx_units_revision ON source_units(revision_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_units_projection ON source_units(projection_id, ordinal);
CREATE UNIQUE INDEX IF NOT EXISTS idx_units_identity
    ON source_units(unit_id, revision_id);

CREATE VIRTUAL TABLE IF NOT EXISTS source_units_fts USING fts5(
    unit_id UNINDEXED,
    document_id UNINDEXED,
    relative_path,
    structure_path,
    normalized_content,
    tokenize = 'unicode61 remove_diacritics 2'
);

CREATE TABLE IF NOT EXISTS extraction_issues (
    issue_id TEXT PRIMARY KEY,
    document_id TEXT,
    revision_id TEXT,
    attempt_id TEXT,
    projection_id TEXT,
    scan_id TEXT,
    stage TEXT NOT NULL,
    severity TEXT NOT NULL,
    code TEXT NOT NULL,
    message TEXT NOT NULL,
    details_json TEXT NOT NULL,
    structural_locator_json TEXT NOT NULL DEFAULT '{}',
    locator_key TEXT NOT NULL DEFAULT '',
    lifecycle_state TEXT NOT NULL DEFAULT 'active'
        CHECK (lifecycle_state IN ('active', 'resolved', 'superseded', 'legacy_unverified')),
    created_at TEXT NOT NULL,
    CHECK (revision_id IS NULL OR document_id IS NOT NULL),
    CHECK (attempt_id IS NULL OR revision_id IS NOT NULL),
    CHECK (projection_id IS NULL OR revision_id IS NOT NULL),
    FOREIGN KEY(document_id) REFERENCES documents(document_id),
    FOREIGN KEY(revision_id) REFERENCES revisions(revision_id),
    FOREIGN KEY(attempt_id) REFERENCES extraction_attempts(attempt_id),
    FOREIGN KEY(projection_id) REFERENCES extraction_projections(projection_id),
    FOREIGN KEY(revision_id, document_id)
        REFERENCES revisions(revision_id, document_id),
    FOREIGN KEY(attempt_id, revision_id)
        REFERENCES extraction_attempts(attempt_id, revision_id),
    FOREIGN KEY(projection_id, revision_id)
        REFERENCES extraction_projections(projection_id, revision_id),
    FOREIGN KEY(scan_id) REFERENCES scan_runs(scan_id)
);

CREATE INDEX IF NOT EXISTS idx_issues_document ON extraction_issues(document_id, created_at);
CREATE INDEX IF NOT EXISTS idx_issues_projection
    ON extraction_issues(projection_id, lifecycle_state, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_active_projection_issue
    ON extraction_issues(projection_id, stage, code, locator_key)
    WHERE projection_id IS NOT NULL AND lifecycle_state = 'active';

CREATE TABLE IF NOT EXISTS interpretation_queue (
    queue_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    revision_id TEXT NOT NULL,
    projection_id TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL
        CHECK (state IN ('pending', 'in_progress', 'complete', 'stale', 'failed')),
    reason TEXT NOT NULL,
    checkpoint_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(document_id) REFERENCES documents(document_id),
    FOREIGN KEY(revision_id) REFERENCES revisions(revision_id),
    FOREIGN KEY(projection_id) REFERENCES extraction_projections(projection_id),
    FOREIGN KEY(revision_id, document_id)
        REFERENCES revisions(revision_id, document_id),
    FOREIGN KEY(projection_id, revision_id)
        REFERENCES extraction_projections(projection_id, revision_id)
);

CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    document_id TEXT,
    revision_id TEXT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id TEXT PRIMARY KEY,
    state TEXT NOT NULL CHECK (state IN ('complete', 'failed')),
    coverage_state TEXT NOT NULL CHECK (coverage_state IN ('complete', 'partial')),
    document_revision_set_hash TEXT NOT NULL,
    extraction_projection_set_hash TEXT NOT NULL,
    document_count INTEGER NOT NULL,
    supported_document_count INTEGER NOT NULL,
    extraction_schema_version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS snapshot_documents (
    snapshot_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    revision_id TEXT NOT NULL,
    projection_id TEXT NOT NULL,
    PRIMARY KEY(snapshot_id, document_id),
    FOREIGN KEY(snapshot_id) REFERENCES snapshots(snapshot_id),
    FOREIGN KEY(document_id) REFERENCES documents(document_id),
    FOREIGN KEY(revision_id) REFERENCES revisions(revision_id),
    FOREIGN KEY(projection_id) REFERENCES extraction_projections(projection_id),
    FOREIGN KEY(revision_id, document_id)
        REFERENCES revisions(revision_id, document_id),
    FOREIGN KEY(projection_id, revision_id)
        REFERENCES extraction_projections(projection_id, revision_id)
);

CREATE TABLE IF NOT EXISTS semantic_commits (
    commit_id TEXT PRIMARY KEY,
    base_snapshot_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    input_sha256 TEXT NOT NULL,
    created_by TEXT NOT NULL CHECK (created_by = 'model'),
    materializer_version TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(base_snapshot_id) REFERENCES snapshots(snapshot_id)
);

CREATE TABLE IF NOT EXISTS atomic_claims (
    claim_id TEXT PRIMARY KEY,
    commit_id TEXT NOT NULL,
    body TEXT NOT NULL,
    subject TEXT,
    modality TEXT NOT NULL,
    scope_and_conditions_json TEXT NOT NULL,
    time_window_json TEXT NOT NULL,
    claim_assessment TEXT NOT NULL
        CHECK (claim_assessment IN ('supported', 'qualified', 'conflicting', 'unresolved')),
    validation_state TEXT NOT NULL
        CHECK (validation_state IN ('unchecked', 'extraction_rechecked')),
    trust_lineage TEXT NOT NULL CHECK (trust_lineage = 'untrusted_source_derived'),
    dependency_state TEXT NOT NULL
        CHECK (dependency_state IN ('valid', 'stale', 'orphaned')),
    temporal_applicability TEXT NOT NULL
        CHECK (temporal_applicability IN ('current', 'expired', 'future', 'unknown')),
    contest_state TEXT NOT NULL
        CHECK (contest_state IN ('uncontested', 'disputed', 'unknown')),
    apparent_status TEXT,
    created_by TEXT NOT NULL CHECK (created_by = 'model'),
    materializer_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(commit_id) REFERENCES semantic_commits(commit_id)
);

CREATE TABLE IF NOT EXISTS evidence_links (
    evidence_link_id TEXT PRIMARY KEY,
    claim_id TEXT NOT NULL,
    source_unit_id TEXT NOT NULL,
    source_revision_id TEXT NOT NULL,
    source_span_json TEXT NOT NULL,
    stance TEXT NOT NULL
        CHECK (stance IN ('supports', 'qualifies', 'contradicts', 'mentions')),
    qualifier TEXT,
    applicability_json TEXT NOT NULL,
    FOREIGN KEY(claim_id) REFERENCES atomic_claims(claim_id),
    FOREIGN KEY(source_unit_id) REFERENCES source_units(unit_id),
    FOREIGN KEY(source_revision_id) REFERENCES revisions(revision_id),
    FOREIGN KEY(source_unit_id, source_revision_id)
        REFERENCES source_units(unit_id, revision_id)
);

CREATE INDEX IF NOT EXISTS idx_evidence_claim ON evidence_links(claim_id);
CREATE INDEX IF NOT EXISTS idx_evidence_revision ON evidence_links(source_revision_id);
CREATE INDEX IF NOT EXISTS idx_claims_dependency ON atomic_claims(dependency_state, created_at);

CREATE VIRTUAL TABLE IF NOT EXISTS atomic_claims_fts USING fts5(
    claim_id UNINDEXED,
    subject,
    body,
    scope_and_conditions,
    tokenize = 'unicode61 remove_diacritics 2'
);
"""

CORPUS_SCHEMA += PROVENANCE_GUARD_SCHEMA
