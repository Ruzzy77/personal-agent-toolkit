import { requireChatGPTApiUser } from '@/app/chatgpt-auth';
import {
  journalRequest,
  type PeriodKind,
  type PeriodSummaryVersion,
} from '@/lib/journal';

const PERIODS = new Set<PeriodKind>(['week', 'month', 'quarter', 'year']);
const DATE = /^\d{4}-\d{2}-\d{2}$/;

export async function POST(request: Request) {
  const authError = await requireChatGPTApiUser();
  if (authError) return authError;

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
  const kind = body.kind as PeriodKind;
  const anchor = typeof body.anchor === 'string' ? body.anchor : '';
  const summary = typeof body.body === 'string' ? body.body.trim() : '';
  const expectedVersion = body.expectedVersion;
  if (
    !PERIODS.has(kind) ||
    !DATE.test(anchor) ||
    !summary ||
    summary.length > 5000 ||
    (expectedVersion !== null &&
      (!Number.isInteger(expectedVersion) || Number(expectedVersion) < 1))
  ) {
    return Response.json(
      {
        ok: false,
        error: {
          code: 'invalid_summary',
          message: '요약 내용이 올바르지 않습니다.',
        },
      },
      { status: 400 },
    );
  }
  try {
    const result = await journalRequest<{
      summary: PeriodSummaryVersion;
      duplicate: boolean;
    }>('/api/v1/period-summaries', {
      method: 'POST',
      body: JSON.stringify({
        kind,
        anchor,
        body: summary,
        expectedVersion,
        idempotencyKey: `site:period-summary:${kind}:${anchor}:${crypto.randomUUID()}`,
      }),
    });
    return Response.json({ ok: true, result });
  } catch (error) {
    return Response.json(
      {
        ok: false,
        error: {
          code: 'period_summary_failed',
          message:
            error instanceof Error
              ? error.message
              : '요약을 저장하지 못했습니다.',
        },
      },
      { status: 409 },
    );
  }
}
