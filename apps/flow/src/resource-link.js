import {validResourceReference} from './work-surface/resource-reference.js';
export function resourceLink(reference,toolkitUrl,workspaceId){
 if(!validResourceReference(reference)||!toolkitUrl)return null;
 try{
  const base=new URL(toolkitUrl);
  if(!['https:','http:'].includes(base.protocol)||base.username||base.password)return null;
  const url=new URL('/flow',base);url.searchParams.set('resource',JSON.stringify(reference));
  if(workspaceId)url.searchParams.set('workspace',workspaceId);
  return url.href;
 }catch{return null}
}
