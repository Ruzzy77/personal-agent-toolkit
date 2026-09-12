import { requireChatGPTApiUser } from "@/app/chatgpt-auth";
import { designRequest, designApiError } from "@/lib/design";
export async function GET(request: Request) {
  const unauthorized = await requireChatGPTApiUser();
  if (unauthorized) return unauthorized;
  try {
    return Response.json({
      recipes: await designRequest(
        `/api/v1/recipes${new URL(request.url).search}`,
      ),
    });
  } catch (error) {
    return designApiError(error);
  }
}
