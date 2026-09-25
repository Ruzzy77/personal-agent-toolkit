import { proxyOwnerRequest } from "../../../../../../lib/owner-service";

export const dynamic = "force-dynamic";

async function serve(request: Request, context: {params: Promise<{workspace:string;kind:string;name:string}>}) {
  const {workspace,kind,name} = await context.params;
  if (!/^[a-z0-9][a-z0-9._-]*$/.test(workspace) || !["assets","examples","files"].includes(kind) || !/^[A-Za-z0-9._-]+$/.test(name))
    return Response.json({error:"invalid_path"},{status:400});
  const incoming = new URL(request.url),path = incoming.searchParams.get("path"),page = incoming.searchParams.get("page");
  const validPage = page === null || name === "preview" && /^(?:[1-9]\d{0,3}|10000)$/.test(page);
  if (kind === "files" ? !["content","preview"].includes(name) || !path || path.length > 2048 || !validPage : path !== null || page !== null)
    return Response.json({error:"invalid_path"},{status:400});
  const query = new URLSearchParams();
  if (path) query.set("path",path);
  if (page) query.set("page",page);
  const target = "/site/host/v1/flow-media/" + [workspace,kind,name].map(encodeURIComponent).join("/") + (query.size ? "?"+query : "");
  return proxyOwnerRequest(request,target);
}

export const GET = serve;
export const HEAD = serve;
