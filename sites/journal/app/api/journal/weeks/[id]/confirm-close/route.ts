import { journalRequest, type WeekClosureResult } from '@/lib/journal';

const WEEK_ID = /^\d{4}-\d{2}-\d{2}$/;

export async function POST(
  request: Request,
  context: { params: Promise<{ id: string }> },
) {
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
  let body: { preparationVersion?: unknown };
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
    typeof body.preparationVersion !== 'string' ||
    body.preparationVersion.length < 16
  ) {
    return Response.json(
      {
        ok: false,
        error: {
          code: 'invalid_preparation',
          message: '마감 준비를 다시 실행해 주세요.',
        },
      },
      { status: 400 },
    );
  }
  try {
    const result = await journalRequest<WeekClosureResult>(
      `/api/v1/weeks/${id}:confirm-close`,
      {
        method: 'POST',
        body: JSON.stringify({
          preparationVersion: body.preparationVersion,
          idempotencyKey: `site:close:${id}:${crypto.randomUUID()}`,
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
          code: 'week_close_failed',
          message:
            error instanceof Error
              ? error.message
              : '주간 기록을 마감하지 못했습니다.',
        },
      },
      { status: 409 },
    );
  }
}
