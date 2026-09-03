import type { AuthServiceBinding } from "@personal-agent/remote-runtime";

export type {
  AccessValidationResult,
  AuthenticatedOwner,
  AuthServiceBinding,
} from "@personal-agent/remote-runtime";

export type Lane = "today" | "direct" | "waiting" | "attention";
export type Resolution = "active" | "held" | "completed" | "canceled";
export type WeekStatus = "open" | "closed";
export type ActorKind = "owner" | "automation" | "source";
export type PeriodKind = "day" | "week" | "month" | "quarter" | "year";
export type Responsibility = "user" | "counterparty" | "system";

export interface Env {
  DB: D1Database;
  AUTH_SERVICE?: AuthServiceBinding;
  JOURNAL_RESOURCE: string;
  AUTH_ISSUER: string;
  ALLOWED_SITE_ORIGIN?: string;
  JOURNAL_SITE_TOKEN?: string;
  JOURNAL_INGEST_TOKEN?: string;
}

export interface Principal {
  kind: "owner" | "automation";
  id: string;
  scopes: ReadonlySet<string>;
  auth: "oauth" | "site-token" | "ingest-token";
}

export interface WeekRecord {
  id: string;
  startsOn: string;
  endsOn: string;
  timezone: "Asia/Seoul";
  status: WeekStatus;
  revision: number;
  createdAt: string;
  updatedAt: string;
  closedAt: string | null;
}

export interface ItemRecord {
  id: string;
  logicalItemId: string;
  weekId: string;
  sourceKind: string;
  sourceKey: string;
  sourceRef: string | null;
  sourceVersion: string | null;
  projectKey: string | null;
  title: string;
  summary: string;
  lane: Lane;
  resolution: Resolution;
  responsibility: Responsibility;
  dueAt: string | null;
  durableOutcome: string | null;
  corpusTargetSpace: string | null;
  version: number;
  createdAt: string;
  updatedAt: string;
}

export interface BoardSummary {
  today: number;
  direct: number;
  waiting: number;
  attention: number;
  completed: number;
  held: number;
  canceled: number;
}

export interface WeekFlowEntry {
  date: string;
  events: Array<{
    eventType: string;
    itemId: string | null;
    title: string | null;
    label: string;
    occurredAt: string;
  }>;
}

export interface BoardResult {
  week: WeekRecord;
  summary: BoardSummary;
  items: ItemRecord[];
  flow: WeekFlowEntry[];
}

export interface IngestItemInput {
  idempotencyKey: string;
  sourceKind: string;
  sourceKey: string;
  sourceRef: string | null;
  sourceVersion: string | null;
  weekId: string | null;
  projectKey: string | null;
  title: string;
  summary: string;
  lane: Lane;
  responsibility: Responsibility | null;
  dueAt: string | null;
  durableOutcome: string | null;
  corpusTargetSpace: string | null;
  occurredAt: string | null;
}

export interface IngestResult {
  item: ItemRecord;
  created: boolean;
  duplicate: boolean;
}

export interface ResolutionInput {
  resolution: Resolution;
  idempotencyKey: string;
  expectedVersion: number | null;
  occurredAt: string | null;
}

export interface WeekClosureSummary {
  weekId: string;
  counts: Record<Resolution, number>;
  laneCounts: Record<Lane, number>;
  projectCounts: Array<{ projectKey: string; count: number }>;
  completedTitles: string[];
  rolloverCount: number;
  rolloverTitles: string[];
}

export interface CorpusCandidate {
  itemId: string;
  projectKey: string;
  targetSpace: string;
  durableOutcome: string;
  contentHash: string;
  sourceRef: string | null;
}

export interface WeekClosure {
  week: WeekRecord;
  summary: WeekClosureSummary;
  corpusCandidates: CorpusCandidate[];
  alreadyClosed: boolean;
}

export interface WeekClosePreparation {
  week: WeekRecord;
  summary: WeekClosureSummary;
  corpusCandidates: CorpusCandidate[];
  rolloverItems: Array<{
    itemId: string;
    title: string;
    resolution: Resolution;
  }>;
  preparationVersion: string;
  reflectedCandidateIds: string[];
}

export interface JournalEventRecord {
  id: string;
  weekId: string;
  itemId: string | null;
  eventType: string;
  actorKind: string;
  actorRef: string | null;
  payload: Record<string, unknown>;
  label: string;
  occurredAt: string;
  createdAt: string;
}

export interface ItemSearchInput {
  weekId: string | null;
  startsOn: string | null;
  endsOn: string | null;
  query: string | null;
  projectKey: string | null;
  lane: Lane | null;
  resolution: Resolution | null;
  limit: number;
}

export interface ItemSearchResult {
  items: ItemRecord[];
  count: number;
}

export interface ItemDetailResult {
  item: ItemRecord;
  relatedItems: ItemRecord[];
  history: JournalEventRecord[];
  corrections: JournalEventRecord[];
}

export interface PeriodResult {
  kind: PeriodKind;
  anchor: string;
  startsOn: string;
  endsOn: string;
  weeks: WeekRecord[];
  totals: Record<Resolution, number>;
  lanes: Record<Lane, number>;
  projects: Array<{
    projectKey: string;
    total: number;
    completed: number;
    active: number;
  }>;
  highlights: Array<{
    itemId: string;
    weekId: string;
    title: string;
    projectKey: string | null;
    resolution: Resolution;
    durableOutcome: string | null;
  }>;
  longRunning: Array<{
    logicalItemId: string;
    title: string;
    projectKey: string | null;
    weekCount: number;
    latestWeekId: string;
    resolution: Resolution;
  }>;
  currentSummary: PeriodSummaryVersion | null;
  summaryVersions: PeriodSummaryVersion[];
}

export interface PeriodSummaryVersion {
  id: string;
  kind: PeriodKind;
  anchor: string;
  startsOn: string;
  endsOn: string;
  body: string;
  version: number;
  sourceEventIds: string[];
  createdBy: string;
  createdAt: string;
}

export interface SavePeriodSummaryInput {
  kind: PeriodKind;
  anchor: string;
  body: string;
  expectedVersion: number | null;
  idempotencyKey: string;
}

export interface PromotionReceiptInput {
  weekId: string;
  itemId: string | null;
  targetSpace: string;
  sourcePath: string;
  contentHash: string;
  status: "applied" | "skipped" | "failed";
  details: string | null;
  idempotencyKey: string;
  occurredAt: string | null;
}

export interface CorrectionInput {
  itemId: string | null;
  note: string;
  sourceRef: string | null;
  idempotencyKey: string;
  occurredAt: string | null;
}
