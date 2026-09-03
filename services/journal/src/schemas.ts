import * as z from "zod/v4";

export const laneSchema = z.enum(["today", "direct", "waiting", "attention"]);
export const resolutionSchema = z.enum([
  "active",
  "held",
  "completed",
  "canceled",
]);
export const responsibilitySchema = z.enum([
  "user",
  "counterparty",
  "system",
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

const corpusReceiptSourcePath = z
  .string()
  .trim()
  .min(1)
  .max(1000)
  .refine(
    (value) => {
      if (
        value.startsWith("/") ||
        value.startsWith("\\") ||
        value.startsWith("~") ||
        /^[A-Za-z][A-Za-z0-9+.-]*:/.test(value) ||
        /^[A-Za-z]:[\\/]/.test(value)
      ) {
        return false;
      }
      return !value.split(/[\\/]+/).includes("..");
    },
    "sourcePath must be project-root-relative or a non-path receipt label",
  )
  .describe(
    "A project-root-relative Corpus Source or Work path, or a non-path receipt label; never a local absolute path.",
  );

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
  responsibility: responsibilitySchema.nullable().default(null),
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
  preparationVersion: z.string().trim().min(16).max(160),
  occurredAt: nullableText(48),
});

export const correctionRequestSchema = z.object({
  itemId: z.string().uuid().nullable().default(null),
  note: z.string().trim().min(1).max(2000),
  sourceRef: nullableText(1000),
  idempotencyKey: z.string().trim().min(8).max(240),
  occurredAt: nullableText(48),
});

export const promotionRequestSchema = z.object({
  weekId: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
  itemId: z.string().uuid().nullable().default(null),
  targetSpace: z.string().trim().min(1).max(120),
  sourcePath: corpusReceiptSourcePath,
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

export const findItemsSchema = z.object({
  weekId: z
    .string()
    .regex(/^\d{4}-\d{2}-\d{2}$/)
    .nullable()
    .default(null),
  startsOn: z
    .string()
    .regex(/^\d{4}-\d{2}-\d{2}$/)
    .nullable()
    .default(null),
  endsOn: z
    .string()
    .regex(/^\d{4}-\d{2}-\d{2}$/)
    .nullable()
    .default(null),
  query: nullableText(240),
  projectKey: nullableText(120),
  lane: laneSchema.nullable().default(null),
  resolution: resolutionSchema.nullable().default(null),
  limit: z.number().int().min(1).max(200).default(50),
});

export const getItemHistorySchema = z.object({
  itemId: z.string().uuid(),
});

export const prepareWeekCloseSchema = z.object({
  weekId: z
    .string()
    .regex(/^\d{4}-\d{2}-\d{2}$/)
    .nullable()
    .default(null),
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
  preparationVersion: z.string().trim().min(16).max(160),
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

export const savePeriodSummarySchema = z.object({
  kind: periodKindSchema,
  anchor: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
  body: z.string().trim().min(1).max(5000),
  expectedVersion: z.number().int().min(1).nullable().default(null),
  idempotencyKey: z.string().trim().min(8).max(240),
});

const dateOutputSchema = z.string().regex(/^\d{4}-\d{2}-\d{2}$/);
const openOutputObject = () => z.looseObject({});

const weekOutputSchema = z.looseObject({
  id: dateOutputSchema,
  status: z.enum(["open", "closed"]),
  revision: z.number().int().min(1),
});

const itemOutputSchema = z.looseObject({
  id: z.string().uuid(),
  weekId: dateOutputSchema,
  title: z.string(),
  lane: laneSchema,
  resolution: resolutionSchema,
  version: z.number().int().min(1),
});

const eventOutputSchema = z.looseObject({
  id: z.string(),
  eventType: z.string(),
  occurredAt: z.string(),
});

const periodSummaryOutputSchema = z.looseObject({
  id: z.string(),
  kind: periodKindSchema,
  anchor: dateOutputSchema,
  body: z.string(),
  version: z.number().int().min(1),
});

export const getBoardOutputSchema = z.looseObject({
  week: weekOutputSchema,
  summary: openOutputObject(),
  items: z.array(itemOutputSchema),
  flow: z.array(openOutputObject()),
});

export const ingestOutputSchema = z.looseObject({
  result: z.array(
    z.looseObject({
      item: itemOutputSchema,
      created: z.boolean(),
      duplicate: z.boolean(),
    }),
  ),
});

export const findItemsOutputSchema = z.looseObject({
  items: z.array(itemOutputSchema),
  count: z.number().int().min(0),
});

export const itemHistoryOutputSchema = z.looseObject({
  item: itemOutputSchema,
  relatedItems: z.array(itemOutputSchema),
  history: z.array(eventOutputSchema),
  corrections: z.array(eventOutputSchema),
});

export const resolutionOutputSchema = z.looseObject({
  item: itemOutputSchema,
  duplicate: z.boolean(),
});

export const weekClosePreparationOutputSchema = z.looseObject({
  week: weekOutputSchema,
  summary: openOutputObject(),
  corpusCandidates: z.array(openOutputObject()),
  rolloverItems: z.array(openOutputObject()),
  preparationVersion: z.string(),
  reflectedCandidateIds: z.array(z.string()),
});

export const weekCloseOutputSchema = z.looseObject({
  week: weekOutputSchema,
  summary: openOutputObject(),
  corpusCandidates: z.array(openOutputObject()),
  alreadyClosed: z.boolean(),
});

export const correctionOutputSchema = z.looseObject({
  eventId: z.string(),
  duplicate: z.boolean(),
});

export const periodOutputSchema = z.looseObject({
  kind: periodKindSchema,
  anchor: dateOutputSchema,
  startsOn: dateOutputSchema,
  endsOn: dateOutputSchema,
  weeks: z.array(weekOutputSchema),
  totals: openOutputObject(),
  lanes: openOutputObject(),
  projects: z.array(openOutputObject()),
  highlights: z.array(openOutputObject()),
  longRunning: z.array(openOutputObject()),
  currentSummary: periodSummaryOutputSchema.nullable(),
  summaryVersions: z.array(periodSummaryOutputSchema),
});

export const promotionOutputSchema = z.looseObject({
  receiptId: z.string(),
  duplicate: z.boolean(),
});

export const periodSummarySaveOutputSchema = z.looseObject({
  summary: periodSummaryOutputSchema,
  duplicate: z.boolean(),
});
