import { getBoard } from '@/lib/journal';

const DATE = /^\d{4}-\d{2}-\d{2}$/;

export async function GET(request: Request) {
  const week = new URL(request.url).searchParams.get('week') ?? undefined;
  if (week && !DATE.test(week)) {
    return Response.json(
      {
        ok: false,
        error: { code: 'invalid_week', message: '주차가 올바르지 않습니다.' },
      },
      { status: 400 },
    );
  }
  try {
    return Response.json({ ok: true, result: await getBoard(week) });
  } catch (error) {
    return Response.json(
      {
        ok: false,
        error: {
          code: 'journal_unavailable',
          message:
            error instanceof Error
              ? error.message
              : 'Journal을 불러오지 못했습니다.',
        },
      },
      { status: 502 },
    );
  }
}
