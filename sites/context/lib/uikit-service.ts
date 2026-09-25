import {env} from 'cloudflare:workers';
import {ownerAccessToken,ownerSessionFromCookies,OwnerSessionError} from './owner-session';

type Reader={read(input:{token:string;resource:string;operation:'search'|'read'|'preview';q?:string;id?:string;revision?:string}):Promise<unknown>};
export async function uikitRead(operation:'search'|'read'|'preview',params:{q?:string;id?:string;revision?:string}={}):Promise<unknown>{
 const session=await ownerSessionFromCookies();
 if(!session)throw new OwnerSessionError(401,'owner login is required');
 const reader=(env as unknown as {UIKIT?:Reader}).UIKIT;
 if(!reader)throw new OwnerSessionError(500,'UIKit is not configured');
 return reader.read({token:await ownerAccessToken(session),resource:process.env.TOOLKIT_RESOURCE||'https://personal-agent-context.hiyaq77.workers.dev/mcp',operation,...params});
}
