import { requireChatGPTApiUser } from "@/app/chatgpt-auth";
import { designApiError, designRequest } from "@/lib/design";
const allowed = new Set([
  "design_management_preview",
  "design_trash_list",
  "design_trash_restore",
  "design_trash_purge",
  "design_operation_status",
  "design_capabilities",
  "design_recipe_trash",
  "design_file_trash",
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
      await designRequest(`/api/v1/operations/${operation}`, {
        method: "POST",
        body: await request.text(),
      }),
    );
  } catch (error) {
    return designApiError(error);
  }
}
