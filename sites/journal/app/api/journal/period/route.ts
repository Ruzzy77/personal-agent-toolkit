import { requireChatGPTApiUser } from '@/app/chatgpt-auth';
import { getPeriod, type PeriodKind } from '@/lib/journal';

const PERIODS = new Set<PeriodKind>(['week', 'month', 'quarter', 'year']);
const DATE = /^\d{4}-\d{2}-\d{2}$/;

export async function GET(request: Request) {
  const authError = await requireChatGPTApiUser();
  if (authError) return authError;

  const params = new URL(request.url).searchParams;
  const kind = (params.get('kind') ?? 'week') as PeriodKind;
  const anchor = params.get('anchor') ?? '';
  if (!PERIODS.has(kind) || !DATE.test(anchor)) {
    return Response.json(
      {
        ok: false,
        error: { code: 'invalid_period', message: '기간이 올바르지 않습니다.' },
      },
      { status: 400 },
    );
  }
  try {
    return Response.json({ ok: true, result: await getPeriod(kind, anchor) });
  } catch (error) {
    return Response.json(
      {
        ok: false,
        error: {
          code: 'period_failed',
          message:
            error instanceof Error
              ? error.message
              : '기간 기록을 불러오지 못했습니다.',
        },
      },
      { status: 502 },
    );
  }
}
