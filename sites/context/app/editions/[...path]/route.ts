import { requireOwnerUser } from '@/lib/owner-service';
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
  await requireOwnerUser(new URL(request.url).pathname);
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
