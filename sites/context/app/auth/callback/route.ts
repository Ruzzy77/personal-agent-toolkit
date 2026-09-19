import { OwnerSessionError, completeOwnerLogin } from "@/lib/owner-session";

export const dynamic = "force-dynamic";

export async function GET(request: Request): Promise<Response> {
  try {
    return await completeOwnerLogin(request);
  } catch (error) {
    if (!(error instanceof OwnerSessionError)) {
      console.error("owner login callback failed", error);
    }
    const status = error instanceof OwnerSessionError ? error.status : 500;
    return Response.json(
      { error: status === 401 ? "authentication_required" : "login_unavailable" },
      { status, headers: { "Cache-Control": "no-store" } },
    );
  }
}
