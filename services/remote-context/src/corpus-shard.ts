import { canonicalJson, nowIso, sha256Hex } from "./canonical";
import { ContextError, asContextError } from "./errors";
import {
  corpusDocumentsImportSchema,
  corpusExternalImportSchema,
  projectionBeginSchema,
  projectionCommitSchema,
  projectionUnitsSchema,
  sourceStateSchema,
} from "./schemas";
import type { Env, ProjectionBeginInput } from "./types";

interface UploadRow {
  header_json: string;
  created_at: string;
}

interface CountRow {
  count: number;
  minimum: number | null;
  maximum: number | null;
}

interface UnitRow {
  unit_id: string;
  revision_id: string;
  projection_id: string;
  ordinal: number;
  unit_type: string;
  structure_path_json: string;
  source_anchor_json: string;
  normalized_content: string;
  content_sha256: string;
  previous_unit_id: string | null;
  next_unit_id: string | null;
  extraction_issues_json: string;
  derivation_method: string;
  geometry_json: string;
  confidence: number | null;
  ocr: number;
  quality_flags_json: string;
  document_id: string;
  relative_path: string;
  source_state: string;
  current_revision_id: string | null;
  completeness_state: "complete" | "partial";
  coverage_json: string;
  issues_json: string;
  assurance_state: string;
}

interface CommittedProjectionRow {
  document_id: string;
  relative_path: string;
  extension: string;
  source_state: string;
  media_type: string | null;
  logical_size: number | null;
  modified_ns: string | null;
  residency_state: string;
  eligibility_state: string;
  current_revision_id: string | null;
  deleted_at: string | null;
  lifecycle_state: "active" | "archived" | "trash";
  retention_class: string;
  last_user_access_at: string | null;
  sha256: string;
  source_size: number;
  predecessor_revision_id: string | null;
  adapter_id: string;
  adapter_version: string;
  config_hash: string;
  result_manifest_hash: string;
  completeness_state: "complete" | "partial";
  coverage_json: string;
  capability_manifest_json: string;
  issues_json: string;
  assurance_state: string;
  is_active: number;
  created_at: string;
  unit_count: number;
}

const SHARD_SCHEMA = `
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS shard_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
  document_id TEXT PRIMARY KEY,
  relative_path TEXT NOT NULL,
  extension TEXT NOT NULL,
  source_state TEXT NOT NULL,
  media_type TEXT,
  logical_size INTEGER,
  modified_ns TEXT,
  residency_state TEXT NOT NULL DEFAULT 'unknown',
  eligibility_state TEXT NOT NULL DEFAULT 'supported',
  current_revision_id TEXT,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  deleted_at TEXT,
  lifecycle_state TEXT NOT NULL DEFAULT 'active'
    CHECK (lifecycle_state IN ('active', 'archived', 'trash')),
  retention_class TEXT NOT NULL DEFAULT 'managed',
  last_user_access_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_documents_path ON documents(relative_path);
CREATE INDEX IF NOT EXISTS idx_documents_state
  ON documents(lifecycle_state, source_state, last_seen_at);

CREATE TABLE IF NOT EXISTS revisions (
  revision_id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  source_size INTEGER NOT NULL,
  captured_at TEXT NOT NULL,
  predecessor_revision_id TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(document_id, sha256),
  FOREIGN KEY(document_id) REFERENCES documents(document_id),
  FOREIGN KEY(predecessor_revision_id) REFERENCES revisions(revision_id)
);

CREATE INDEX IF NOT EXISTS idx_revisions_document
  ON revisions(document_id, captured_at);

CREATE TABLE IF NOT EXISTS projections (
  projection_id TEXT PRIMARY KEY,
  revision_id TEXT NOT NULL,
  adapter_id TEXT NOT NULL,
  adapter_version TEXT NOT NULL,
  config_hash TEXT NOT NULL,
  result_manifest_hash TEXT NOT NULL,
  completeness_state TEXT NOT NULL
    CHECK (completeness_state IN ('complete', 'partial')),
  coverage_json TEXT NOT NULL,
  capability_manifest_json TEXT NOT NULL,
  issues_json TEXT NOT NULL DEFAULT '[]',
  assurance_state TEXT NOT NULL,
  is_active INTEGER NOT NULL DEFAULT 0 CHECK (is_active IN (0, 1)),
  created_at TEXT NOT NULL,
  FOREIGN KEY(revision_id) REFERENCES revisions(revision_id)
);

CREATE INDEX IF NOT EXISTS idx_projections_revision
  ON projections(revision_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_projections_active_revision
  ON projections(revision_id) WHERE is_active = 1;

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
  derivation_method TEXT NOT NULL,
  geometry_json TEXT NOT NULL,
  confidence REAL,
  ocr INTEGER NOT NULL CHECK (ocr IN (0, 1)),
  quality_flags_json TEXT NOT NULL,
  UNIQUE(projection_id, ordinal),
  UNIQUE(unit_id, revision_id),
  FOREIGN KEY(revision_id) REFERENCES revisions(revision_id),
  FOREIGN KEY(projection_id) REFERENCES projections(projection_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_units_revision ON source_units(revision_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_units_projection ON source_units(projection_id, ordinal);

CREATE TABLE IF NOT EXISTS external_bindings (
  binding_id TEXT PRIMARY KEY,
  provider_kind TEXT NOT NULL,
  selector_json TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('active', 'archived')),
  last_complete_run_id TEXT,
  last_complete_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS external_runs (
  run_id TEXT PRIMARY KEY,
  binding_id TEXT NOT NULL,
  base_complete_run_id TEXT,
  status TEXT NOT NULL CHECK (status IN ('incomplete', 'complete')),
  started_at TEXT NOT NULL,
  completed_at TEXT,
  superseded_at TEXT,
  FOREIGN KEY(binding_id) REFERENCES external_bindings(binding_id) ON DELETE CASCADE,
  FOREIGN KEY(base_complete_run_id) REFERENCES external_runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_external_runs_binding
  ON external_runs(binding_id, started_at);

CREATE TABLE IF NOT EXISTS external_records (
  source_record_id TEXT PRIMARY KEY,
  binding_id TEXT NOT NULL,
  external_id TEXT NOT NULL,
  parent_external_id TEXT,
  occurred_at TEXT,
  title TEXT,
  participants_json TEXT NOT NULL,
  label_ids_json TEXT NOT NULL,
  attachments_json TEXT NOT NULL,
  provider_metadata_json TEXT NOT NULL,
  locator_json TEXT NOT NULL,
  freshness_identity TEXT,
  metadata_sha256 TEXT NOT NULL,
  membership_state TEXT NOT NULL CHECK (membership_state IN ('active', 'removed')),
  last_seen_run_id TEXT NOT NULL,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  UNIQUE(binding_id, external_id),
  FOREIGN KEY(binding_id) REFERENCES external_bindings(binding_id) ON DELETE CASCADE,
  FOREIGN KEY(last_seen_run_id) REFERENCES external_runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_external_records_binding
  ON external_records(binding_id, membership_state, occurred_at, external_id);

CREATE VIRTUAL TABLE IF NOT EXISTS source_units_fts USING fts5(
  unit_id UNINDEXED,
  projection_id UNINDEXED,
  document_id UNINDEXED,
  relative_path,
  structure_path,
  normalized_content,
  tokenize = 'unicode61 remove_diacritics 2'
);

CREATE TABLE IF NOT EXISTS staged_uploads (
  upload_id TEXT PRIMARY KEY,
  header_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS staged_units_v2 (
  upload_id TEXT NOT NULL,
  unit_id TEXT NOT NULL,
  ordinal INTEGER NOT NULL,
  unit_type TEXT NOT NULL,
  structure_path_json TEXT NOT NULL,
  source_anchor_json TEXT NOT NULL,
  normalized_content TEXT NOT NULL,
  content_sha256 TEXT NOT NULL,
  previous_unit_id TEXT,
  next_unit_id TEXT,
  extraction_issues_json TEXT NOT NULL,
  derivation_method TEXT NOT NULL,
  geometry_json TEXT NOT NULL,
  confidence REAL,
  ocr INTEGER NOT NULL CHECK (ocr IN (0, 1)),
  quality_flags_json TEXT NOT NULL,
  unit_json TEXT NOT NULL,
  PRIMARY KEY(upload_id, unit_id),
  UNIQUE(upload_id, ordinal),
  FOREIGN KEY(upload_id) REFERENCES staged_uploads(upload_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_staged_units_v2_upload
  ON staged_units_v2(upload_id, ordinal);
`;

function json(value: unknown, status = 200): Response {
  return Response.json(value, { status });
}

function errorResponse(error: unknown): Response {
  const normalized = asContextError(error);
  return json(
    {
      ok: false,
      error: {
        code: normalized.code,
        message: normalized.message,
        details: normalized.details,
      },
    },
    normalized.status,
  );
}

function objectValue(value: string): Record<string, unknown> {
  const parsed: unknown = JSON.parse(value);
  if (parsed === null || Array.isArray(parsed) || typeof parsed !== "object") {
    throw new ContextError(
      "invalid_stored_projection",
      "stored projection data is invalid",
      500,
    );
  }
  return parsed as Record<string, unknown>;
}

function listValue(value: string): unknown[] {
  const parsed: unknown = JSON.parse(value);
  if (!Array.isArray(parsed)) {
    throw new ContextError(
      "invalid_stored_projection",
      "stored projection data is invalid",
      500,
    );
  }
  return parsed;
}

function searchExpression(query: string): string {
  const tokens = [
    ...new Set(
      query
        .normalize("NFC")
        .toLocaleLowerCase()
        .match(/[^\W_]+/gu) ?? [],
    ),
  ]
    .slice(0, 24)
    .map((token) => `"${token.replaceAll('"', '""')}"*`);
  if (tokens.length === 0) {
    throw new ContextError(
      "invalid_query",
      "search query must contain searchable text",
    );
  }
  return tokens.length === 1 ? tokens[0]! : tokens.join(" AND ");
}

function dependencyState(row: UnitRow): string {
  if (row.current_revision_id !== row.revision_id)
    return "stale_source_revision";
  if (row.source_state === "unavailable") return "source_unavailable";
  if (row.source_state === "changed") return "source_changed";
  if (row.source_state === "partially_available")
    return "source_partially_available";
  return "current_source";
}

function unitResult(row: UnitRow): Record<string, unknown> {
  const anchor = objectValue(row.source_anchor_json);
  delete anchor.absolute_path;
  delete anchor.surface_open_target;
  anchor.relative_path = row.relative_path;
  return {
    unit_id: row.unit_id,
    document_id: row.document_id,
    revision_id: row.revision_id,
    projection_id: row.projection_id,
    ordinal: row.ordinal,
    unit_type: row.unit_type,
    structure_path: objectValue(row.structure_path_json),
    source_anchor: anchor,
    untrusted_content: row.normalized_content,
    content_sha256: row.content_sha256,
    previous_unit_id: row.previous_unit_id,
    next_unit_id: row.next_unit_id,
    extraction_issues: listValue(row.extraction_issues_json),
    derivation_method: row.derivation_method,
    geometry: objectValue(row.geometry_json),
    confidence: row.confidence,
    ocr: row.ocr === 1,
    quality_flags: listValue(row.quality_flags_json),
    relative_path: row.relative_path,
    source_state: row.source_state,
    dependency_state: dependencyState(row),
    completeness_state: row.completeness_state,
    coverage: objectValue(row.coverage_json),
    projection_issues: listValue(row.issues_json),
    assurance_state: row.assurance_state,
    trust_lineage: "untrusted_source_derived",
  };
}

export class CorpusShard {
  private readonly sql: DurableObjectStorage["sql"];
  private readonly documentColumns: Set<string>;

  constructor(
    private readonly state: DurableObjectState,
    private readonly env: Env,
  ) {
    void this.env;
    this.sql = state.storage.sql;
    const initialized = [
      ...this.sql.exec<{ name: string }>(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'staged_units_v2'",
      ),
    ].length;
    if (initialized === 0) this.sql.exec(SHARD_SCHEMA);
    this.documentColumns = new Set(
      [...this.sql.exec<{ name: string }>("PRAGMA table_info(documents)")].map(
        (row) => row.name,
      ),
    );
  }

  private ensureSchema(): void {
    const additions: Array<[string, string]> = [
      ["media_type", "TEXT"],
      ["logical_size", "INTEGER"],
      ["modified_ns", "TEXT"],
      ["residency_state", "TEXT NOT NULL DEFAULT 'unknown'"],
      ["eligibility_state", "TEXT NOT NULL DEFAULT 'supported'"],
      ["retention_class", "TEXT NOT NULL DEFAULT 'managed'"],
      ["last_user_access_at", "TEXT"],
    ];
    for (const [name, declaration] of additions) {
      if (!this.documentColumns.has(name)) {
        this.sql.exec(
          `ALTER TABLE documents ADD COLUMN ${name} ${declaration}`,
        );
        this.documentColumns.add(name);
      }
    }
  }

  private documentField(
    table: string,
    name: string,
    fallback: string,
  ): string {
    return this.documentColumns.has(name)
      ? `${table}.${name}`
      : `${fallback} AS ${name}`;
  }

  private one<T>(query: string, ...bindings: unknown[]): T | null {
    const rows = [...this.sql.exec(query, ...bindings)] as unknown as T[];
    return rows[0] ?? null;
  }

  private committedProjection(
    header: ProjectionBeginInput,
  ): Record<string, unknown> | null {
    const row = this.one<CommittedProjectionRow>(
      `SELECT r.document_id, d.relative_path, d.extension, d.source_state,
              ${this.documentField("d", "media_type", "NULL")},
              ${this.documentField("d", "logical_size", "NULL")},
              ${this.documentField("d", "modified_ns", "NULL")},
              ${this.documentField("d", "residency_state", "'unknown'")},
              ${this.documentField("d", "eligibility_state", "'supported'")},
              d.current_revision_id, d.deleted_at, d.lifecycle_state,
              ${this.documentField("d", "retention_class", "'managed'")},
              ${this.documentField("d", "last_user_access_at", "NULL")},
              r.sha256, r.source_size, r.predecessor_revision_id,
              p.adapter_id, p.adapter_version, p.config_hash,
              p.result_manifest_hash, p.completeness_state, p.coverage_json,
              p.capability_manifest_json, p.issues_json, p.assurance_state,
              p.is_active, p.created_at,
              (SELECT COUNT(*) FROM source_units u
               WHERE u.projection_id = p.projection_id) AS unit_count
       FROM projections p
       JOIN revisions r ON r.revision_id = p.revision_id
       JOIN documents d ON d.document_id = r.document_id
       WHERE p.projection_id = ?`,
      header.projection.projectionId,
    );
    if (!row) return null;

    const sameRevision =
      row.document_id === header.document.documentId &&
      row.sha256 === header.revision.sha256 &&
      row.source_size === header.revision.sourceSize &&
      (header.revision.predecessorRevisionId === null ||
        row.predecessor_revision_id ===
          header.revision.predecessorRevisionId);
    const sameProjection =
      row.adapter_id === header.projection.adapterId &&
      row.adapter_version === header.projection.adapterVersion &&
      row.config_hash === header.projection.configHash &&
      row.result_manifest_hash === header.projection.resultManifestHash &&
      row.completeness_state === header.projection.completenessState &&
      row.coverage_json === canonicalJson(header.projection.coverage) &&
      row.capability_manifest_json ===
        canonicalJson(header.projection.capabilityManifest) &&
      row.issues_json === canonicalJson(header.projection.issues) &&
      row.assurance_state === header.projection.assuranceState &&
      row.unit_count === header.projection.declaredUnitCount &&
      (header.projection.createdAt === null ||
        row.created_at === header.projection.createdAt);
    if (!sameRevision) {
      throw new ContextError(
        "revision_identity_conflict",
        "revision id already names different captured content",
        409,
      );
    }
    if (!sameProjection) {
      throw new ContextError(
        "projection_identity_conflict",
        "projection id already names different extracted content",
        409,
      );
    }

    const sameDocumentState =
      row.relative_path === header.document.relativePath.normalize("NFC") &&
      row.extension === header.document.extension &&
      row.source_state === header.document.sourceState &&
      (header.document.mediaType === null ||
        row.media_type === header.document.mediaType) &&
      (header.document.logicalSize === null ||
        row.logical_size === header.document.logicalSize) &&
      (header.document.modifiedNs === null ||
        row.modified_ns === header.document.modifiedNs) &&
      (header.document.residencyState === "unknown" ||
        row.residency_state === header.document.residencyState) &&
      row.eligibility_state === header.document.eligibilityState &&
      row.deleted_at === header.document.deletedAt &&
      row.lifecycle_state === header.document.lifecycleState &&
      row.retention_class === header.document.retentionClass &&
      row.last_user_access_at === header.document.lastUserAccessAt &&
      (!header.revision.makeCurrent ||
        row.current_revision_id === header.revision.revisionId) &&
      row.is_active === (header.projection.activate ? 1 : 0);
    if (!sameDocumentState) return null;

    return {
      corpusId: header.corpusId,
      documentId: header.document.documentId,
      revisionId: header.revision.revisionId,
      projectionId: header.projection.projectionId,
      resultManifestHash: header.projection.resultManifestHash,
      unitCount: row.unit_count,
      committedAt: row.created_at,
      alreadyCommitted: true,
    };
  }

  private begin(ownerId: string, raw: unknown): Record<string, unknown> {
    const input = projectionBeginSchema.parse(raw);
    const storedOwner = this.one<{ value: string }>(
      "SELECT value FROM shard_meta WHERE key = 'owner_id'",
    );
    const storedCorpus = this.one<{ value: string }>(
      "SELECT value FROM shard_meta WHERE key = 'corpus_id'",
    );
    if (storedOwner && storedOwner.value !== ownerId) {
      throw new ContextError(
        "shard_identity_mismatch",
        "Corpus shard owner does not match",
        409,
      );
    }
    if (storedCorpus && storedCorpus.value !== input.corpusId) {
      throw new ContextError(
        "shard_identity_mismatch",
        "Corpus shard id does not match",
        409,
      );
    }

    const header = canonicalJson(input);
    const current = this.one<UploadRow>(
      "SELECT header_json, created_at FROM staged_uploads WHERE upload_id = ?",
      input.uploadId,
    );
    if (current) {
      if (current.header_json !== header) {
        throw new ContextError(
          "upload_conflict",
          "upload id already names a different staged projection",
          409,
        );
      }
    }

    const committed = this.committedProjection(input);
    if (committed) {
      if (current) {
        this.sql.exec(
          "DELETE FROM staged_uploads WHERE upload_id = ?",
          input.uploadId,
        );
      }
      return committed;
    }

    if (current) {
      const count =
        this.one<{ count: number }>(
          "SELECT COUNT(*) AS count FROM staged_units_v2 WHERE upload_id = ?",
          input.uploadId,
        )?.count ?? 0;
      return {
        uploadId: input.uploadId,
        stagedUnitCount: count,
        resumed: true,
      };
    }

    const createdAt = nowIso();
    this.state.storage.transactionSync(() => {
      this.sql.exec(
        "INSERT OR IGNORE INTO shard_meta(key, value) VALUES ('owner_id', ?)",
        ownerId,
      );
      this.sql.exec(
        "INSERT OR IGNORE INTO shard_meta(key, value) VALUES ('corpus_id', ?)",
        input.corpusId,
      );
      this.sql.exec(
        "INSERT INTO staged_uploads(upload_id, header_json, created_at) VALUES (?, ?, ?)",
        input.uploadId,
        header,
        createdAt,
      );
    });
    return { uploadId: input.uploadId, stagedUnitCount: 0, resumed: false };
  }

  private async addUnits(raw: unknown): Promise<Record<string, unknown>> {
    const input = projectionUnitsSchema.parse(raw);
    const upload = this.one<UploadRow>(
      "SELECT header_json, created_at FROM staged_uploads WHERE upload_id = ?",
      input.uploadId,
    );
    if (!upload)
      throw new ContextError(
        "upload_not_found",
        "staged upload was not found",
        404,
      );

    for (const unit of input.units) {
      const digest = await sha256Hex(unit.content);
      if (digest !== unit.contentSha256) {
        throw new ContextError(
          "unit_digest_mismatch",
          "a Source unit content digest does not match",
          400,
          { unitId: unit.unitId },
        );
      }
    }
    this.state.storage.transactionSync(() => {
      for (const unit of input.units) {
        const serialized = canonicalJson(unit);
        const existing = this.one<{ unit_json: string }>(
          "SELECT unit_json FROM staged_units_v2 WHERE upload_id = ? AND unit_id = ?",
          input.uploadId,
          unit.unitId,
        );
        if (existing) {
          if (existing.unit_json !== serialized) {
            throw new ContextError(
              "unit_conflict",
              "a staged Source unit changed within the same upload",
              409,
              { unitId: unit.unitId },
            );
          }
          continue;
        }
        this.sql.exec(
          `INSERT INTO staged_units_v2(
             upload_id, unit_id, ordinal, unit_type, structure_path_json,
             source_anchor_json, normalized_content, content_sha256,
             previous_unit_id, next_unit_id, extraction_issues_json,
             derivation_method, geometry_json, confidence, ocr,
             quality_flags_json, unit_json
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
          input.uploadId,
          unit.unitId,
          unit.ordinal,
          unit.unitType,
          canonicalJson(unit.structurePath),
          canonicalJson(unit.sourceAnchor),
          unit.content,
          unit.contentSha256,
          unit.previousUnitId,
          unit.nextUnitId,
          canonicalJson(unit.extractionIssues),
          unit.derivationMethod,
          canonicalJson(unit.geometry),
          unit.confidence,
          unit.ocr ? 1 : 0,
          canonicalJson(unit.qualityFlags),
          serialized,
        );
      }
    });
    const count =
      this.one<{ count: number }>(
        "SELECT COUNT(*) AS count FROM staged_units_v2 WHERE upload_id = ?",
        input.uploadId,
      )?.count ?? 0;
    return { uploadId: input.uploadId, stagedUnitCount: count };
  }

  private importDocuments(
    ownerId: string,
    raw: unknown,
  ): Record<string, unknown> {
    const input = corpusDocumentsImportSchema.parse(raw);
    this.ensureSchema();
    const storedOwner = this.one<{ value: string }>(
      "SELECT value FROM shard_meta WHERE key = 'owner_id'",
    );
    const storedCorpus = this.one<{ value: string }>(
      "SELECT value FROM shard_meta WHERE key = 'corpus_id'",
    );
    if (storedOwner && storedOwner.value !== ownerId) {
      throw new ContextError(
        "shard_identity_mismatch",
        "Corpus shard owner does not match",
        409,
      );
    }
    if (storedCorpus && storedCorpus.value !== input.corpusId) {
      throw new ContextError(
        "shard_identity_mismatch",
        "Corpus shard id does not match",
        409,
      );
    }
    const changedDocuments = input.documents.filter((document) => {
      const current = this.one<Record<string, unknown>>(
        `SELECT document_id, relative_path, extension, source_state, media_type,
                logical_size, modified_ns, residency_state, eligibility_state,
                current_revision_id, first_seen_at, last_seen_at, deleted_at,
                lifecycle_state, retention_class, last_user_access_at
         FROM documents WHERE document_id = ?`,
        document.documentId,
      );
      return (
        current === null ||
        current.relative_path !== document.relativePath.normalize("NFC") ||
        current.extension !== document.extension ||
        current.source_state !== document.sourceState ||
        current.media_type !== document.mediaType ||
        current.logical_size !== document.logicalSize ||
        current.modified_ns !== document.modifiedNs ||
        current.residency_state !== document.residencyState ||
        current.eligibility_state !== document.eligibilityState ||
        current.current_revision_id !== document.currentRevisionId ||
        current.first_seen_at !== document.firstSeenAt ||
        current.last_seen_at !== document.lastSeenAt ||
        current.deleted_at !== document.deletedAt ||
        current.lifecycle_state !== document.lifecycleState ||
        current.retention_class !== document.retentionClass ||
        current.last_user_access_at !== document.lastUserAccessAt
      );
    });
    if (changedDocuments.length === 0) {
      return {
        corpusId: input.corpusId,
        importedDocumentCount: input.documents.length,
        changedDocumentCount: 0,
      };
    }
    this.state.storage.transactionSync(() => {
      this.sql.exec(
        "INSERT OR IGNORE INTO shard_meta(key, value) VALUES ('owner_id', ?)",
        ownerId,
      );
      this.sql.exec(
        "INSERT OR IGNORE INTO shard_meta(key, value) VALUES ('corpus_id', ?)",
        input.corpusId,
      );
      for (const document of changedDocuments) {
        this.sql.exec(
          `INSERT INTO documents(
             document_id, relative_path, extension, source_state, media_type,
             logical_size, modified_ns, residency_state, eligibility_state,
             current_revision_id, first_seen_at, last_seen_at, deleted_at,
             lifecycle_state, retention_class, last_user_access_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(document_id) DO UPDATE SET
             relative_path = excluded.relative_path,
             extension = excluded.extension,
             source_state = excluded.source_state,
             media_type = excluded.media_type,
             logical_size = excluded.logical_size,
             modified_ns = excluded.modified_ns,
             residency_state = excluded.residency_state,
             eligibility_state = excluded.eligibility_state,
             current_revision_id = excluded.current_revision_id,
             first_seen_at = excluded.first_seen_at,
             last_seen_at = excluded.last_seen_at,
             deleted_at = excluded.deleted_at,
             lifecycle_state = excluded.lifecycle_state,
             retention_class = excluded.retention_class,
             last_user_access_at = excluded.last_user_access_at
           WHERE documents.relative_path IS NOT excluded.relative_path
              OR documents.extension IS NOT excluded.extension
              OR documents.source_state IS NOT excluded.source_state
              OR documents.media_type IS NOT excluded.media_type
              OR documents.logical_size IS NOT excluded.logical_size
              OR documents.modified_ns IS NOT excluded.modified_ns
              OR documents.residency_state IS NOT excluded.residency_state
              OR documents.eligibility_state IS NOT excluded.eligibility_state
              OR documents.current_revision_id IS NOT excluded.current_revision_id
              OR documents.first_seen_at IS NOT excluded.first_seen_at
              OR documents.last_seen_at IS NOT excluded.last_seen_at
              OR documents.deleted_at IS NOT excluded.deleted_at
              OR documents.lifecycle_state IS NOT excluded.lifecycle_state
              OR documents.retention_class IS NOT excluded.retention_class
              OR documents.last_user_access_at IS NOT excluded.last_user_access_at`,
          document.documentId,
          document.relativePath.normalize("NFC"),
          document.extension,
          document.sourceState,
          document.mediaType,
          document.logicalSize,
          document.modifiedNs,
          document.residencyState,
          document.eligibilityState,
          document.currentRevisionId,
          document.firstSeenAt,
          document.lastSeenAt,
          document.deletedAt,
          document.lifecycleState,
          document.retentionClass,
          document.lastUserAccessAt,
        );
      }
    });
    return {
      corpusId: input.corpusId,
      importedDocumentCount: input.documents.length,
      changedDocumentCount: changedDocuments.length,
    };
  }

  private async importExternal(
    ownerId: string,
    raw: unknown,
  ): Promise<Record<string, unknown>> {
    const input = corpusExternalImportSchema.parse(raw);
    const storedOwner = this.one<{ value: string }>(
      "SELECT value FROM shard_meta WHERE key = 'owner_id'",
    );
    const storedCorpus = this.one<{ value: string }>(
      "SELECT value FROM shard_meta WHERE key = 'corpus_id'",
    );
    if (storedOwner && storedOwner.value !== ownerId) {
      throw new ContextError(
        "shard_identity_mismatch",
        "Corpus shard owner does not match",
        409,
      );
    }
    if (storedCorpus && storedCorpus.value !== input.corpusId) {
      throw new ContextError(
        "shard_identity_mismatch",
        "Corpus shard id does not match",
        409,
      );
    }
    const bindingIds = new Set(
      input.bindings.map((binding) => binding.bindingId),
    );
    const runIds = new Set(input.runs.map((run) => run.runId));
    if (
      bindingIds.size !== input.bindings.length ||
      runIds.size !== input.runs.length
    ) {
      throw new ContextError(
        "duplicate_external_identity",
        "external Source identities are duplicated",
      );
    }
    for (const run of input.runs) {
      if (
        !bindingIds.has(run.bindingId) ||
        (run.baseCompleteRunId && !runIds.has(run.baseCompleteRunId))
      ) {
        throw new ContextError(
          "invalid_external_binding",
          "external Source run binding is invalid",
        );
      }
    }
    const recordIds = new Set<string>();
    for (const record of input.records) {
      if (
        recordIds.has(record.sourceRecordId) ||
        !bindingIds.has(record.bindingId) ||
        !runIds.has(record.lastSeenRunId)
      ) {
        throw new ContextError(
          "invalid_external_binding",
          "external Source record binding is invalid",
        );
      }
      recordIds.add(record.sourceRecordId);
    }

    const sourceDigest = await sha256Hex(canonicalJson(input));
    const previousDigest = this.one<{ value: string }>(
      "SELECT value FROM shard_meta WHERE key = 'external_import_sha256'",
    );
    if (previousDigest?.value === sourceDigest) {
      return {
        corpusId: input.corpusId,
        importedBindingCount: input.bindings.length,
        importedRunCount: input.runs.length,
        importedRecordCount: input.records.length,
        changed: false,
      };
    }

    this.state.storage.transactionSync(() => {
      this.sql.exec(
        "INSERT OR IGNORE INTO shard_meta(key, value) VALUES ('owner_id', ?)",
        ownerId,
      );
      this.sql.exec(
        "INSERT OR IGNORE INTO shard_meta(key, value) VALUES ('corpus_id', ?)",
        input.corpusId,
      );
      this.sql.exec("DELETE FROM external_records");
      this.sql.exec("DELETE FROM external_runs");
      this.sql.exec("DELETE FROM external_bindings");
      for (const binding of input.bindings) {
        this.sql.exec(
          `INSERT INTO external_bindings(
             binding_id, provider_kind, selector_json, state,
             last_complete_run_id, last_complete_at, created_at, updated_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
          binding.bindingId,
          binding.providerKind,
          canonicalJson(binding.selector),
          binding.state,
          binding.lastCompleteRunId,
          binding.lastCompleteAt,
          binding.createdAt,
          binding.updatedAt,
        );
      }
      for (const run of input.runs) {
        this.sql.exec(
          `INSERT INTO external_runs(
             run_id, binding_id, base_complete_run_id, status, started_at,
             completed_at, superseded_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?)`,
          run.runId,
          run.bindingId,
          null,
          run.status,
          run.startedAt,
          run.completedAt,
          run.supersededAt,
        );
      }
      for (const run of input.runs) {
        if (run.baseCompleteRunId) {
          this.sql.exec(
            "UPDATE external_runs SET base_complete_run_id = ? WHERE run_id = ?",
            run.baseCompleteRunId,
            run.runId,
          );
        }
      }
      for (const record of input.records) {
        this.sql.exec(
          `INSERT INTO external_records(
             source_record_id, binding_id, external_id, parent_external_id,
             occurred_at, title, participants_json, label_ids_json,
             attachments_json, provider_metadata_json, locator_json,
             freshness_identity, metadata_sha256, membership_state,
             last_seen_run_id, first_seen_at, last_seen_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
          record.sourceRecordId,
          record.bindingId,
          record.externalId,
          record.parentExternalId,
          record.occurredAt,
          record.title,
          canonicalJson(record.participants),
          canonicalJson(record.labelIds),
          canonicalJson(record.attachments),
          canonicalJson(record.providerMetadata),
          canonicalJson(record.locator),
          record.freshnessIdentity,
          record.metadataSha256,
          record.membershipState,
          record.lastSeenRunId,
          record.firstSeenAt,
          record.lastSeenAt,
        );
      }
      this.sql.exec(
        `INSERT INTO shard_meta(key, value)
         VALUES ('external_import_sha256', ?)
         ON CONFLICT(key) DO UPDATE SET value = excluded.value`,
        sourceDigest,
      );
    });
    return {
      corpusId: input.corpusId,
      importedBindingCount: input.bindings.length,
      importedRunCount: input.runs.length,
      importedRecordCount: input.records.length,
      changed: true,
    };
  }

  private commit(raw: unknown): Record<string, unknown> {
    const input = projectionCommitSchema.parse(raw);
    this.ensureSchema();
    const upload = this.one<UploadRow>(
      "SELECT header_json, created_at FROM staged_uploads WHERE upload_id = ?",
      input.uploadId,
    );
    if (!upload)
      throw new ContextError(
        "upload_not_found",
        "staged upload was not found",
        404,
      );
    const header = projectionBeginSchema.parse(JSON.parse(upload.header_json));
    if (
      header.projection.declaredUnitCount !== input.expectedUnitCount ||
      header.projection.resultManifestHash !== input.expectedManifestHash
    ) {
      throw new ContextError(
        "projection_manifest_mismatch",
        "projection commit does not match the staged header",
        409,
      );
    }
    const counts = this.one<CountRow>(
      `SELECT COUNT(*) AS count, MIN(ordinal) AS minimum, MAX(ordinal) AS maximum
       FROM staged_units_v2 WHERE upload_id = ?`,
      input.uploadId,
    ) ?? { count: 0, minimum: null, maximum: null };
    const contiguous =
      input.expectedUnitCount === 0 ||
      (counts.minimum === 1 && counts.maximum === input.expectedUnitCount);
    if (counts.count !== input.expectedUnitCount || !contiguous) {
      throw new ContextError(
        "projection_unit_count_mismatch",
        "staged Source units are incomplete or non-contiguous",
        409,
        { expected: input.expectedUnitCount, actual: counts.count },
      );
    }

    const document = this.one<{ current_revision_id: string | null }>(
      "SELECT current_revision_id FROM documents WHERE document_id = ?",
      header.document.documentId,
    );
    const expectedPredecessor =
      header.revision.predecessorRevisionId ??
      (header.revision.makeCurrent &&
      document?.current_revision_id !== header.revision.revisionId
        ? (document?.current_revision_id ?? null)
        : null);
    const existingRevision = this.one<{
      document_id: string;
      sha256: string;
      source_size: number;
      captured_at: string;
      predecessor_revision_id: string | null;
    }>(
      `SELECT document_id, sha256, source_size, captured_at, predecessor_revision_id
       FROM revisions WHERE revision_id = ?`,
      header.revision.revisionId,
    );
    if (
      existingRevision &&
      (existingRevision.document_id !== header.document.documentId ||
        existingRevision.sha256 !== header.revision.sha256 ||
        existingRevision.source_size !== header.revision.sourceSize ||
        (header.revision.predecessorRevisionId !== null &&
          existingRevision.predecessor_revision_id !==
            header.revision.predecessorRevisionId))
    ) {
      throw new ContextError(
        "revision_identity_conflict",
        "revision id already names different captured content",
        409,
      );
    }

    const existingProjection = this.one<{
      revision_id: string;
      result_manifest_hash: string;
    }>(
      `SELECT revision_id, result_manifest_hash FROM projections WHERE projection_id = ?`,
      header.projection.projectionId,
    );
    if (
      existingProjection &&
      (existingProjection.revision_id !== header.revision.revisionId ||
        existingProjection.result_manifest_hash !==
          header.projection.resultManifestHash)
    ) {
      throw new ContextError(
        "projection_identity_conflict",
        "projection id already names different extracted content",
        409,
      );
    }

    const committedAt = nowIso();
    this.state.storage.transactionSync(() => {
      this.sql.exec(
        `INSERT INTO documents(
           document_id, relative_path, extension, source_state, media_type,
           logical_size, modified_ns, residency_state, eligibility_state,
           current_revision_id, first_seen_at, last_seen_at, deleted_at,
           lifecycle_state, retention_class, last_user_access_at
         ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)
         ON CONFLICT(document_id) DO UPDATE SET
           relative_path = excluded.relative_path,
           extension = excluded.extension,
           source_state = excluded.source_state,
           media_type = COALESCE(excluded.media_type, documents.media_type),
           logical_size = COALESCE(excluded.logical_size, documents.logical_size),
           modified_ns = COALESCE(excluded.modified_ns, documents.modified_ns),
           residency_state = CASE
             WHEN excluded.residency_state = 'unknown'
             THEN documents.residency_state
             ELSE excluded.residency_state
           END,
           eligibility_state = excluded.eligibility_state,
           deleted_at = excluded.deleted_at,
           lifecycle_state = excluded.lifecycle_state,
           retention_class = excluded.retention_class,
           last_user_access_at = excluded.last_user_access_at
         WHERE documents.relative_path IS NOT excluded.relative_path
            OR documents.extension IS NOT excluded.extension
            OR documents.source_state IS NOT excluded.source_state
            OR (excluded.media_type IS NOT NULL
                AND documents.media_type IS NOT excluded.media_type)
            OR (excluded.logical_size IS NOT NULL
                AND documents.logical_size IS NOT excluded.logical_size)
            OR (excluded.modified_ns IS NOT NULL
                AND documents.modified_ns IS NOT excluded.modified_ns)
            OR (excluded.residency_state != 'unknown'
                AND documents.residency_state IS NOT excluded.residency_state)
            OR documents.eligibility_state IS NOT excluded.eligibility_state
            OR documents.deleted_at IS NOT excluded.deleted_at
            OR documents.lifecycle_state IS NOT excluded.lifecycle_state
            OR documents.retention_class IS NOT excluded.retention_class
            OR documents.last_user_access_at IS NOT excluded.last_user_access_at`,
        header.document.documentId,
        header.document.relativePath.normalize("NFC"),
        header.document.extension,
        header.document.sourceState,
        header.document.mediaType,
        header.document.logicalSize,
        header.document.modifiedNs,
        header.document.residencyState,
        header.document.eligibilityState,
        committedAt,
        committedAt,
        header.document.deletedAt,
        header.document.lifecycleState,
        header.document.retentionClass,
        header.document.lastUserAccessAt,
      );
      this.sql.exec(
        `INSERT INTO revisions(
           revision_id, document_id, sha256, source_size, captured_at,
           predecessor_revision_id, created_at
         ) VALUES (?, ?, ?, ?, ?, ?, ?)
         ON CONFLICT(revision_id) DO NOTHING`,
        header.revision.revisionId,
        header.document.documentId,
        header.revision.sha256,
        header.revision.sourceSize,
        header.revision.capturedAt,
        expectedPredecessor,
        committedAt,
      );
      if (existingProjection) {
        this.sql.exec(
          "DELETE FROM source_units_fts WHERE projection_id = ?",
          header.projection.projectionId,
        );
        this.sql.exec(
          "DELETE FROM source_units WHERE projection_id = ?",
          header.projection.projectionId,
        );
        this.sql.exec(
          "DELETE FROM projections WHERE projection_id = ?",
          header.projection.projectionId,
        );
      }
      if (header.projection.activate) {
        this.sql.exec(
          "UPDATE projections SET is_active = 0 WHERE revision_id = ? AND is_active = 1",
          header.revision.revisionId,
        );
      }
      this.sql.exec(
        `INSERT INTO projections(
           projection_id, revision_id, adapter_id, adapter_version, config_hash,
           result_manifest_hash, completeness_state, coverage_json,
           capability_manifest_json, issues_json, assurance_state, is_active, created_at
         ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
        header.projection.projectionId,
        header.revision.revisionId,
        header.projection.adapterId,
        header.projection.adapterVersion,
        header.projection.configHash,
        header.projection.resultManifestHash,
        header.projection.completenessState,
        canonicalJson(header.projection.coverage),
        canonicalJson(header.projection.capabilityManifest),
        canonicalJson(header.projection.issues),
        header.projection.assuranceState,
        header.projection.activate ? 1 : 0,
        header.projection.createdAt ?? committedAt,
      );
      this.sql.exec(
        `INSERT INTO source_units(
           unit_id, revision_id, projection_id, ordinal, unit_type,
           structure_path_json, source_anchor_json, normalized_content,
           content_sha256, previous_unit_id, next_unit_id,
           extraction_issues_json, derivation_method, geometry_json,
           confidence, ocr, quality_flags_json
         )
         SELECT unit_id, ?, ?, ordinal, unit_type, structure_path_json,
                source_anchor_json, normalized_content, content_sha256,
                previous_unit_id, next_unit_id, extraction_issues_json,
                derivation_method, geometry_json, confidence, ocr,
                quality_flags_json
         FROM staged_units_v2 WHERE upload_id = ? ORDER BY ordinal`,
        header.revision.revisionId,
        header.projection.projectionId,
        input.uploadId,
      );
      this.sql.exec(
        `INSERT INTO source_units_fts(
           unit_id, projection_id, document_id, relative_path,
           structure_path, normalized_content
         )
         SELECT unit_id, ?, ?, ?, structure_path_json, normalized_content
         FROM staged_units_v2 WHERE upload_id = ? ORDER BY ordinal`,
        header.projection.projectionId,
        header.document.documentId,
        header.document.relativePath.normalize("NFC"),
        input.uploadId,
      );
      if (header.revision.makeCurrent) {
        this.sql.exec(
          `UPDATE documents SET current_revision_id = ?, source_state = ?
           WHERE document_id = ?`,
          header.revision.revisionId,
          header.document.sourceState,
          header.document.documentId,
        );
      }
      this.sql.exec(
        "DELETE FROM staged_uploads WHERE upload_id = ?",
        input.uploadId,
      );
    });
    return {
      corpusId: header.corpusId,
      documentId: header.document.documentId,
      revisionId: header.revision.revisionId,
      projectionId: header.projection.projectionId,
      resultManifestHash: header.projection.resultManifestHash,
      unitCount: counts.count,
      committedAt,
    };
  }

  private sourceState(raw: unknown): Record<string, unknown> {
    const input = sourceStateSchema.parse(raw);
    const corpus = this.one<{ value: string }>(
      "SELECT value FROM shard_meta WHERE key = 'corpus_id'",
    );
    if (corpus && corpus.value !== input.corpusId) {
      throw new ContextError(
        "shard_identity_mismatch",
        "Corpus shard id does not match",
        409,
      );
    }
    const document = this.one<{
      document_id: string;
      relative_path: string;
      source_state: string;
      deleted_at: string | null;
    }>(
      `SELECT document_id, relative_path, source_state, deleted_at
       FROM documents WHERE document_id = ?`,
      input.documentId,
    );
    if (!document)
      throw new ContextError(
        "document_not_found",
        "document was not found",
        404,
      );
    const relativePath = input.relativePath?.normalize("NFC") ?? null;
    const pathChanged =
      relativePath !== null && relativePath !== document.relative_path;
    const deletionChanged =
      input.sourceState === "unavailable"
        ? document.deleted_at === null
        : document.deleted_at !== null;
    const changed =
      document.source_state !== input.sourceState ||
      pathChanged ||
      deletionChanged;
    if (changed) {
      this.state.storage.transactionSync(() => {
        this.sql.exec(
          `UPDATE documents SET
             source_state = ?,
             relative_path = COALESCE(?, relative_path),
             last_seen_at = ?,
             deleted_at = CASE WHEN ? = 'unavailable' THEN ? ELSE NULL END
           WHERE document_id = ?`,
          input.sourceState,
          relativePath,
          input.observedAt,
          input.sourceState,
          input.observedAt,
          input.documentId,
        );
        if (pathChanged) {
          this.sql.exec(
            "UPDATE source_units_fts SET relative_path = ? WHERE document_id = ?",
            relativePath,
            input.documentId,
          );
        }
      });
    }
    return {
      corpusId: input.corpusId,
      documentId: input.documentId,
      sourceState: input.sourceState,
      observedAt: input.observedAt,
      changed,
    };
  }

  private search(raw: unknown): Record<string, unknown> {
    const value = raw as Record<string, unknown>;
    const query = typeof value.query === "string" ? value.query.trim() : "";
    const limit = Number.isInteger(value.limit) ? Number(value.limit) : 20;
    if (!query || query.length > 2_000 || limit < 1 || limit > 100) {
      throw new ContextError(
        "invalid_query",
        "Corpus search parameters are invalid",
      );
    }
    const expression = searchExpression(query);
    const rows = [
      ...this.sql.exec<{
        unit_id: string;
        document_id: string;
        projection_id: string;
        relative_path: string;
        normalized_content: string;
        rank: number;
        revision_id: string;
        source_state: string;
        completeness_state: string;
        assurance_state: string;
      }>(
        `SELECT f.unit_id, f.document_id, f.projection_id, d.relative_path,
                f.normalized_content, bm25(source_units_fts) AS rank,
                u.revision_id, d.source_state, p.completeness_state,
                p.assurance_state
         FROM source_units_fts AS f
         JOIN source_units AS u ON u.unit_id = f.unit_id
         JOIN projections AS p ON p.projection_id = u.projection_id AND p.is_active = 1
         JOIN documents AS d
           ON d.document_id = f.document_id
          AND d.current_revision_id = u.revision_id
          AND d.lifecycle_state = 'active'
         WHERE source_units_fts MATCH ?
         ORDER BY rank, f.relative_path, u.ordinal
         LIMIT ?`,
        expression,
        limit,
      ),
    ];
    return {
      query,
      count: rows.length,
      candidates: rows.map((row) => ({
        unit_id: row.unit_id,
        document_id: row.document_id,
        revision_id: row.revision_id,
        projection_id: row.projection_id,
        relative_path: row.relative_path,
        excerpt: row.normalized_content.slice(0, 1_200),
        source_state: row.source_state,
        completeness_state: row.completeness_state,
        assurance_state: row.assurance_state,
        trust_lineage: "untrusted_source_derived",
      })),
    };
  }

  private readUnits(raw: unknown): Record<string, unknown> {
    const value = raw as Record<string, unknown>;
    if (
      !Array.isArray(value.unitIds) ||
      value.unitIds.length < 1 ||
      value.unitIds.length > 100 ||
      !value.unitIds.every((item) => typeof item === "string")
    ) {
      throw new ContextError(
        "invalid_read",
        "unitIds must contain 1 to 100 unit ids",
      );
    }
    const neighborSpan = Number.isInteger(value.neighborSpan)
      ? Number(value.neighborSpan)
      : 0;
    if (neighborSpan < 0 || neighborSpan > 10) {
      throw new ContextError(
        "invalid_read",
        "neighborSpan must be between 0 and 10",
      );
    }
    const results: Record<string, unknown>[] = [];
    const missing: string[] = [];
    const selectedRows = new Map<string, UnitRow>();
    for (const id of [...new Set(value.unitIds as string[])]) {
      const row = this.one<UnitRow>(
        `SELECT u.*, d.document_id, d.relative_path, d.source_state,
                d.current_revision_id, p.completeness_state, p.coverage_json,
                p.issues_json, p.assurance_state
         FROM source_units AS u
         JOIN revisions AS r ON r.revision_id = u.revision_id
         JOIN documents AS d ON d.document_id = r.document_id
         JOIN projections AS p ON p.projection_id = u.projection_id
         WHERE u.unit_id = ?`,
        id,
      );
      if (!row) missing.push(id);
      else {
        selectedRows.set(row.unit_id, row);
        if (neighborSpan > 0) {
          const neighbors = [
            ...this.sql.exec(
              `SELECT u.*, d.document_id, d.relative_path, d.source_state,
                      d.current_revision_id, p.completeness_state, p.coverage_json,
                      p.issues_json, p.assurance_state
               FROM source_units AS u
               JOIN revisions AS r ON r.revision_id = u.revision_id
               JOIN documents AS d ON d.document_id = r.document_id
               JOIN projections AS p ON p.projection_id = u.projection_id
               WHERE u.projection_id = ? AND u.ordinal BETWEEN ? AND ?
               ORDER BY u.ordinal`,
              row.projection_id,
              Math.max(1, row.ordinal - neighborSpan),
              row.ordinal + neighborSpan,
            ),
          ] as unknown as UnitRow[];
          for (const neighbor of neighbors)
            selectedRows.set(neighbor.unit_id, neighbor);
        }
      }
    }
    for (const row of [...selectedRows.values()].sort(
      (left, right) =>
        left.projection_id.localeCompare(right.projection_id) ||
        left.ordinal - right.ordinal,
    )) {
      results.push(unitResult(row));
    }
    return { count: results.length, units: results, missing_unit_ids: missing };
  }

  private inventory(raw: unknown): Record<string, unknown> {
    const value = raw as Record<string, unknown>;
    const documentOffset = Number.isInteger(value.documentOffset)
      ? Number(value.documentOffset)
      : 0;
    const projectionOffset = Number.isInteger(value.projectionOffset)
      ? Number(value.projectionOffset)
      : 0;
    const limit = Number.isInteger(value.limit) ? Number(value.limit) : 500;
    if (
      documentOffset < 0 ||
      projectionOffset < 0 ||
      limit < 1 ||
      limit > 500
    ) {
      throw new ContextError(
        "invalid_inventory_request",
        "inventory pagination is invalid",
      );
    }
    const documents = [
      ...this.sql.exec(
        `SELECT d.document_id, d.relative_path, d.extension, d.source_state,
                ${this.documentField("d", "media_type", "NULL")},
                ${this.documentField("d", "logical_size", "NULL")},
                ${this.documentField("d", "modified_ns", "NULL")},
                ${this.documentField("d", "residency_state", "'unknown'")},
                ${this.documentField("d", "eligibility_state", "'supported'")},
                d.current_revision_id, d.first_seen_at, d.last_seen_at,
                d.deleted_at, d.lifecycle_state,
                ${this.documentField("d", "retention_class", "'managed'")},
                ${this.documentField("d", "last_user_access_at", "NULL")},
                r.sha256, r.source_size, r.captured_at,
                p.projection_id, p.result_manifest_hash, p.completeness_state,
                p.assurance_state,
                (SELECT COUNT(*) FROM source_units u WHERE u.projection_id = p.projection_id)
                  AS unit_count
         FROM documents d
         LEFT JOIN revisions r ON r.revision_id = d.current_revision_id
         LEFT JOIN projections p ON p.revision_id = r.revision_id AND p.is_active = 1
         ORDER BY d.document_id LIMIT ? OFFSET ?`,
        limit + 1,
        documentOffset,
      ),
    ] as unknown as Array<Record<string, unknown>>;
    const projections = [
      ...this.sql.exec(
        `SELECT p.projection_id, p.revision_id, r.document_id, r.sha256,
                r.source_size, r.captured_at, r.predecessor_revision_id,
                p.adapter_id, p.adapter_version, p.config_hash,
                p.result_manifest_hash, p.completeness_state, p.assurance_state,
                p.coverage_json, p.capability_manifest_json, p.issues_json,
                p.is_active, p.created_at,
                CASE WHEN d.current_revision_id = r.revision_id THEN 1 ELSE 0 END
                  AS is_current_revision,
                (SELECT COUNT(*) FROM source_units u
                 WHERE u.projection_id = p.projection_id) AS unit_count
         FROM projections p
         JOIN revisions r ON r.revision_id = p.revision_id
         JOIN documents d ON d.document_id = r.document_id
         ORDER BY p.projection_id LIMIT ? OFFSET ?`,
        limit + 1,
        projectionOffset,
      ),
    ] as unknown as Array<Record<string, unknown>>;
    const counts = {
      documents:
        this.one<{ count: number }>("SELECT COUNT(*) AS count FROM documents")
          ?.count ?? 0,
      revisions:
        this.one<{ count: number }>("SELECT COUNT(*) AS count FROM revisions")
          ?.count ?? 0,
      projections:
        this.one<{ count: number }>("SELECT COUNT(*) AS count FROM projections")
          ?.count ?? 0,
      units:
        this.one<{ count: number }>(
          "SELECT COUNT(*) AS count FROM source_units",
        )?.count ?? 0,
    };
    const staged =
      this.one<{ count: number }>(
        "SELECT COUNT(*) AS count FROM staged_uploads",
      )?.count ?? 0;
    const external = {
      binding_count:
        this.one<{ count: number }>(
          "SELECT COUNT(*) AS count FROM external_bindings",
        )?.count ?? 0,
      run_count:
        this.one<{ count: number }>(
          "SELECT COUNT(*) AS count FROM external_runs",
        )?.count ?? 0,
      record_count:
        this.one<{ count: number }>(
          "SELECT COUNT(*) AS count FROM external_records",
        )?.count ?? 0,
    };
    return {
      counts,
      documents: documents.slice(0, limit),
      document_offset: documentOffset,
      document_has_more: documents.length > limit,
      projections: projections.slice(0, limit),
      projection_offset: projectionOffset,
      projection_has_more: projections.length > limit,
      staged_upload_count: staged,
      external,
    };
  }

  async fetch(request: Request): Promise<Response> {
    try {
      if (request.method !== "POST") {
        throw new ContextError(
          "method_not_allowed",
          "only POST is supported",
          405,
        );
      }
      const ownerId = request.headers.get("X-Owner-Id");
      if (!ownerId)
        throw new ContextError(
          "missing_owner",
          "internal owner id is required",
          401,
        );
      const path = new URL(request.url).pathname;
      const body: unknown = await request.json();
      if (path === "/projection/begin")
        return json({ ok: true, result: this.begin(ownerId, body) });
      if (path === "/documents/import") {
        return json({ ok: true, result: this.importDocuments(ownerId, body) });
      }
      if (path === "/external/import") {
        return json({
          ok: true,
          result: await this.importExternal(ownerId, body),
        });
      }
      if (path === "/projection/units") {
        return json({ ok: true, result: await this.addUnits(body) });
      }
      if (path === "/projection/commit")
        return json({ ok: true, result: this.commit(body) });
      if (path === "/source-state")
        return json({ ok: true, result: this.sourceState(body) });
      if (path === "/search")
        return json({ ok: true, result: this.search(body) });
      if (path === "/units/read")
        return json({ ok: true, result: this.readUnits(body) });
      if (path === "/inventory")
        return json({ ok: true, result: this.inventory(body) });
      throw new ContextError(
        "not_found",
        "Corpus shard route was not found",
        404,
      );
    } catch (error) {
      return errorResponse(error);
    }
  }
}
