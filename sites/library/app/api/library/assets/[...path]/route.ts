import { requireChatGPTApiUser } from '@/app/chatgpt-auth';
import { libraryApiError, libraryFetch } from '@/lib/library';

type RouteContext = { params: Promise<{ path: string[] }> };

export async function PUT(
  request: Request,
  { params }: RouteContext,
): Promise<Response> {
  const unauthorized = await requireChatGPTApiUser();
  if (unauthorized) return unauthorized;
  try {
    const { path } = await params;
    const key = path.map(encodeURIComponent).join('/');
    const response = await libraryFetch(`/api/v1/assets/${key}`, {
      method: 'PUT',
      headers: { 'Content-Type': request.headers.get('Content-Type') ?? '' },
      body: await request.arrayBuffer(),
    });
    return new Response(response.body, {
      status: response.status,
      headers: { 'Content-Type': 'application/json; charset=utf-8' },
    });
  } catch (error) {
    return libraryApiError(error);
  }
}
