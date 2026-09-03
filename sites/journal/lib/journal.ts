import {
  serviceRequest,
  ServiceRequestError,
} from "@personal-agent/site-runtime";

export { ServiceRequestError as JournalRequestError };

export type Lane = 'today' | 'direct' | 'waiting' | 'attention';
export type Resolution = 'active' | 'held' | 'completed' | 'canceled';
export type Responsibility = 'user' | 'counterparty' | 'system';
export type PeriodKind = 'week' | 'month' | 'quarter' | 'year';

export type JournalWeek = {
  id: string;
  startsOn: string;
  endsOn: string;
  timezone: 'Asia/Seoul';
  status: 'open' | 'closed';
  revision: number;
  createdAt: string;
  updatedAt: string;
  closedAt: string | null;
};

export type JournalItem = {
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
};

export type BoardSummary = Record<Lane, number> & {
  completed: number;
  held: number;
  canceled: number;
};

export type WeekFlowEntry = {
  date: string;
  events: Array<{
    eventType: string;
    itemId: string | null;
    title: string | null;
    label: string;
    occurredAt: string;
  }>;
};

export type BoardResult = {
  week: JournalWeek;
  summary: BoardSummary;
  items: JournalItem[];
  flow: WeekFlowEntry[];
};

export type JournalEvent = {
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
};

export type ItemDetailResult = {
  item: JournalItem;
  relatedItems: JournalItem[];
  history: JournalEvent[];
  corrections: JournalEvent[];
};

export type ItemSearchResult = {
  items: JournalItem[];
  count: number;
};

export type IngestResult = {
  item: JournalItem;
  created: boolean;
  duplicate: boolean;
};

export type CorpusCandidate = {
  itemId: string;
  projectKey: string;
  targetSpace: string;
  durableOutcome: string;
  contentHash: string;
  sourceRef: string | null;
};

export type WeekClosePreparation = {
  week: JournalWeek;
  summary: {
    rolloverCount: number;
    completedTitles: string[];
  } & Record<string, unknown>;
  corpusCandidates: CorpusCandidate[];
  rolloverItems: Array<{
    itemId: string;
    title: string;
    resolution: Resolution;
  }>;
  preparationVersion: string;
  reflectedCandidateIds: string[];
};

export type WeekClosureResult = {
  week: JournalWeek;
  summary: { rolloverCount: number } & Record<string, unknown>;
  corpusCandidates: CorpusCandidate[];
  alreadyClosed: boolean;
};

export type PeriodResult = {
  kind: PeriodKind;
  anchor: string;
  startsOn: string;
  endsOn: string;
  weeks: JournalWeek[];
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
};

export type PeriodSummaryVersion = {
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
};

function serviceConfiguration(): { url: string; token: string } {
  const url = process.env.JOURNAL_SERVICE_URL?.replace(/\/$/, '');
  const token = process.env.JOURNAL_SITE_TOKEN;
  if (!url || !token) {
    throw new Error('Journal service is not configured');
  }
  return { url, token };
}

export async function journalRequest<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const { url, token } = serviceConfiguration();
  return serviceRequest<T>({
    baseUrl: url,
    token,
    path,
    serviceName: "journal",
    init,
  });
}

export function getBoard(week?: string): Promise<BoardResult> {
  const params = new URLSearchParams({ include_resolved: 'true' });
  if (week) params.set('week', week);
  return journalRequest<BoardResult>(`/api/v1/board?${params}`);
}

export function getPeriod(
  kind: PeriodKind,
  anchor: string,
): Promise<PeriodResult> {
  const params = new URLSearchParams({ kind, anchor });
  return journalRequest<PeriodResult>(`/api/v1/period?${params}`);
}

export function findItems(params: {
  week?: string;
  startsOn?: string;
  endsOn?: string;
  query?: string;
  project?: string;
  lane?: Lane;
  resolution?: Resolution;
  limit?: number;
}): Promise<ItemSearchResult> {
  const search = new URLSearchParams();
  if (params.week) search.set('week', params.week);
  if (params.startsOn) search.set('starts_on', params.startsOn);
  if (params.endsOn) search.set('ends_on', params.endsOn);
  if (params.query) search.set('query', params.query);
  if (params.project) search.set('project', params.project);
  if (params.lane) search.set('lane', params.lane);
  if (params.resolution) search.set('resolution', params.resolution);
  search.set('limit', String(params.limit ?? 50));
  return journalRequest<ItemSearchResult>(`/api/v1/items?${search}`);
}

export function getItemDetail(itemId: string): Promise<ItemDetailResult> {
  return journalRequest<ItemDetailResult>(
    `/api/v1/items/${encodeURIComponent(itemId)}`,
  );
}

export function emptyBoard(weekId: string): BoardResult {
  const start = new Date(`${weekId}T00:00:00+09:00`);
  const end = new Date(start.getTime() + 6 * 86_400_000);
  const endsOn = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Seoul',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(end);
  const now = new Date().toISOString();
  return {
    week: {
      id: weekId,
      startsOn: weekId,
      endsOn,
      timezone: 'Asia/Seoul',
      status: 'open',
      revision: 0,
      createdAt: now,
      updatedAt: now,
      closedAt: null,
    },
    summary: {
      today: 0,
      direct: 0,
      waiting: 0,
      attention: 0,
      completed: 0,
      held: 0,
      canceled: 0,
    },
    items: [],
    flow: [],
  };
}

export function currentKstDate(): string {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Seoul',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(new Date());
}

export function weekIdForDate(date: string): string {
  const base = new Date(`${date}T12:00:00+09:00`);
  const weekday = new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Seoul',
    weekday: 'short',
  }).format(base);
  const offset = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].indexOf(
    weekday,
  );
  const monday = new Date(base.getTime() - Math.max(offset, 0) * 86_400_000);
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Seoul',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(monday);
}
