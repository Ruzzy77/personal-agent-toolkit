import { requireChatGPTApiUser } from "@/app/chatgpt-auth";
import { designRequest, designApiError } from "@/lib/design";
export async function GET(
  _request: Request,
  context: { params: Promise<{ id: string }> },
) {
  const unauthorized = await requireChatGPTApiUser();
  if (unauthorized) return unauthorized;
  const { id } = await context.params;
  try {
    return Response.json({
      recipe: await designRequest(`/api/v1/recipes/${encodeURIComponent(id)}`),
    });
  } catch (error) {
    return designApiError(error);
  }
}
