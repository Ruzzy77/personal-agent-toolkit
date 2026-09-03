import { requireChatGPTApiUser } from '@/app/chatgpt-auth';
import {
  libraryApiError,
  libraryRequest,
  type LibraryIssue,
  type LibraryMutationResult,
} from '@/lib/library';

type RouteContext = { params: Promise<{ id: string }> };

export async function GET(
  _request: Request,
  { params }: RouteContext,
): Promise<Response> {
  const unauthorized = await requireChatGPTApiUser();
  if (unauthorized) return unauthorized;
  try {
    const { id } = await params;
    const issue = await libraryRequest<LibraryIssue>(
      `/api/v1/issues/${encodeURIComponent(id)}`,
    );
    return Response.json({ issue });
  } catch (error) {
    return libraryApiError(error);
  }
}

async function mutate(
  request: Request,
  context: RouteContext,
): Promise<Response> {
  const unauthorized = await requireChatGPTApiUser();
  if (unauthorized) return unauthorized;
  try {
    const { id } = await context.params;
    const result = await libraryRequest<LibraryMutationResult>(
      `/api/v1/issues/${encodeURIComponent(id)}`,
      { method: request.method, body: await request.text() },
    );
    return Response.json(result);
  } catch (error) {
    return libraryApiError(error);
  }
}

export function PUT(
  request: Request,
  context: RouteContext,
): Promise<Response> {
  return mutate(request, context);
}

export function PATCH(
  request: Request,
  context: RouteContext,
): Promise<Response> {
  return mutate(request, context);
}
