import { proxyOwnerRequest } from "../../../../../lib/owner-service";
export const dynamic="force-dynamic";
async function handle(request:Request,context:{params:Promise<{id:string;action:string}>}) {
  const {id,action}=await context.params;
  if(!/^[A-Za-z0-9_-]{12,80}$/.test(id)||!["status","chunk","commit","content"].includes(action)) return Response.json({error:"not_found"},{status:404});
  return proxyOwnerRequest(request,"/site/host/v1/transfers/"+id+"/"+action+new URL(request.url).search);
}
export {handle as GET,handle as PUT,handle as POST,handle as DELETE};
