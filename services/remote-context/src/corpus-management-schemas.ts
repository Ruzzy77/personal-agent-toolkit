import { z } from "zod/v4";

export const managementId = z
  .string()
  .min(1)
  .max(200)
  .regex(/^[A-Za-z0-9][A-Za-z0-9._:@-]*$/);
const space = z
  .string()
  .min(1)
  .max(64)
  .regex(/^[a-z0-9][a-z0-9._-]*$/);
const version = z
  .number()
  .int()
  .min(1)
  .max(Number.MAX_SAFE_INTEGER - 1);
const key = z.string().min(1).max(200);
const token = z.string().regex(/^impact-v1:[a-f0-9]{64}$/);
const commit = { impact_token: token, idempotency_key: key };
export const targetSchema = z
  .object({
    kind: z.enum(["document", "context_item", "context_skill", "space"]),
    space_id: space,
    id: managementId,
  })
  .strict();
export const corpusManagementPreviewSchema = z
  .object({
    action: z.enum(["trash", "restore", "purge"]),
    target: targetSchema.optional(),
    deletion_group_id: managementId.optional(),
  })
  .strict()
  .refine((v) =>
    v.action === "trash"
      ? Boolean(v.target) && !v.deletion_group_id
      : Boolean(v.deletion_group_id) && !v.target,
  );
export const corpusDocumentMoveSchema = z
  .object({
    space_id: space,
    document_id: managementId,
    destination_space_id: space,
    destination_document_id: managementId.optional(),
    expected_version: version,
    expected_source_version: version,
    expected_destination_version: version,
    idempotency_key: key,
  })
  .strict();
export const corpusContextItemMoveSchema = z
  .object({
    space_id: space,
    item_id: managementId,
    destination_space_id: space,
    expected_version: version,
    expected_destination_version: version,
    idempotency_key: key,
  })
  .strict();
export const corpusDocumentTrashSchema = z
  .object({
    space_id: space,
    document_id: managementId,
    expected_version: version,
    ...commit,
  })
  .strict();
export const corpusContextItemTrashSchema = z
  .object({
    space_id: space,
    item_id: managementId,
    expected_version: version,
    ...commit,
  })
  .strict();
export const corpusContextSkillTrashSchema = z
  .object({ space_id: space, expected_version: z.string().min(1), ...commit })
  .strict();
export const corpusSpaceTrashSchema = z
  .object({ space_id: space, expected_version: version, ...commit })
  .strict();
export const corpusTrashListSchema = z
  .object({
    space_id: space.optional(),
    state: z
      .enum(["trashed", "blocked", "purging", "purged", "restored", "all"])
      .default("trashed"),
    limit: z.number().int().min(1).max(50).default(20),
    offset: z.number().int().min(0).max(100000).default(0),
  })
  .strict();
export const corpusTrashRestoreSchema = z
  .object({
    deletion_group_id: managementId,
    expected_version: version,
    ...commit,
  })
  .strict();
export const corpusTrashPurgeSchema = corpusTrashRestoreSchema.extend({
  confirm_permanent_delete: z.literal(true),
});
export const corpusOperationStatusSchema = z
  .object({ idempotency_key: key })
  .strict();
export const corpusSpaceReviseSchema = z
  .object({
    space_id: space,
    expected_version: version,
    display_name: z.string().min(1).max(500),
    purpose: z.string().max(4000),
    scope: z
      .record(z.string().max(100), z.unknown())
      .refine((v) => JSON.stringify(v).length <= 16384),
    state: z.enum(["active", "archived"]).default("active"),
  })
  .strict();
export const corpusContextItemCreateSchema = z
  .object({
    space_id: space,
    item_id: managementId,
    expected_version: version,
    kind: z.enum(["finding", "relationship", "difference", "question", "gap"]),
    body_text: z.string().min(1).max(30000),
    status: z.string().max(100).optional(),
  })
  .strict();
