import { z } from "zod/v4";

const sectionId = z
  .string()
  .min(1)
  .max(64)
  .regex(/^[a-z0-9]+(?:-[a-z0-9]+)*$/);
const sha256 = z.string().regex(/^[0-9a-f]{64}$/);
const persistentNodeRef = z.string().regex(/^node_[0-9a-f]{32}$/);
const persistentPredicateRef = z.string().regex(/^pred_[0-9a-f]{32}$/);
const persistentEdgeRef = z.string().regex(/^edge_[0-9a-f]{32}$/);
const temporaryRef = z.string().regex(/^\$[a-z][a-z0-9._-]{0,63}$/);
const nodeRef = z.union([persistentNodeRef, temporaryRef]);
const predicateRef = z.union([persistentPredicateRef, temporaryRef]);
const edgeRef = z.union([persistentEdgeRef, temporaryRef]);

export const profileSectionSchema = z
  .object({
    id: sectionId,
    purpose: z.string().min(1).max(320),
    text: z.string().min(1).max(12_000),
    origins: z
      .array(z.enum(["user_set", "learned_from_results"]))
      .min(1)
      .max(2)
      .refine((values) => new Set(values).size === values.length),
    sensitivity: z.enum(["ordinary", "sensitive"]).default("ordinary"),
  })
  .strict();

export const senseProfileSchema = z
  .object({
    schema_version: z.literal(2),
    sections: z
      .array(profileSectionSchema)
      .min(1)
      .max(24)
      .refine(
        (sections) =>
          new Set(sections.map((section) => section.id)).size ===
          sections.length,
        "section ids must be unique",
      ),
  })
  .strict();

export const senseReadSchema = z
  .object({
    view: z.enum(["index", "sections", "full"]).default("index"),
    section_ids: z.array(sectionId).max(24).nullable().optional(),
  })
  .strict();

export const senseReviseSchema = z
  .object({
    changes: z
      .array(
        z
          .object({
            section_id: sectionId,
            previous_section_sha256: sha256,
            new_section: profileSectionSchema,
          })
          .strict()
          .refine((value) => value.section_id === value.new_section.id),
      )
      .min(1)
      .max(12),
  })
  .strict();

export const senseSkillReviseSchema = z
  .object({
    section_id: sectionId,
    expected_version: z.string().min(1).max(160),
    new_skill: z
      .object({
        name: z.string().min(1).max(160),
        description: z.string().min(1).max(1000),
        instructions: z.string().min(1).max(100_000),
      })
      .strict(),
  })
  .strict();

function jsonDepth(value: unknown): number {
  if (Array.isArray(value)) {
    return 1 + Math.max(0, ...value.map(jsonDepth));
  }
  if (value !== null && typeof value === "object") {
    return 1 + Math.max(0, ...Object.values(value).map(jsonDepth));
  }
  return 0;
}

const boundedJsonObject = z
  .record(z.string(), z.json())
  .refine((value) => jsonDepth(value) <= 8, "JSON nesting exceeds eight levels")
  .refine(
    (value) => new TextEncoder().encode(JSON.stringify(value)).length <= 65_536,
    "JSON object exceeds 64 KiB",
  );

export const nodeInputSchema = z
  .object({
    labels: z.array(z.string().min(1).max(120)).max(32).default([]),
    name: z.string().min(1).max(240),
    description: z.string().min(1).max(2000).nullable().optional(),
    aliases: z.array(z.string().min(1).max(240)).max(64).default([]),
    attributes: boundedJsonObject.default({}),
  })
  .strict();

export const predicateInputSchema = z
  .object({
    name: z.string().min(1).max(240),
    description: z.string().min(1).max(2000).nullable().optional(),
    aliases: z.array(z.string().min(1).max(240)).max(64).default([]),
  })
  .strict();

export const edgeInputSchema = z
  .object({
    source_ref: nodeRef,
    predicate_ref: predicateRef,
    target_ref: nodeRef,
    qualifiers: boundedJsonObject.default({}),
  })
  .strict();

const rewriteOperationSchema = z.discriminatedUnion("op", [
  z
    .object({ op: z.literal("put_node"), ref: nodeRef, value: nodeInputSchema })
    .strict(),
  z
    .object({
      op: z.literal("put_predicate"),
      ref: predicateRef,
      value: predicateInputSchema,
    })
    .strict(),
  z
    .object({ op: z.literal("put_edge"), ref: edgeRef, value: edgeInputSchema })
    .strict(),
  z
    .object({
      op: z.literal("delete"),
      ref: z.union([
        persistentNodeRef,
        persistentPredicateRef,
        persistentEdgeRef,
      ]),
    })
    .strict(),
]);

export const hypesRewriteSchema = z
  .object({ operations: z.array(rewriteOperationSchema).min(1).max(128) })
  .strict();

export const hypesReadSchema = z
  .object({
    focus: z.string().min(1).max(400).nullable().optional(),
    seed_refs: z
      .array(z.union([persistentNodeRef, persistentPredicateRef]))
      .max(64)
      .nullable()
      .optional(),
    continuation: z.string().max(400).nullable().optional(),
    limit: z.number().int().min(1).max(200).default(50),
    max_hops: z.number().int().min(0).max(2).default(1),
  })
  .strict();

export const projectionBeginSchema = z
  .object({
    uploadId: z.string().regex(/^upload_[0-9a-f]{32}$/),
    corpusId: z.string().min(1).max(128),
    document: z
      .object({
        documentId: z.string().min(1).max(128),
        relativePath: z.string().min(1).max(4096),
        extension: z.string().max(512),
        sourceState: z.string().min(1).max(64),
        mediaType: z.string().max(200).nullable().default(null),
        logicalSize: z
          .number()
          .int()
          .min(0)
          .max(Number.MAX_SAFE_INTEGER)
          .nullable()
          .default(null),
        modifiedNs: z
          .string()
          .regex(/^[0-9]{1,30}$/)
          .nullable()
          .default(null),
        residencyState: z.string().min(1).max(64).default("unknown"),
        eligibilityState: z.string().min(1).max(64).default("supported"),
        lifecycleState: z
          .enum(["active", "archived", "trash"])
          .default("active"),
        retentionClass: z.string().min(1).max(64).default("managed"),
        lastUserAccessAt: z.string().datetime().nullable().default(null),
        deletedAt: z.string().datetime().nullable().default(null),
      })
      .strict(),
    revision: z
      .object({
        revisionId: z.string().min(1).max(160),
        sha256,
        sourceSize: z.number().int().min(0).max(1_073_741_824),
        capturedAt: z.string().datetime(),
        predecessorRevisionId: z
          .string()
          .min(1)
          .max(160)
          .nullable()
          .default(null),
        makeCurrent: z.boolean().default(true),
      })
      .strict(),
    projection: z
      .object({
        projectionId: z.string().min(1).max(192),
        adapterId: z.string().min(1).max(192),
        adapterVersion: z.string().min(1).max(128),
        configHash: sha256,
        resultManifestHash: sha256,
        completenessState: z.enum(["complete", "partial"]),
        coverage: boundedJsonObject,
        capabilityManifest: boundedJsonObject,
        issues: z.array(z.json()).max(10_000).default([]),
        assuranceState: z.string().min(1).max(64),
        declaredUnitCount: z.number().int().min(0).max(2_000_000),
        activate: z.boolean().default(true),
        createdAt: z.string().datetime().nullable().default(null),
      })
      .strict(),
  })
  .strict();

export const revisionResolveSchema = z
  .object({
    corpusId: z.string().min(1).max(128),
    documentId: z.string().min(1).max(128),
    sha256,
    sourceSize: z.number().int().min(0).max(1_073_741_824),
  })
  .strict();

export const corpusUnitSchema = z
  .object({
    unitId: z.string().min(1).max(192),
    ordinal: z.number().int().min(1),
    unitType: z.string().min(1).max(64),
    structurePath: boundedJsonObject,
    sourceAnchor: boundedJsonObject,
    content: z.string().max(1_500_000),
    contentSha256: sha256,
    previousUnitId: z.string().max(192).nullable(),
    nextUnitId: z.string().max(192).nullable(),
    extractionIssues: z.array(z.json()).max(1000),
    derivationMethod: z.string().min(1).max(64),
    geometry: boundedJsonObject,
    confidence: z.number().min(0).max(1).nullable(),
    ocr: z.boolean(),
    qualityFlags: z.array(z.string().min(1).max(120)).max(128),
  })
  .strict();

export const projectionUnitsSchema = z
  .object({
    uploadId: z.string().regex(/^upload_[0-9a-f]{32}$/),
    units: z.array(corpusUnitSchema).min(1).max(500),
  })
  .strict();

export const corpusDocumentsImportSchema = z
  .object({
    corpusId: z.string().min(1).max(128),
    documents: z
      .array(
        z
          .object({
            documentId: z.string().min(1).max(128),
            relativePath: z.string().min(1).max(4096),
            extension: z.string().max(512),
            sourceState: z.enum([
              "unknown",
              "available",
              "changed",
              "partially_available",
              "unavailable",
            ]),
            mediaType: z.string().max(200).nullable(),
            logicalSize: z.number().int().min(0).max(Number.MAX_SAFE_INTEGER),
            modifiedNs: z.string().regex(/^[0-9]{1,30}$/),
            residencyState: z.string().min(1).max(64),
            eligibilityState: z.string().min(1).max(64),
            currentRevisionId: z.string().min(1).max(160).nullable(),
            lifecycleState: z.enum(["active", "archived", "trash"]),
            retentionClass: z.string().min(1).max(64),
            lastUserAccessAt: z.string().datetime().nullable(),
            firstSeenAt: z.string().datetime(),
            lastSeenAt: z.string().datetime(),
            deletedAt: z.string().datetime().nullable(),
          })
          .strict(),
      )
      .min(1)
      .max(500),
  })
  .strict();

export const corpusExternalImportSchema = z
  .object({
    corpusId: z.string().min(1).max(128),
    bindings: z
      .array(
        z
          .object({
            bindingId: z.string().min(1).max(192),
            providerKind: z.string().min(1).max(120),
            selector: boundedJsonObject,
            state: z.enum(["active", "archived"]),
            lastCompleteRunId: z.string().min(1).max(192).nullable(),
            lastCompleteAt: z.string().datetime().nullable(),
            createdAt: z.string().datetime(),
            updatedAt: z.string().datetime(),
          })
          .strict(),
      )
      .max(100),
    runs: z
      .array(
        z
          .object({
            runId: z.string().min(1).max(192),
            bindingId: z.string().min(1).max(192),
            baseCompleteRunId: z.string().min(1).max(192).nullable(),
            status: z.enum(["incomplete", "complete"]),
            startedAt: z.string().datetime(),
            completedAt: z.string().datetime().nullable(),
            supersededAt: z.string().datetime().nullable(),
          })
          .strict(),
      )
      .max(10_000),
    records: z
      .array(
        z
          .object({
            sourceRecordId: z.string().min(1).max(192),
            bindingId: z.string().min(1).max(192),
            externalId: z.string().min(1).max(4096),
            parentExternalId: z.string().max(4096).nullable(),
            occurredAt: z.string().datetime().nullable(),
            title: z.string().max(20_000).nullable(),
            participants: z.array(z.json()).max(2000),
            labelIds: z.array(z.string().max(1000)).max(2000),
            attachments: z.array(z.json()).max(2000),
            providerMetadata: boundedJsonObject,
            locator: boundedJsonObject,
            freshnessIdentity: z.string().max(4096).nullable(),
            metadataSha256: sha256,
            membershipState: z.enum(["active", "removed"]),
            lastSeenRunId: z.string().min(1).max(192),
            firstSeenAt: z.string().datetime(),
            lastSeenAt: z.string().datetime(),
          })
          .strict(),
      )
      .max(10_000),
  })
  .strict();

export const projectionCommitSchema = z
  .object({
    uploadId: z.string().regex(/^upload_[0-9a-f]{32}$/),
    expectedUnitCount: z.number().int().min(0).max(2_000_000),
    expectedManifestHash: sha256,
  })
  .strict();

export const sourceStateSchema = z
  .object({
    corpusId: z.string().min(1).max(128),
    documentId: z.string().min(1).max(128),
    sourceState: z.enum([
      "unknown",
      "available",
      "changed",
      "partially_available",
      "unavailable",
    ]),
    observedAt: z.string().datetime(),
    relativePath: z.string().min(1).max(4096).optional(),
    logicalSize: z
      .number()
      .int()
      .min(0)
      .max(Number.MAX_SAFE_INTEGER)
      .optional(),
    modifiedNs: z.string().regex(/^[0-9]{1,30}$/).optional(),
    residencyState: z.string().min(1).max(64).optional(),
    eligibilityState: z.string().min(1).max(64).optional(),
  })
  .strict();

export const syncHelloSchema = z
  .object({
    type: z.literal("hello"),
    protocolVersion: z.literal(1),
    displayName: z.string().min(1).max(160),
    capabilities: z.array(z.string().min(1).max(120)).max(64),
  })
  .strict();

export const syncResultSchema = z
  .object({
    type: z.literal("job_result"),
    jobId: z.string().regex(/^job_[0-9a-f]{32}$/),
    ok: z.boolean(),
    result: z.record(z.string(), z.json()).optional(),
    error: z
      .object({
        code: z.string().min(1).max(120),
        message: z.string().max(1000),
      })
      .strict()
      .optional(),
  })
  .strict()
  .refine((value) =>
    value.ok ? value.result !== undefined : value.error !== undefined,
  );

const spaceId = z
  .string()
  .min(1)
  .max(64)
  .regex(/^[a-z0-9][a-z0-9._-]{0,63}$/);
const connectionId = spaceId;
const relativePath = z.string().min(1).max(4096);
const spaceReference = z.string().min(7).max(8192);
const versionToken = z.string().min(4).max(1000);

export const corpusSpaceListSchema = z
  .object({
    limit: z.number().int().min(1).max(100).default(100),
    offset: z.number().int().min(0).max(10_000).default(0),
  })
  .strict();

export const corpusSpaceGetSchema = z
  .object({
    space_id: spaceId,
    context_limit: z.number().int().min(1).max(100).default(100),
    context_offset: z.number().int().min(0).max(10_000).default(0),
  })
  .strict();

export const corpusContextItemsReviseSchema = z
  .object({
    space_id: spaceId,
    expected_version: z.number().int().min(1).max(Number.MAX_SAFE_INTEGER),
    revisions: z
      .array(
        z
          .object({
            item_id: z.string().min(1).max(200),
            kind: z.enum([
              "finding",
              "relationship",
              "difference",
              "question",
              "gap",
            ]),
            body_text: z.string().min(1).max(12_000),
            status: z.string().min(1).max(200),
          })
          .strict(),
      )
      .min(1)
      .max(20),
  })
  .strict();

export const corpusContextSkillReviseSchema = z
  .object({
    space_id: spaceId,
    expected_version: z.string().min(6).max(128),
    new_skill: z
      .object({
        name: z
          .string()
          .min(1)
          .max(64)
          .regex(/^[a-z0-9]+(?:-[a-z0-9]+)*$/),
        description: z.string().min(1).max(1000),
        instructions: z.string().min(1).max(24_000),
      })
      .strict(),
  })
  .strict();

export const corpusSpaceSearchSchema = z
  .object({
    space_id: spaceId,
    query: z.string().min(1).max(2000),
    connection_id: connectionId.nullable().optional(),
    limit: z.number().int().min(1).max(200).default(20),
  })
  .strict();

export const corpusSourceRefreshSchema = z
  .object({
    space_id: spaceId,
    connection_id: connectionId.nullable().optional(),
    document_id: z.string().regex(/^doc_[0-9a-f]{32}$/),
    expected_revision_sha256: sha256.nullable().optional(),
  })
  .strict();

export const corpusJobStatusSchema = z
  .object({
    job_id: z.string().regex(/^job_[0-9a-f]{32}$/),
  })
  .strict();

export const corpusFileListSchema = z
  .object({
    space_id: spaceId,
    connection_id: connectionId.nullable().optional(),
    mode: z.enum(["list_directory", "find"]).default("list_directory"),
    relative_path: relativePath.nullable().optional(),
    query: z.string().max(1000).nullable().optional(),
    cursor: spaceReference.nullable().optional(),
    limit: z.number().int().min(1).max(200).default(100),
  })
  .strict();

export const corpusFileReadSchema = z
  .object({
    space_id: spaceId,
    connection_id: connectionId.nullable().optional(),
    relative_path: relativePath.nullable().optional(),
    read_ref: spaceReference.nullable().optional(),
    encoding: z.enum(["utf8", "base64"]).default("utf8"),
    max_bytes: z
      .number()
      .int()
      .min(1)
      .max(2 * 1024 * 1024)
      .default(2 * 1024 * 1024),
    neighbor_span: z.number().int().min(0).max(10).default(0),
    include_structure_context: z.boolean().default(false),
    max_chars: z.number().int().min(1000).max(200_000).default(30_000),
    start_char: z
      .number()
      .int()
      .min(0)
      .max(2 * 1024 * 1024)
      .default(0),
  })
  .strict()
  .refine(
    (value) =>
      Number(value.relative_path != null) + Number(value.read_ref != null) <= 1,
    "select at most one of relative_path and read_ref",
  );

export const corpusFileWriteSchema = z
  .object({
    space_id: spaceId,
    relative_path: relativePath,
    content: z.string().max(6 * 1024 * 1024),
    content_encoding: z.enum(["utf8", "base64"]),
    expected_version: versionToken,
    connection_id: connectionId.nullable().optional(),
    replace_start_marker: z.string().min(1).max(4096).nullable().optional(),
    replace_end_marker: z.string().min(1).max(4096).nullable().optional(),
    make_current: z.boolean().default(false),
  })
  .strict();

export const corpusFileDeleteSchema = z
  .object({
    space_id: spaceId,
    relative_path: relativePath,
    expected_version: versionToken,
    confirm_delete: z.boolean(),
    connection_id: connectionId.nullable().optional(),
  })
  .strict();

export const corpusFileSelectSchema = z
  .object({
    space_id: spaceId,
    relative_path: relativePath,
    connection_id: connectionId.nullable().optional(),
  })
  .strict();

export const corpusFileRestoreSchema = z
  .object({
    space_id: spaceId,
    recovery_id: z.string().regex(/^wrec_[0-9a-f]{32}$/),
    expected_version: versionToken,
    connection_id: connectionId.nullable().optional(),
  })
  .strict();

const isoTimestamp = z.string().datetime();
const migrationId = z.string().min(1).max(200);

export const corpusMetadataImportSchema = z
  .object({
    schemaVersion: z.literal(1),
    sourceDigest: sha256,
    sourceSchemaVersion: z.number().int().min(1).max(10_000),
    spaces: z
      .array(
        z
          .object({
            spaceId,
            displayName: z.string().min(1).max(300),
            state: z.enum(["active", "archived"]),
            accessScope: z.enum(["remote_allowed", "local_only"]),
            primaryWorkConnectionId: connectionId.nullable(),
            updatedAt: isoTimestamp,
          })
          .strict(),
      )
      .max(10_000),
    contexts: z
      .array(
        z
          .object({
            spaceId,
            title: z.string().min(1).max(1000),
            purpose: z.string().min(1).max(4000),
            scope: boundedJsonObject,
            version: z.number().int().min(1).max(Number.MAX_SAFE_INTEGER),
            updatedAt: isoTimestamp,
            items: z
              .array(
                z
                  .object({
                    itemId: migrationId,
                    kind: z.enum([
                      "finding",
                      "relationship",
                      "difference",
                      "question",
                      "gap",
                    ]),
                    bodyText: z.string().min(1).max(12_000),
                    attributes: boundedJsonObject,
                    disclosureState: z
                      .string()
                      .min(1)
                      .max(120)
                      .default("restricted"),
                    lifecycleState: z
                      .string()
                      .min(1)
                      .max(120)
                      .default("active"),
                    supersedesItemId: migrationId.nullable().default(null),
                    createdAt: isoTimestamp,
                  })
                  .strict(),
              )
              .max(100_000),
            sources: z
              .array(
                z
                  .object({
                    sourceRefId: migrationId,
                    itemId: migrationId,
                    corpusId: migrationId.nullable().default(null),
                    snapshotId: migrationId.nullable().default(null),
                    documentId: migrationId.nullable().default(null),
                    revisionId: migrationId.nullable().default(null),
                    projectionId: migrationId.nullable().default(null),
                    sourceUnitId: migrationId.nullable().default(null),
                    providerKind: z.string().max(120).nullable().default(null),
                    providerRecordId: z
                      .string()
                      .max(1000)
                      .nullable()
                      .default(null),
                    linkRole: z.string().min(1).max(120),
                    sourceSpan: boundedJsonObject,
                  })
                  .strict(),
              )
              .max(200_000),
            skill: z
              .object({
                name: z.string().min(1).max(160),
                description: z.string().min(1).max(1000),
                instructions: z.string().min(1).max(100_000),
                version: z.string().min(1).max(200),
                updatedAt: isoTimestamp,
              })
              .strict()
              .nullable()
              .default(null),
          })
          .strict(),
      )
      .max(10_000),
    connections: z
      .array(
        z
          .object({
            spaceId,
            connectionId,
            displayName: z.string().min(1).max(300),
            roles: z
              .array(z.enum(["source", "work"]))
              .min(1)
              .max(2),
            accessScope: z.enum(["remote_allowed", "local_only"]),
            permission: z.enum(["read_only", "read_write"]),
            indexMode: z.enum(["indexed", "not_indexed"]),
            corpusId: migrationId.nullable().default(null),
            deviceId: z.string().min(1).max(64).nullable().default(null),
            localConnectionKey: z
              .string()
              .min(1)
              .max(500)
              .nullable()
              .default(null),
            generation: z.number().int().min(1),
            configurationState: z.string().min(1).max(120),
            sourceState: z.string().max(120).nullable().default(null),
            recordState: z.string().max(120).nullable().default(null),
            capturedAt: isoTimestamp.nullable().default(null),
            updatedAt: isoTimestamp,
          })
          .strict(),
      )
      .max(50_000),
    currentFiles: z
      .array(
        z
          .object({
            spaceId,
            connectionId,
            relativePath,
            versionToken: z.string().max(1000).nullable().default(null),
            state: z.string().min(1).max(120),
            reason: z.string().max(1000).nullable().default(null),
            residencyState: z.string().max(120).nullable().default(null),
            size: z.number().int().min(0).nullable().default(null),
            modifiedNs: z.string().max(40).nullable().default(null),
            updatedAt: isoTimestamp,
          })
          .strict(),
      )
      .max(50_000),
    devices: z
      .array(
        z
          .object({
            deviceId: z
              .string()
              .min(1)
              .max(64)
              .regex(/^[a-z0-9][a-z0-9._-]{0,63}$/),
            displayName: z.string().min(1).max(160),
            status: z.enum(["active", "revoked"]).default("active"),
            capabilities: z
              .array(z.string().min(1).max(120))
              .max(64)
              .default([]),
            createdAt: isoTimestamp,
            updatedAt: isoTimestamp,
          })
          .strict(),
      )
      .max(100),
  })
  .strict();
