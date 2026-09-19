import { ownerSession } from "@/lib/owner-session";

export const dynamic = "force-dynamic";

export async function GET(request: Request): Promise<Response> {
  const session = await ownerSession(request);
  if (!session) {
    return Response.json({ error: "authentication_required" }, { status: 401 });
  }
  return Response.json(
    { owner_id: session.ownerId, csrf: session.csrf },
    { headers: { "Cache-Control": "private, no-store" } },
  );
}
