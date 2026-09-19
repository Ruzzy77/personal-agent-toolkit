import { requireOwnerApiUser } from "@/lib/owner-service";
import { designApiError, designRequest } from "@/lib/design";

export async function GET(request: Request): Promise<Response> {
  const unauthorized = await requireOwnerApiUser(request);
  if (unauthorized) return unauthorized;
  try {
    return Response.json({ catalog: await designRequest("/api/v1/catalog") });
  } catch (error) {
    return designApiError(error);
  }
}
