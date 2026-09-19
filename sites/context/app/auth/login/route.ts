import { startOwnerLogin } from "@/lib/owner-session";

export const dynamic = "force-dynamic";

export async function GET(request: Request): Promise<Response> {
  return startOwnerLogin(new URL(request.url).searchParams.get("returnTo"));
}
