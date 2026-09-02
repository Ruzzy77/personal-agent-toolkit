import { canonicalJson, nowIso, sha256Hex } from "./canonical";
import { ContextError, asContextError } from "./errors";
import {
  projectionBeginSchema,
  projectionCommitSchema,
  projectionUnitsSchema,
  sourceStateSchema,
} from "./schemas";
import type { CorpusUnitInput, Env, ProjectionBeginInput } from "./types";

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
  current_revision_id TEXT,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  deleted_at TEXT,
  lifecycle_state TEXT NOT NULL DEFAULT 'active'
    CHECK (lifecycle_state IN ('active', 'archived', 'trash'))
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

CREATE TABLE IF NOT EXISTS staged_units (
  upload_id TEXT NOT NULL,
  unit_id TEXT NOT NULL,
  ordinal INTEGER NOT NULL,
  unit_json TEXT NOT NULL,
  PRIMARY KEY(upload_id, unit_id),
  UNIQUE(upload_id, ordinal),
  FOREIGN KEY(upload_id) REFERENCES staged_uploads(upload_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_staged_units_upload
  ON staged_units(upload_id, ordinal);
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
    throw new ContextError("invalid_stored_projection", "stored projection data is invalid", 500);
  }
  return parsed as Record<string, unknown>;
}

function listValue(value: string): unknown[] {
  const parsed: unknown = JSON.parse(value);
  if (!Array.isArray(parsed)) {
    throw new ContextError("invalid_stored_projection", "stored projection data is invalid", 500);
  }
  return parsed;
}

function searchExpression(query: string): string {
  const tokens = [...new Set(query.normalize("NFC").toLocaleLowerCase().match(/[^\W_]+/gu) ?? [])]
    .slice(0, 24)
    .map((token) => `"${token.replaceAll('"', '""')}"*`);
  if (tokens.length === 0) {
    throw new ContextError("invalid_query", "search query must contain searchable text");
  }
  return tokens.length === 1 ? tokens[0]! : tokens.join(" AND ");
}

function dependencyState(row: UnitRow): string {
  if (row.current_revision_id !== row.revision_id) return "stale_source_revision";
  if (row.source_state === "unavailable") return "source_unavailable";
  if (row.source_state === "changed") return "source_changed";
  if (row.source_state === "partially_available") return "source_partially_available";
  return "current_source";
}

function unitResult(row: UnitRow): Record<string, unknown> {
  const anchor = objectValue(row.source_anchor_json);
  delete anchor.absolute_path;
  delete anchor.surface_open_target;
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

  constructor(
    private readonly state: DurableObjectState,
    private readonly env: Env,
  ) {
    void this.env;
    this.sql = state.storage.sql;
    this.sql.exec(SHARD_SCHEMA);
  }

  private one<T>(query: string, ...bindings: unknown[]): T | null {
    const rows = [...this.sql.exec(query, ...bindings)] as unknown as T[];
    return rows[0] ?? null;
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
      throw new ContextError("shard_identity_mismatch", "Corpus shard owner does not match", 409);
    }
    if (storedCorpus && storedCorpus.value !== input.corpusId) {
      throw new ContextError("shard_identity_mismatch", "Corpus shard id does not match", 409);
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
      const count = this.one<{ count: number }>(
        "SELECT COUNT(*) AS count FROM staged_units WHERE upload_id = ?",
        input.uploadId,
      )?.count ?? 0;
      return { uploadId: input.uploadId, stagedUnitCount: count, resumed: true };
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
    if (!upload) throw new ContextError("upload_not_found", "staged upload was not found", 404);

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
          "SELECT unit_json FROM staged_units WHERE upload_id = ? AND unit_id = ?",
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
          `INSERT INTO staged_units(upload_id, unit_id, ordinal, unit_json)
           VALUES (?, ?, ?, ?)`,
          input.uploadId,
          unit.unitId,
          unit.ordinal,
          serialized,
        );
      }
    });
    const count = this.one<{ count: number }>(
      "SELECT COUNT(*) AS count FROM staged_units WHERE upload_id = ?",
      input.uploadId,
    )?.count ?? 0;
    return { uploadId: input.uploadId, stagedUnitCount: count };
  }

  private commit(raw: unknown): Record<string, unknown> {
    const input = projectionCommitSchema.parse(raw);
    const upload = this.one<UploadRow>(
      "SELECT header_json, created_at FROM staged_uploads WHERE upload_id = ?",
      input.uploadId,
    );
    if (!upload) throw new ContextError("upload_not_found", "staged upload was not found", 404);
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
       FROM staged_units WHERE upload_id = ?`,
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

    const staged = [
      ...this.sql.exec<{ unit_json: string }>(
        "SELECT unit_json FROM staged_units WHERE upload_id = ? ORDER BY ordinal",
        input.uploadId,
      ),
    ].map((row) => projectionUnitsSchema.shape.units.element.parse(JSON.parse(row.unit_json)));
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
        existingProjection.result_manifest_hash !== header.projection.resultManifestHash)
    ) {
      throw new ContextError(
        "projection_identity_conflict",
        "projection id already names different extracted content",
        409,
      );
    }

    const committedAt = nowIso();
    this.state.storage.transactionSync(() => {
      const document = this.one<{ current_revision_id: string | null }>(
        "SELECT current_revision_id FROM documents WHERE document_id = ?",
        header.document.documentId,
      );
      this.sql.exec(
        `INSERT INTO documents(
           document_id, relative_path, extension, source_state, current_revision_id,
           first_seen_at, last_seen_at, deleted_at, lifecycle_state
         ) VALUES (?, ?, ?, ?, NULL, ?, ?, NULL, 'active')
         ON CONFLICT(document_id) DO UPDATE SET
           relative_path = excluded.relative_path,
           extension = excluded.extension,
           source_state = excluded.source_state,
           last_seen_at = excluded.last_seen_at,
           deleted_at = NULL,
           lifecycle_state = 'active'`,
        header.document.documentId,
        header.document.relativePath.normalize("NFC"),
        header.document.extension,
        header.document.sourceState,
        committedAt,
        committedAt,
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
        document?.current_revision_id ?? null,
        committedAt,
      );
      if (existingProjection) {
        this.sql.exec("DELETE FROM source_units_fts WHERE projection_id = ?", header.projection.projectionId);
        this.sql.exec("DELETE FROM source_units WHERE projection_id = ?", header.projection.projectionId);
        this.sql.exec("DELETE FROM projections WHERE projection_id = ?", header.projection.projectionId);
      }
      this.sql.exec(
        "UPDATE projections SET is_active = 0 WHERE revision_id = ?",
        header.revision.revisionId,
      );
      this.sql.exec(
        `INSERT INTO projections(
           projection_id, revision_id, adapter_id, adapter_version, config_hash,
           result_manifest_hash, completeness_state, coverage_json,
           capability_manifest_json, issues_json, assurance_state, is_active, created_at
         ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)`,
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
        committedAt,
      );
      for (const unit of staged) this.insertUnit(header, unit);
      this.sql.exec(
        `UPDATE documents SET current_revision_id = ?, source_state = ?, last_seen_at = ?
         WHERE document_id = ?`,
        header.revision.revisionId,
        header.document.sourceState,
        committedAt,
        header.document.documentId,
      );
      this.sql.exec("DELETE FROM staged_uploads WHERE upload_id = ?", input.uploadId);
    });
    return {
      corpusId: header.corpusId,
      documentId: header.document.documentId,
      revisionId: header.revision.revisionId,
      projectionId: header.projection.projectionId,
      resultManifestHash: header.projection.resultManifestHash,
      unitCount: staged.length,
      committedAt,
    };
  }

  private insertUnit(header: ProjectionBeginInput, unit: CorpusUnitInput): void {
    this.sql.exec(
      `INSERT INTO source_units(
         unit_id, revision_id, projection_id, ordinal, unit_type, structure_path_json,
         source_anchor_json, normalized_content, content_sha256, previous_unit_id,
         next_unit_id, extraction_issues_json, derivation_method, geometry_json,
         confidence, ocr, quality_flags_json
       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      unit.unitId,
      header.revision.revisionId,
      header.projection.projectionId,
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
    );
    this.sql.exec(
      `INSERT INTO source_units_fts(
         unit_id, projection_id, document_id, relative_path, structure_path, normalized_content
       ) VALUES (?, ?, ?, ?, ?, ?)`,
      unit.unitId,
      header.projection.projectionId,
      header.document.documentId,
      header.document.relativePath.normalize("NFC"),
      canonicalJson(unit.structurePath),
      unit.content,
    );
  }

  private sourceState(raw: unknown): Record<string, unknown> {
    const input = sourceStateSchema.parse(raw);
    const corpus = this.one<{ value: string }>(
      "SELECT value FROM shard_meta WHERE key = 'corpus_id'",
    );
    if (corpus && corpus.value !== input.corpusId) {
      throw new ContextError("shard_identity_mismatch", "Corpus shard id does not match", 409);
    }
    const document = this.one<{ document_id: string }>(
      "SELECT document_id FROM documents WHERE document_id = ?",
      input.documentId,
    );
    if (!document) throw new ContextError("document_not_found", "document was not found", 404);
    this.sql.exec(
      `UPDATE documents SET
         source_state = ?,
         relative_path = COALESCE(?, relative_path),
         last_seen_at = ?,
         deleted_at = CASE WHEN ? = 'unavailable' THEN ? ELSE NULL END
       WHERE document_id = ?`,
      input.sourceState,
      input.relativePath?.normalize("NFC") ?? null,
      input.observedAt,
      input.sourceState,
      input.observedAt,
      input.documentId,
    );
    return {
      corpusId: input.corpusId,
      documentId: input.documentId,
      sourceState: input.sourceState,
      observedAt: input.observedAt,
    };
  }

  private search(raw: unknown): Record<string, unknown> {
    const value = raw as Record<string, unknown>;
    const query = typeof value.query === "string" ? value.query.trim() : "";
    const limit = Number.isInteger(value.limit) ? Number(value.limit) : 20;
    if (!query || query.length > 2_000 || limit < 1 || limit > 100) {
      throw new ContextError("invalid_query", "Corpus search parameters are invalid");
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
      throw new ContextError("invalid_read", "unitIds must contain 1 to 100 unit ids");
    }
    const neighborSpan = Number.isInteger(value.neighborSpan) ? Number(value.neighborSpan) : 0;
    if (neighborSpan < 0 || neighborSpan > 10) {
      throw new ContextError("invalid_read", "neighborSpan must be between 0 and 10");
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
          for (const neighbor of neighbors) selectedRows.set(neighbor.unit_id, neighbor);
        }
      }
    }
    for (const row of [...selectedRows.values()].sort(
      (left, right) =>
        left.projection_id.localeCompare(right.projection_id) || left.ordinal - right.ordinal,
    )) {
      results.push(unitResult(row));
    }
    return { count: results.length, units: results, missing_unit_ids: missing };
  }

  private inventory(): Record<string, unknown> {
    const documents = [
      ...this.sql.exec(
        `SELECT d.document_id, d.relative_path, d.extension, d.source_state,
                d.current_revision_id, d.last_seen_at, d.lifecycle_state,
                r.sha256, r.source_size, r.captured_at,
                p.projection_id, p.result_manifest_hash, p.completeness_state,
                p.assurance_state,
                (SELECT COUNT(*) FROM source_units u WHERE u.projection_id = p.projection_id)
                  AS unit_count
         FROM documents d
         LEFT JOIN revisions r ON r.revision_id = d.current_revision_id
         LEFT JOIN projections p ON p.revision_id = r.revision_id AND p.is_active = 1
         ORDER BY d.relative_path, d.document_id`,
      ),
    ] as unknown as Array<Record<string, unknown>>;
    const staged = this.one<{ count: number }>(
      "SELECT COUNT(*) AS count FROM staged_uploads",
    )?.count ?? 0;
    return { documents, staged_upload_count: staged };
  }

  async fetch(request: Request): Promise<Response> {
    try {
      if (request.method !== "POST") {
        throw new ContextError("method_not_allowed", "only POST is supported", 405);
      }
      const ownerId = request.headers.get("X-Owner-Id");
      if (!ownerId) throw new ContextError("missing_owner", "internal owner id is required", 401);
      const path = new URL(request.url).pathname;
      const body: unknown = await request.json();
      if (path === "/projection/begin") return json({ ok: true, result: this.begin(ownerId, body) });
      if (path === "/projection/units") {
        return json({ ok: true, result: await this.addUnits(body) });
      }
      if (path === "/projection/commit") return json({ ok: true, result: this.commit(body) });
      if (path === "/source-state") return json({ ok: true, result: this.sourceState(body) });
      if (path === "/search") return json({ ok: true, result: this.search(body) });
      if (path === "/units/read") return json({ ok: true, result: this.readUnits(body) });
      if (path === "/inventory") return json({ ok: true, result: this.inventory() });
      throw new ContextError("not_found", "Corpus shard route was not found", 404);
    } catch (error) {
      return errorResponse(error);
    }
  }
}
