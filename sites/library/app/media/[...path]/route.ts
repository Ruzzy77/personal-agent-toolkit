import { requireChatGPTApiUser } from '@/app/chatgpt-auth';
import { libraryApiError, libraryFetch } from '@/lib/library';

type RouteContext = { params: Promise<{ path: string[] }> };

async function media(
  request: Request,
  { params }: RouteContext,
): Promise<Response> {
  const unauthorized = await requireChatGPTApiUser();
  if (unauthorized) return unauthorized;
  try {
    const { path } = await params;
    const key = path.map(encodeURIComponent).join('/');
    const response = await libraryFetch(`/media/${key}`, {
      method: request.method,
    });
    const headers = new Headers();
    for (const name of ['content-type', 'etag', 'cache-control']) {
      const value = response.headers.get(name);
      if (value) headers.set(name, value);
    }
    return new Response(request.method === 'HEAD' ? null : response.body, {
      status: response.status,
      headers,
    });
  } catch (error) {
    return libraryApiError(error);
  }
}

export function GET(
  request: Request,
  context: RouteContext,
): Promise<Response> {
  return media(request, context);
}

export function HEAD(
  request: Request,
  context: RouteContext,
): Promise<Response> {
  return media(request, context);
}
