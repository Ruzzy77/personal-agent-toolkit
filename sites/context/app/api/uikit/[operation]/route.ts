import {uikitRead} from '@/lib/uikit-service';
import {isolatedUIKitPreview,UIKIT_PREVIEW_CSP} from '@/lib/uikit-preview';
import {OwnerSessionError} from '@/lib/owner-session';
export async function GET(request:Request,{params}:{params:Promise<{operation:string}>}){
 const {operation}=await params;
 if(!['search','read','preview'].includes(operation))return new Response('Not found',{status:404});
 const query=new URL(request.url).searchParams;
 const id=query.get('id'),revision=query.get('revision');
 if(operation!=='search'&&(!id||!/^[a-z0-9][a-z0-9-]{0,100}$/.test(id)))return new Response('Invalid item',{status:400});
 if(revision&&!/^[a-f0-9]{64}$/.test(revision))return new Response('Invalid revision',{status:400});
 if(operation==='preview'&&request.headers.get('Sec-Fetch-Dest')!=='iframe')return new Response('Open in Flow',{status:403});
 try{
  const result=await uikitRead(operation as 'search'|'read'|'preview',{...(id?{id}:{}),...(revision?{revision}:{}),q:(query.get('q')||'').slice(0,300)});
  const headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer'};
  if(operation==='preview'){
   const file=result as {type:string;content:string};
   if(file.type!=='text/html')return new Response('Not found',{status:404});
   return new Response(isolatedUIKitPreview(file.content),{headers:{...headers,'Content-Type':'text/html;charset=utf-8','Content-Security-Policy':UIKIT_PREVIEW_CSP}});
  }
  return Response.json(result,{headers});
 }catch(error){
  return Response.json({error:'UIKit 자료를 열지 못했습니다.'},{status:error instanceof OwnerSessionError?error.status:502,headers:{'Cache-Control':'private, no-store'}});
 }
}
