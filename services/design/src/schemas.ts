import { z } from "zod/v4";

export const recipeIdSchema = z
  .string()
  .regex(/^[a-z0-9][a-z0-9-]{0,63}$/);

export const designPathSchema = z
  .string()
  .min(1)
  .max(500)
  .refine(
    (value) =>
      !value.startsWith("/")
      && !value.split("/").includes("..")
      && !value.includes("\\")
      && /^[a-zA-Z0-9._/-]+$/.test(value),
    "asset path is invalid",
  );

export const recipeMetadataSchema = z.looseObject({
  id: recipeIdSchema,
  name: z.string().min(1).max(200),
  description: z.string().min(1).max(2000),
  version: z.string().min(1).max(80),
  status: z.enum(["draft", "candidate", "validated", "deprecated"]),
  selection_ready: z.boolean().default(false),
  kind: z.literal("recipe"),
  visibility: z.literal("private"),
  pattern_refs: z.array(recipeIdSchema).max(100).default([]),
  formats: z.array(z.enum(["web", "document", "slides", "image"])).min(1),
});

export const listRecipesSchema = z.object({
  status: z.enum(["draft", "candidate", "validated", "deprecated"]).optional(),
  format: z.enum(["web", "document", "slides", "image"]).optional(),
  limit: z.number().int().min(1).max(100).default(50),
});

export const readRecipeSchema = z.object({ id: recipeIdSchema });

export const readFileSchema = z.object({
  id: recipeIdSchema,
  path: designPathSchema,
  encoding: z.enum(["utf8", "base64"]).default("utf8"),
});

export const createRecipeSchema = z.object({
  recipe: recipeMetadataSchema,
});

export const updateRecipeSchema = z.object({
  id: recipeIdSchema,
  expected_revision: z.number().int().min(1),
  recipe: recipeMetadataSchema,
});

export const uploadFileSchema = z.object({
  id: recipeIdSchema,
  path: designPathSchema,
  content_type: z.string().min(1).max(200),
  base64: z.string().min(1).max(16_000_000),
  expected_file_revision: z.number().int().min(0),
});

export const importFileSchema = z.object({
  path: designPathSchema,
  content_type: z.string().min(1).max(200),
  base64: z.string().min(1).max(16_000_000),
  sha256: z.string().regex(/^[a-f0-9]{64}$/),
});

export const importRecipeSchema = z.object({
  library: z.record(z.string(), z.unknown()),
  patterns: z.array(z.record(z.string(), z.unknown())).min(1),
  recipe: recipeMetadataSchema,
  files: z.array(importFileSchema).min(1).max(200),
});

const fileSummaryOutputSchema = z.looseObject({
  path: z.string(),
  contentType: z.string(),
  byteSize: z.number().int().min(0),
  sha256: z.string(),
  revision: z.number().int().min(1),
  updatedAt: z.string(),
});

const recipeSummaryOutputSchema = z.looseObject({
  id: z.string(),
  name: z.string(),
  description: z.string(),
  recipeVersion: z.string(),
  status: z.enum(["draft", "candidate", "validated", "deprecated"]),
  selectionReady: z.boolean(),
  revision: z.number().int().min(1),
  updatedAt: z.string(),
});

export const listRecipesOutputSchema = z.looseObject({
  recipes: z.array(recipeSummaryOutputSchema),
});

export const readRecipeOutputSchema = recipeSummaryOutputSchema.extend({
  metadata: z.record(z.string(), z.unknown()),
  files: z.array(fileSummaryOutputSchema),
  createdAt: z.string(),
});

export const readFileOutputSchema = z.looseObject({
  id: z.string(),
  path: z.string(),
  content_type: z.string(),
  encoding: z.enum(["utf8", "base64"]),
  content: z.string(),
  sha256: z.string(),
  file_revision: z.number().int().min(1),
});

export const mutationOutputSchema = z.looseObject({
  status: z.enum(["created", "updated", "unchanged"]),
  id: z.string(),
  version: z.string(),
  revision: z.number().int().min(1),
  updated_at: z.string(),
});

export const uploadFileOutputSchema = z.looseObject({
  status: z.literal("stored"),
  id: z.string(),
  path: z.string(),
  sha256: z.string(),
  file_revision: z.number().int().min(1),
  bytes: z.number().int().min(1),
});

export type DesignCreateInput = z.infer<typeof createRecipeSchema>;
export type DesignImportInput = z.infer<typeof importRecipeSchema>;
export type DesignUpdateInput = z.infer<typeof updateRecipeSchema>;
export type DesignUploadInput = z.infer<typeof uploadFileSchema>;
