import { proxyOwnerRequest } from "../../../../lib/owner-service";
export const dynamic="force-dynamic";
const operations=new Set([
  "host_roots","host_capabilities","host_read","host_write","host_files","host_transfer",
  "flow_library_list","flow_library_read","flow_library_upsert","flow_workspace_list","flow_work_list","flow_work_read",
  "flow_work_create","flow_work_update","flow_snapshot_list","flow_snapshot_read","flow_snapshot_create","flow_asset_import","flow_change_submit","flow_change_action",
]);
export async function POST(request:Request,context:{params:Promise<{operation:string}>}) {
  const {operation}=await context.params;
  if(!operations.has(operation)) return Response.json({error:{code:"not_found"}},{status:404});
  return proxyOwnerRequest(request,"/site/host/v1/"+operation);
}
