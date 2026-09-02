export type ResourceKind = "sense" | "corpus" | "hypes";

export interface AuthenticatedOwner {
  userId: string;
  provider: "google";
  subject: string;
  email?: string;
  displayName?: string;
  resource: string;
  scopes: string[];
  clientId: string;
  expiresAt: number;
}

export type AccessValidationResult =
  | { ok: true; owner: AuthenticatedOwner }
  | {
      ok: false;
      code:
        | "invalid_token"
        | "invalid_target"
        | "invalid_scope"
        | "insufficient_scope";
      status: 401 | 403 | 500;
      requiredScopes?: string[];
    };

export interface AuthServiceBinding {
  validateAccessToken(
    token: string,
    resource: string,
    requiredScopes: string[],
  ): Promise<AccessValidationResult>;
}

export interface Env {
  STATE_DB: D1Database;
  CORPUS_SHARDS: DurableObjectNamespace;
  SYNC_BROKERS: DurableObjectNamespace;
  AUTH_SERVICE?: AuthServiceBinding;
  DOCUMENT_ANALYZER?: Fetcher;
  AUTH_ISSUER: string;
  SENSE_RESOURCE: string;
  CORPUS_RESOURCE: string;
  HYPES_RESOURCE: string;
  STRUCTURE_PATH_COMPACTION_WRITE_ENABLED?: string;
  SYNC_DEVICE_TOKEN?: string;
  SYNC_DEVICE_ID?: string;
  SYNC_DEVICE_TOKENS_JSON?: string;
  SYNC_OWNER_ID?: string;
}

export interface Principal {
  ownerId: string;
  scopes: ReadonlySet<string>;
  clientId: string;
  auth: "oauth" | "sync-device";
  deviceId?: string;
}

export interface ProfileSection {
  id: string;
  purpose: string;
  text: string;
  origins: Array<"user_set" | "learned_from_results">;
  sensitivity: "ordinary" | "sensitive";
}

export interface SenseProfile {
  schema_version: 2;
  sections: ProfileSection[];
}

export interface SectionSkill {
  name: string;
  description: string;
  instructions: string;
  version: string;
  updated_at: string;
}

export interface CorpusUnitInput {
  unitId: string;
  ordinal: number;
  unitType: string;
  structurePath: Record<string, unknown>;
  sourceAnchor: Record<string, unknown>;
  content: string;
  contentSha256: string;
  previousUnitId: string | null;
  nextUnitId: string | null;
  extractionIssues: unknown[];
  derivationMethod: string;
  geometry: Record<string, unknown>;
  confidence: number | null;
  ocr: boolean;
  qualityFlags: string[];
}

export interface ProjectionBeginInput {
  uploadId: string;
  corpusId: string;
  document: {
    documentId: string;
    relativePath: string;
    extension: string;
    sourceState: string;
    mediaType: string | null;
    logicalSize: number | null;
    modifiedNs: string | null;
    residencyState: string;
    eligibilityState: string;
    lifecycleState: "active" | "archived" | "trash";
    retentionClass: string;
    lastUserAccessAt: string | null;
    deletedAt: string | null;
  };
  revision: {
    revisionId: string;
    sha256: string;
    sourceSize: number;
    capturedAt: string;
    predecessorRevisionId: string | null;
    makeCurrent: boolean;
  };
  projection: {
    projectionId: string;
    adapterId: string;
    adapterVersion: string;
    configHash: string;
    resultManifestHash: string;
    completenessState: "complete" | "partial";
    coverage: Record<string, unknown>;
    capabilityManifest: Record<string, unknown>;
    issues: unknown[];
    assuranceState: string;
    declaredUnitCount: number;
    activate: boolean;
    createdAt: string | null;
  };
}

export interface SyncJobRequest {
  jobId: string;
  operation: string;
  scope: Record<string, unknown>;
  request: Record<string, unknown>;
  maximumResponseBytes: number;
  expiresAt: string;
}
