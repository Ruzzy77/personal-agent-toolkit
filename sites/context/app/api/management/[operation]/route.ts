import { proxyOwnerRequest } from "../../../../lib/owner-service";
export const dynamic="force-dynamic";
export async function POST(request:Request,context:{params:Promise<{operation:string}>}){
 const {operation}=await context.params;
 if(!/^(corpus|sense|library|design)_[a-z_]{1,80}$/.test(operation))return Response.json({error:"operation_not_found"},{status:404});
 return proxyOwnerRequest(request,"/admin/v1/"+operation);
}
