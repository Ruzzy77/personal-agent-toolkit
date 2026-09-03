import { requireChatGPTApiUser } from '@/app/chatgpt-auth';
import { journalRequest } from '@/lib/journal';

const WEEK_ID = /^\d{4}-\d{2}-\d{2}$/;
const ITEM_ID = /^[0-9a-f]{8}-[0-9a-f-]{27}$/i;

export async function POST(
  request: Request,
  context: { params: Promise<{ id: string }> },
) {
  const authError = await requireChatGPTApiUser();
  if (authError) return authError;

  const { id } = await context.params;
  if (!WEEK_ID.test(id)) {
    return Response.json(
      {
        ok: false,
        error: { code: 'invalid_week', message: '주차가 올바르지 않습니다.' },
      },
      { status: 400 },
    );
  }
  let body: { itemId?: unknown; note?: unknown; sourceRef?: unknown };
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
  const note = typeof body.note === 'string' ? body.note.trim() : '';
  const itemId = typeof body.itemId === 'string' ? body.itemId : null;
  const sourceRef =
    typeof body.sourceRef === 'string' && body.sourceRef.trim()
      ? body.sourceRef.trim()
      : null;
  if (
    !note ||
    note.length > 2000 ||
    !itemId ||
    !ITEM_ID.test(itemId) ||
    (sourceRef && sourceRef.length > 500)
  ) {
    return Response.json(
      {
        ok: false,
        error: {
          code: 'invalid_correction',
          message: '정정 내용이 올바르지 않습니다.',
        },
      },
      { status: 400 },
    );
  }
  try {
    const result = await journalRequest<{
      eventId: string;
      duplicate: boolean;
    }>(`/api/v1/weeks/${id}/corrections`, {
      method: 'POST',
      body: JSON.stringify({
        itemId,
        note,
        sourceRef,
        idempotencyKey: `site:correction:${itemId}:${crypto.randomUUID()}`,
        occurredAt: null,
      }),
    });
    return Response.json({ ok: true, result });
  } catch (error) {
    return Response.json(
      {
        ok: false,
        error: {
          code: 'correction_failed',
          message:
            error instanceof Error
              ? error.message
              : '정정 기록을 추가하지 못했습니다.',
        },
      },
      { status: 409 },
    );
  }
}
