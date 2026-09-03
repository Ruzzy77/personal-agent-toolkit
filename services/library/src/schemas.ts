import { z } from "zod/v4";

export const issueIdSchema = z
  .string()
  .regex(/^(daily|digest|research):\d{4}-\d{2}-\d{2}(?::(?:[01]\d|2[0-3]))?$/);

export const newIssueIdSchema = z
  .string()
  .regex(/^(daily|digest|research):\d{4}-\d{2}-\d{2}:(?:[01]\d|2[0-3])$/);

export const sourceHtmlSchema = z
  .string()
  .min(1)
  .max(2_000_000)
  .refine(
    (value) =>
      /<!doctype html>/i.test(value)
      && /<h1\b/i.test(value)
      && /<article\b/i.test(value),
    "완전한 발간호 HTML이 필요합니다.",
  )
  .describe(
    "제목 h1, 도입문 .lead 또는 .standfirst, article을 포함한 완전한 HTML입니다. "
      + "새 발간호는 저장할 때 색인 발간호 템플릿으로 정규화됩니다.",
  );

export const listIssuesSchema = z.object({
  collection: z.enum(["daily", "digest", "research"]).optional(),
  limit: z.number().int().min(1).max(200).default(20),
});

export const readIssueSchema = z.object({
  id: issueIdSchema,
  format: z.enum(["text", "source_html"]).default("text"),
});

export const updateIssueBodySchema = z.object({
  source_html: sourceHtmlSchema,
  expected_version: z.number().int().min(1),
  cover_path: z.string().max(500).optional(),
  references: z.array(z.string().max(1000)).max(100).optional(),
});

export const updateIssueSchema = updateIssueBodySchema.extend({
  id: issueIdSchema,
});

export const createIssueSchema = z.object({
  id: newIssueIdSchema,
  published_at: z.string().max(80).optional(),
  references: z.array(z.string().max(1000)).max(100).default([]),
  source_html: sourceHtmlSchema,
  cover_path: z.string().max(500).optional(),
});

export const importIssueSchema = z.object({
  id: issueIdSchema,
  collection: z.enum(["daily", "digest", "research"]),
  date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
  published_at: z.string().min(1).max(80),
  title: z.string().min(1).max(300),
  references: z.array(z.string().max(1000)).max(100).default([]),
  canonical_path: z.string().startsWith("/editions/").max(1000),
  text: z.string().max(2_000_000),
  source_html: sourceHtmlSchema,
  cover_path: z.string().max(500).nullable().default(null),
  updated_at: z.string().min(1).max(80),
});

export const updateIssueFragmentsSchema = z.object({
  title: z.string().min(1).max(300),
  lead_text: z.string().max(3000).optional(),
  article_html: z.string().max(2_000_000),
  expected_version: z.number().int().min(1),
});

export const uploadAssetSchema = z.object({
  path: z
    .string()
    .regex(/^[a-zA-Z0-9/_-]+\.(png|jpe?g|webp|gif|avif)$/i)
    .max(500),
  content_type: z.enum([
    "image/avif",
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/webp",
  ]),
  base64: z.string().min(1).max(14_000_000),
});

const issueSummaryOutputSchema = z.looseObject({
  id: z.string(),
  collection: z.enum(["daily", "digest", "research"]),
  date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
  publishedAt: z.string(),
  title: z.string(),
  canonicalPath: z.string(),
  coverPath: z.string().nullable(),
  version: z.number().int().min(1),
  updatedAt: z.string(),
});

export const whoamiOutputSchema = z.looseObject({
  authenticated: z.literal(true),
  provider: z.literal("google"),
  email_hint: z.string().nullable(),
  scopes: z.array(z.string()),
  resource: z.string(),
});

export const listIssuesOutputSchema = z.looseObject({
  issues: z.array(issueSummaryOutputSchema),
});

export const readIssueOutputSchema = issueSummaryOutputSchema.extend({
  references: z.array(z.string()),
  format: z.enum(["text", "source_html"]),
  content: z.string(),
});

export const mutationOutputSchema = z.looseObject({
  status: z.enum(["created", "updated", "unchanged"]),
  id: z.string(),
  title: z.string(),
  version: z.number().int().min(1),
  updated_at: z.string(),
  canonical_path: z.string(),
});

export const uploadAssetOutputSchema = z.looseObject({
  status: z.literal("stored"),
  path: z.string(),
  bytes: z.number().int().min(1),
});

export type LibraryCreateInput = z.infer<typeof createIssueSchema>;
export type LibraryFragmentUpdateInput = z.infer<
  typeof updateIssueFragmentsSchema
>;
export type LibraryImportInput = z.infer<typeof importIssueSchema>;
export type LibraryUpdateInput = z.infer<typeof updateIssueBodySchema>;
