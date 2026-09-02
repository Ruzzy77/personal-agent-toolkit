import { journalRequest, type WeekClosePreparation } from '@/lib/journal';

const WEEK_ID = /^\d{4}-\d{2}-\d{2}$/;

export async function POST(
  _request: Request,
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
  try {
    const result = await journalRequest<WeekClosePreparation>(
      `/api/v1/weeks/${id}:prepare-close`,
      { method: 'POST' },
    );
    return Response.json({ ok: true, result });
  } catch (error) {
    return Response.json(
      {
        ok: false,
        error: {
          code: 'week_close_prepare_failed',
          message:
            error instanceof Error
              ? error.message
              : '주간 마감을 준비하지 못했습니다.',
        },
      },
      { status: 409 },
    );
  }
}
