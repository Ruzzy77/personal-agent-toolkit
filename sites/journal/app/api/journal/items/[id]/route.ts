import { requireChatGPTApiUser } from '@/app/chatgpt-auth';
import { getItemDetail } from '@/lib/journal';

const ITEM_ID = /^[0-9a-f]{8}-[0-9a-f-]{27}$/i;

export async function GET(
  _request: Request,
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
  try {
    return Response.json({ ok: true, result: await getItemDetail(id) });
  } catch (error) {
    return Response.json(
      {
        ok: false,
        error: {
          code: 'item_detail_failed',
          message:
            error instanceof Error
              ? error.message
              : '항목을 불러오지 못했습니다.',
        },
      },
      { status: 404 },
    );
  }
}
