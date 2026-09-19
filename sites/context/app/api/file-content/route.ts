import { ownerServiceFetch, requireOwnerApiUser } from "../../../lib/owner-service";
export const dynamic="force-dynamic";
export async function GET(request:Request) {
  const denied=await requireOwnerApiUser(request);if(denied)return denied;
  const url=new URL(request.url),root=url.searchParams.get("root"),path=url.searchParams.get("path");
  if(!root||!path||root.length>256||path.length>4096) return Response.json({error:"invalid_path"},{status:400});
  const begin=await ownerServiceFetch("/site/host/v1/host_transfer",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({root,path,direction:"download"})});
  if(!begin.ok) return begin;
  const transfer=await begin.json() as {transfer_id:string;token:string};
  const base="/site/host/v1/transfers/"+encodeURIComponent(transfer.transfer_id);
  const headers=new Headers({"X-Toolkit-Transfer-Token":transfer.token});
  const range=request.headers.get("range");if(range)headers.set("Range",range);
  const response=await ownerServiceFetch(base+"/content"+(url.searchParams.get("preview")==="1"?"?preview=1":""),{headers});
  const cleanup=()=>ownerServiceFetch(base+"/status",{method:"DELETE",headers}).catch(()=>undefined);
  if(!response.body){await cleanup();return response;}
  const reader=response.body.getReader();
  const body=new ReadableStream({
    async pull(controller){try{const {done,value}=await reader.read();if(done){await cleanup();controller.close();}else controller.enqueue(value);}catch(error){await cleanup();controller.error(error);}},
    async cancel(reason){await reader.cancel(reason);await cleanup();}
  });
  return new Response(body,{status:response.status,headers:response.headers});
}
