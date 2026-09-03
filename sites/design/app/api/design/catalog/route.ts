import { requireChatGPTApiUser } from "@/app/chatgpt-auth";
import { designApiError, designRequest } from "@/lib/design";

export async function GET(): Promise<Response> {
  const unauthorized = await requireChatGPTApiUser();
  if (unauthorized) return unauthorized;
  try {
    return Response.json({ catalog: await designRequest("/api/v1/catalog") });
  } catch (error) {
    return designApiError(error);
  }
}
