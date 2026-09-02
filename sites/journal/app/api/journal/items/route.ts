import {
  findItems,
  journalRequest,
  type IngestResult,
  type Lane,
  type Responsibility,
} from '@/lib/journal';

const LANES = new Set<Lane>(['today', 'direct', 'waiting', 'attention']);
const RESPONSIBILITIES = new Set<Responsibility>([
  'user',
  'counterparty',
  'system',
]);
const DATE = /^\d{4}-\d{2}-\d{2}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function optionalText(value: unknown, max: number): string | null | undefined {
  if (value === null || value === undefined || value === '') return null;
  if (typeof value !== 'string') return undefined;
  const trimmed = value.trim();
  return trimmed && trimmed.length <= max ? trimmed : undefined;
}

export async function GET(request: Request) {
  const input = new URL(request.url).searchParams;
  try {
    const result = await findItems({
      week: input.get('week') ?? undefined,
      startsOn: input.get('starts_on') ?? undefined,
      endsOn: input.get('ends_on') ?? undefined,
      query: input.get('query') ?? undefined,
      project: input.get('project') ?? undefined,
      lane: (input.get('lane') as Lane | null) ?? undefined,
      resolution:
        (input.get('resolution') as
          | 'active'
          | 'held'
          | 'completed'
          | 'canceled'
          | null) ?? undefined,
      limit: Number(input.get('limit') ?? '50'),
    });
    return Response.json({ ok: true, result });
  } catch (error) {
    return Response.json(
      {
        ok: false,
        error: {
          code: 'item_search_failed',
          message:
            error instanceof Error ? error.message : '항목을 찾지 못했습니다.',
        },
      },
      { status: 400 },
    );
  }
}

export async function POST(request: Request) {
  let body: Record<string, unknown>;
  try {
    body = (await request.json()) as Record<string, unknown>;
  } catch {
    return Response.json(
      {
        ok: false,
        error: { code: 'invalid_json', message: '요청을 읽지 못했습니다.' },
      },
      { status: 400 },
    );
  }

  const title = optionalText(body.title, 240);
  const summary = optionalText(body.summary, 1000);
  const projectKey = optionalText(body.projectKey, 120);
  const weekId = optionalText(body.weekId, 10);
  const clientRequestId = optionalText(body.clientRequestId, 64);
  const lane = body.lane;
  const responsibility = body.responsibility;
  if (
    !title ||
    !summary ||
    !clientRequestId ||
    !UUID.test(clientRequestId) ||
    typeof lane !== 'string' ||
    !LANES.has(lane as Lane) ||
    weekId === undefined ||
    (weekId !== null && !DATE.test(weekId)) ||
    projectKey === undefined ||
    (responsibility !== null &&
      responsibility !== undefined &&
      (typeof responsibility !== 'string' ||
        !RESPONSIBILITIES.has(responsibility as Responsibility)))
  ) {
    return Response.json(
      {
        ok: false,
        error: { code: 'invalid_item', message: '항목 내용이 올바르지 않습니다.' },
      },
      { status: 400 },
    );
  }

  try {
    const result = await journalRequest<IngestResult[]>(
      '/api/v1/items:ingest',
      {
        method: 'POST',
        body: JSON.stringify({
          items: [
            {
              idempotencyKey: `site:add:${clientRequestId}`,
              sourceKind: 'site',
              sourceKey: `journal-site:${clientRequestId}`,
              sourceRef: null,
              sourceVersion: clientRequestId,
              weekId,
              projectKey,
              title,
              summary,
              lane,
              responsibility: responsibility ?? null,
              dueAt: null,
              durableOutcome: null,
              corpusTargetSpace: null,
              occurredAt: null,
            },
          ],
        }),
      },
    );
    return Response.json({ ok: true, result: result[0] });
  } catch (error) {
    return Response.json(
      {
        ok: false,
        error: {
          code: 'item_add_failed',
          message:
            error instanceof Error ? error.message : '항목을 추가하지 못했습니다.',
        },
      },
      { status: 409 },
    );
  }
}
