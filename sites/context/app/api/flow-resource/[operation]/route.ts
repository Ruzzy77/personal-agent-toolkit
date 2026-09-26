import {proxyOwnerRequest} from "../../../../lib/owner-service";
export const dynamic="force-dynamic";
export async function POST(request:Request,context:{params:Promise<{operation:string}>}){
 const {operation}=await context.params;
 if(!["flow_resource_search","flow_resource_read"].includes(operation))return Response.json({error:"not_found"},{status:404});
 return proxyOwnerRequest(request,"/site/flow/v1/"+operation);
}
