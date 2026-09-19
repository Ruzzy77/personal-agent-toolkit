import { proxyOwnerRequest } from "../../../../lib/owner-service";
export const dynamic="force-dynamic";
const operations=new Set(["host_roots","host_capabilities","host_read","host_write","host_files","host_transfer"]);
export async function POST(request:Request,context:{params:Promise<{operation:string}>}) {
  const {operation}=await context.params;
  if(!operations.has(operation)) return Response.json({error:{code:"not_found"}},{status:404});
  return proxyOwnerRequest(request,"/site/host/v1/"+operation);
}
