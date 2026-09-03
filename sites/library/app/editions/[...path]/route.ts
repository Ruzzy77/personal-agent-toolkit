import { requireChatGPTApiUser } from '@/app/chatgpt-auth';
import { issueHtmlResponse } from '@/lib/issue-page';
import {
  libraryApiError,
  libraryRequest,
  type LibraryIssue,
} from '@/lib/library';

type RouteContext = { params: Promise<{ path: string[] }> };

async function issue(
  request: Request,
  { params }: RouteContext,
): Promise<Response> {
  const { path } = await params;
  const pathname = `/editions/${path.map(encodeURIComponent).join('/')}`;
  const unauthorized = await requireChatGPTApiUser();
  if (unauthorized) return unauthorized;
  try {
    const value = await libraryRequest<LibraryIssue>(
      `/api/v1/issues/by-path?path=${encodeURIComponent(pathname)}`,
    );
    return issueHtmlResponse(value, request.method === 'HEAD');
  } catch (error) {
    return libraryApiError(error);
  }
}

export function GET(
  request: Request,
  context: RouteContext,
): Promise<Response> {
  return issue(request, context);
}

export function HEAD(
  request: Request,
  context: RouteContext,
): Promise<Response> {
  return issue(request, context);
}
