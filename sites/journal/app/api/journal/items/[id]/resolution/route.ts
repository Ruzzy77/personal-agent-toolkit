import { requireChatGPTApiUser } from '@/app/chatgpt-auth';
import {
  journalRequest,
  type JournalItem,
  type Resolution,
} from '@/lib/journal';

const RESOLUTIONS = new Set<Resolution>([
  'active',
  'held',
  'completed',
  'canceled',
]);
const ITEM_ID = /^[0-9a-f]{8}-[0-9a-f-]{27}$/i;

type ResolutionResult = { item: JournalItem; duplicate: boolean };

export async function PATCH(
  request: Request,
  context: { params: Promise<{ id: string }> },
) {
  const authError = await requireChatGPTApiUser();
  if (authError) return authError;

  const { id } = await context.params;
  if (!ITEM_ID.test(id)) {
    return Response.json(
      {
        ok: false,
        error: { code: 'invalid_item', message: '항목이 올바르지 않습니다.' },
      },
      { status: 400 },
    );
  }

  let body: { resolution?: unknown; expectedVersion?: unknown };
  try {
    body = (await request.json()) as typeof body;
  } catch {
    return Response.json(
      {
        ok: false,
        error: { code: 'invalid_json', message: '요청을 읽지 못했습니다.' },
      },
      { status: 400 },
    );
  }
  if (
    typeof body.resolution !== 'string' ||
    !RESOLUTIONS.has(body.resolution as Resolution) ||
    (body.expectedVersion !== undefined &&
      (!Number.isInteger(body.expectedVersion) ||
        Number(body.expectedVersion) < 1))
  ) {
    return Response.json(
      {
        ok: false,
        error: {
          code: 'invalid_resolution',
          message: '처리 상태가 올바르지 않습니다.',
        },
      },
      { status: 400 },
    );
  }

  try {
    const result = await journalRequest<ResolutionResult>(
      `/api/v1/items/${id}/resolution`,
      {
        method: 'PATCH',
        body: JSON.stringify({
          resolution: body.resolution,
          expectedVersion:
            typeof body.expectedVersion === 'number'
              ? body.expectedVersion
              : null,
          idempotencyKey: `site:${id}:${crypto.randomUUID()}`,
          occurredAt: null,
        }),
      },
    );
    return Response.json({ ok: true, result });
  } catch (error) {
    return Response.json(
      {
        ok: false,
        error: {
          code: 'resolution_failed',
          message:
            error instanceof Error
              ? error.message
              : '상태를 바꾸지 못했습니다.',
        },
      },
      { status: 409 },
    );
  }
}
