import { requireChatGPTApiUser } from "@/app/chatgpt-auth";
import { libraryApiError, libraryRequest } from "@/lib/library";
const allowed = new Set([
  "library_management_preview",
  "library_trash_list",
  "library_trash_restore",
  "library_trash_purge",
  "library_operation_status",
  "library_capabilities",
  "library_issue_trash",
]);
export async function POST(
  request: Request,
  context: { params: Promise<{ operation: string }> },
): Promise<Response> {
  const unauthorized = await requireChatGPTApiUser();
  if (unauthorized) return unauthorized;
  const origin = request.headers.get("origin");
  if (origin && origin !== new URL(request.url).origin)
    return Response.json({ error: "origin_mismatch" }, { status: 403 });
  const { operation } = await context.params;
  if (!allowed.has(operation))
    return Response.json({ error: "not_found" }, { status: 404 });
  try {
    return Response.json(
      await libraryRequest(`/api/v1/operations/${operation}`, {
        method: "POST",
        body: await request.text(),
      }),
    );
  } catch (error) {
    return libraryApiError(error);
  }
}
