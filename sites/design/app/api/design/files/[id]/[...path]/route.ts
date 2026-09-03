import { requireChatGPTApiUser } from "@/app/chatgpt-auth";
import { designApiError, designFetch } from "@/lib/design";

type RouteContext = { params: Promise<{ id: string; path: string[] }> };

async function readAsset(
  request: Request,
  { params }: RouteContext,
): Promise<Response> {
  const unauthorized = await requireChatGPTApiUser();
  if (unauthorized) return unauthorized;
  try {
    const { id, path } = await params;
    const encodedId = encodeURIComponent(id);
    const encodedPath = path.map(encodeURIComponent).join("/");
    const response = await designFetch(
      `/api/v1/recipes/${encodedId}/files/${encodedPath}`,
      { method: request.method },
    );
    const headers = new Headers();
    for (const name of ["Content-Type", "Content-Length", "ETag"]) {
      const value = response.headers.get(name);
      if (value) headers.set(name, value);
    }
    headers.set("Cache-Control", "private, max-age=60");
    headers.set("X-Content-Type-Options", "nosniff");
    const contentType = response.headers.get("Content-Type") ?? "";
    if (contentType.startsWith("text/html") || contentType.includes("svg")) {
      headers.set(
        "Content-Security-Policy",
        "sandbox; default-src 'self' data: blob:; script-src 'none'; connect-src 'none'; object-src 'none'; frame-ancestors 'self'",
      );
    }
    return new Response(request.method === "HEAD" ? null : response.body, {
      status: response.status,
      headers,
    });
  } catch (error) {
    return designApiError(error);
  }
}

export const GET = readAsset;
export const HEAD = readAsset;
