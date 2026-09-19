import {
  csrfMatches,
  deleteOwnerSession,
  ownerCookieClear,
  ownerSession,
} from "@/lib/owner-session";

export const dynamic = "force-dynamic";

export async function POST(request: Request): Promise<Response> {
  const session = await ownerSession(request);
  if (!session || !csrfMatches(session, request)) {
    return Response.json({ error: "csrf_rejected" }, { status: 403 });
  }
  await deleteOwnerSession(request);
  return new Response(null, {
    status: 204,
    headers: {
      "Cache-Control": "no-store",
      "Set-Cookie": ownerCookieClear(),
    },
  });
}
