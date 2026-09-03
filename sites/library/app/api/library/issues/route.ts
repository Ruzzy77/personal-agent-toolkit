import { requireChatGPTApiUser } from '@/app/chatgpt-auth';
import {
  libraryApiError,
  libraryRequest,
  type LibraryIssueSummary,
  type LibraryMutationResult,
} from '@/lib/library';

export async function GET(request: Request): Promise<Response> {
  const unauthorized = await requireChatGPTApiUser();
  if (unauthorized) return unauthorized;
  try {
    const query = new URL(request.url).search;
    const issues = await libraryRequest<LibraryIssueSummary[]>(
      `/api/v1/issues${query}`,
    );
    return Response.json({ issues });
  } catch (error) {
    return libraryApiError(error);
  }
}

export async function POST(request: Request): Promise<Response> {
  const unauthorized = await requireChatGPTApiUser();
  if (unauthorized) return unauthorized;
  try {
    const result = await libraryRequest<LibraryMutationResult>(
      '/api/v1/issues',
      { method: 'POST', body: await request.text() },
    );
    return Response.json(result, { status: 201 });
  } catch (error) {
    return libraryApiError(error);
  }
}
