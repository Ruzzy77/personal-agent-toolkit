import * as z from "zod/v4";

export const laneSchema = z.enum(["today", "direct", "waiting", "attention"]);
export const resolutionSchema = z.enum([
  "active",
  "held",
  "completed",
  "canceled",
]);
export const periodKindSchema = z.enum([
  "day",
  "week",
  "month",
  "quarter",
  "year",
]);

const nullableText = (max: number) =>
  z.string().trim().min(1).max(max).nullable().default(null);

export const ingestItemSchema = z.object({
  idempotencyKey: z.string().trim().min(8).max(240),
  sourceKind: z.string().trim().min(1).max(48),
  sourceKey: z.string().trim().min(1).max(240),
  sourceRef: nullableText(1000),
  sourceVersion: nullableText(240),
  weekId: z
    .string()
    .regex(/^\d{4}-\d{2}-\d{2}$/)
    .nullable()
    .default(null),
  projectKey: nullableText(120),
  title: z.string().trim().min(1).max(240),
  summary: z.string().trim().min(1).max(1000),
  lane: laneSchema,
  dueAt: nullableText(48),
  durableOutcome: nullableText(1000),
  corpusTargetSpace: nullableText(120),
  occurredAt: nullableText(48),
});

export const ingestRequestSchema = z.object({
  items: z.array(ingestItemSchema).min(1).max(100),
});

export const resolutionRequestSchema = z.object({
  resolution: resolutionSchema,
  idempotencyKey: z.string().trim().min(8).max(240),
  expectedVersion: z.number().int().positive().nullable().default(null),
  occurredAt: nullableText(48),
});

export const closeWeekRequestSchema = z.object({
  idempotencyKey: z.string().trim().min(8).max(240),
  occurredAt: nullableText(48),
});

export const correctionRequestSchema = z.object({
  note: z.string().trim().min(1).max(2000),
  sourceRef: nullableText(1000),
  idempotencyKey: z.string().trim().min(8).max(240),
  occurredAt: nullableText(48),
});

export const promotionRequestSchema = z.object({
  weekId: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
  itemId: z.string().uuid().nullable().default(null),
  targetSpace: z.string().trim().min(1).max(120),
  sourcePath: z.string().trim().min(1).max(1000),
  contentHash: z.string().trim().min(8).max(160),
  status: z.enum(["applied", "skipped", "failed"]),
  details: nullableText(1000),
  idempotencyKey: z.string().trim().min(8).max(240),
  occurredAt: nullableText(48),
});

export const getBoardToolSchema = z.object({
  weekId: z
    .string()
    .regex(/^\d{4}-\d{2}-\d{2}$/)
    .nullable()
    .default(null)
    .describe("KST Monday date; omit for the current week"),
  includeResolved: z.boolean().default(false),
});

export const setResolutionToolSchema = z.object({
  itemId: z.string().uuid(),
  resolution: resolutionSchema,
  idempotencyKey: z.string().trim().min(8).max(240),
  expectedVersion: z.number().int().positive().nullable().default(null),
  occurredAt: nullableText(48),
});

export const closeWeekToolSchema = z.object({
  weekId: z
    .string()
    .regex(/^\d{4}-\d{2}-\d{2}$/)
    .nullable()
    .default(null),
  idempotencyKey: z.string().trim().min(8).max(240),
  occurredAt: nullableText(48),
});

export const periodToolSchema = z.object({
  kind: periodKindSchema,
  anchor: z
    .string()
    .regex(/^\d{4}-\d{2}-\d{2}$/)
    .nullable()
    .default(null),
});
