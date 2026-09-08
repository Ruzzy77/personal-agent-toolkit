import { z } from "zod/v4";

const spaceId = z.string().min(1).max(64).regex(/^[a-z0-9][a-z0-9._-]{0,63}$/);
const identifier = z.string().min(1).max(200).regex(/^[A-Za-z0-9][A-Za-z0-9._:@-]*$/);
const version = z.number().int().min(1).max(Number.MAX_SAFE_INTEGER - 1);
const relativePath = z.string().min(1).max(4096).refine((value) =>
  !value.startsWith("/") && !value.startsWith("~") && !value.includes("\\") &&
  !/^[A-Za-z]:/.test(value) && !value.includes("\0") &&
  !value.split("/").some((part) => part === ".." || part === "." || !part),
  "Only project-relative paths are accepted",
);
const labels = z.array(z.string().min(1).max(200)).max(32).default([]);
const scope = z.record(z.string().max(100), z.unknown()).refine(
  (value) => new TextEncoder().encode(JSON.stringify(value)).byteLength <= 16_384,
  "Scope exceeds its metadata budget",
);

export const corpusDocumentApplicabilitySchema = z.object({
  activities: labels,
  targets: labels,
  topics: labels,
  paths: z.array(relativePath).max(32).default([]),
}).strict();

export const corpusDocumentSourceRefSchema = z.object({
  connection_id: spaceId,
  document_id: identifier,
  revision_id: identifier,
  projection_id: identifier,
  unit_id: identifier,
  link_role: z.string().min(1).max(100).default("evidence"),
}).strict();

const documentFields = {
  title: z.string().min(1).max(500),
  body_markdown: z.string().min(1).max(524_288).describe(
    "Complete Markdown. The service enforces a 512 KiB UTF-8 byte budget and never truncates it.",
  ),
  kind: z.enum(["context", "guidance"]).default("context"),
  applicability: corpusDocumentApplicabilitySchema.default({ activities: [], targets: [], topics: [], paths: [] }),
  guidance_approval: z.object({
    explicit_user_approval: z.literal(true),
    basis: z.string().min(1).max(2000),
  }).strict().nullable().optional(),
  source_refs: z.array(corpusDocumentSourceRefSchema).max(32).default([]),
  migration_provenance: z.object({
    // This explicit pair plus matching protected source_refs marks only those
    // exact Source revision/projections historical. A later Source revision
    // remains searchable; ordinary citations and previous snapshots never hide it.
    source_id: spaceId.optional().describe("The Source Connection ID in this Space, not a corpus ID or local path."),
    document_id: identifier.optional().describe("The exact Source document ID migrated to this native document; source_id and matching exact source_refs are required for historical search classification."),
    relative_path: relativePath.optional().describe("Descriptive project-relative migration origin; never used to identify or hide a Source document."),
  }).strict().nullable().optional(),
};

function approvalMatchesKind(value: { kind: string; guidance_approval?: unknown }): boolean {
  return value.kind === "guidance" ? Boolean(value.guidance_approval) : !value.guidance_approval;
}

export const corpusSpaceCreateSchema = z.object({
  space_id: spaceId,
  display_name: z.string().min(1).max(500),
  purpose: z.string().max(4000).default(""),
  scope: scope.default({}),
}).strict();

export const corpusWorkspaceBindSchema = z.object({
  workspace_id: identifier,
  space_id: spaceId,
  environment_kind: z.enum(["local", "remote"]),
  host_id: identifier,
  project_id: identifier.nullable().optional(),
  expected_version: z.union([z.literal("absent"), version]),
}).strict();

export const corpusWorkspaceResolveSchema = z.object({
  workspace_id: identifier,
  host_id: identifier,
}).strict();

export const corpusDocumentCreateSchema = z.object({
  space_id: spaceId,
  document_id: identifier,
  ...documentFields,
}).strict().refine(approvalMatchesKind, "Guidance requires explicit user approval; Context cannot claim guidance approval");

export const corpusDocumentReviseSchema = z.object({
  space_id: spaceId,
  document_id: identifier,
  expected_version: version,
  ...documentFields,
}).strict().refine(approvalMatchesKind, "Guidance requires explicit user approval; Context cannot claim guidance approval");

export const corpusDocumentRestoreSchema = z.object({
  space_id: spaceId,
  document_id: identifier,
  expected_version: version,
}).strict();

export const corpusDocumentReadSchema = z.object({
  space_id: spaceId,
  document_id: identifier,
  snapshot: z.enum(["current", "previous"]).default("current"),
  expected_version: version.optional(),
  start_char: z.number().int().min(0).max(524_288).default(0),
  max_chars: z.number().int().min(1).max(200_000).default(30_000),
}).strict();

export const corpusDocumentListSchema = z.object({
  space_id: spaceId,
  kind: z.enum(["context", "guidance"]).optional(),
  limit: z.number().int().min(1).max(200).default(50),
  offset: z.number().int().min(0).max(100_000).default(0),
}).strict();

export const corpusContextSearchSchema = z.object({
  space_id: spaceId,
  query: z.string().trim().min(1).max(2000),
  limit: z.number().int().min(1).max(200).default(20),
  offset: z.number().int().min(0).max(100_000).default(0),
}).strict();

export type CorpusDocumentInput = z.infer<typeof corpusDocumentCreateSchema>;
export type CorpusDocumentSourceRef = z.infer<typeof corpusDocumentSourceRefSchema>;
