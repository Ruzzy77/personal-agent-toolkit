import {workspaceFileType,workspaceFilePresentation} from './file-types.js';

export function safeContentHref(value){
 if(typeof value!=='string')return null;
 const href=value.trim();
 if(!href||/[\u0000-\u001f\u007f]/.test(href)||href.startsWith('//'))return null;
 if(href.startsWith('/')||href.startsWith('./')||href.startsWith('../')||href.startsWith('#'))return href;
 try{
  const url=new URL(href);
  return ['http:','https:','mailto:'].includes(url.protocol)?href:null;
 }catch{return null}
}

function inlineWorkspaceFileHref(value,check){
 const href=safeContentHref(value);
 if(!href?.startsWith('/'))return null;
 try{
  const url=new URL(href,'http://flow.local');
  if(url.origin!=='http://flow.local')return null;
  const local=url.pathname==='/api/flow/files/content';
  const bridged=/^\/api\/flow-media\/[a-z0-9][a-z0-9._-]*\/files\/content$/.test(url.pathname);
  return (local||bridged)&&check(url.searchParams.get('path')||'')?href:null;
 }catch{return null}
}

export function inlineFileSource(value){
 const href=safeContentHref(value);
 if(!href?.startsWith('/'))return null;
 try{
  const url=new URL(href,'http://flow.local');
  if(url.origin!=='http://flow.local')return null;
  const workspace=url.pathname==='/api/flow/files/content'||/^\/api\/flow-media\/[a-z0-9][a-z0-9._-]*\/files\/content$/.test(url.pathname);
  if(workspace){const path=url.searchParams.get('path');return path?{href,path}:null}
  const sample=/^\/(?:api\/flow-media\/[a-z0-9][a-z0-9._-]*\/)?examples\/([A-Za-z0-9._-]+)$/.exec(url.pathname);
  const kind=sample&&workspaceFilePresentation(sample[1])?.viewer;
  return sample&&['image','video','audio'].includes(kind)?{href,path:sample[1]}:null;
 }catch{return null}
}

export function inlinePdfHref(value){
 return inlineWorkspaceFileHref(value,path=>workspaceFileType(path)==='PDF');
}

export function inlineTextHref(value){
 return inlineWorkspaceFileHref(value,path=>{
  const type=workspaceFileType(path);
  return type!==null&&type!=='PDF';
 });
}

export function pdfPreviewHref(value,page=1){
 const href=inlinePdfHref(value);
 if(!href||!Number.isInteger(page)||page<1||page>10000)return null;
 const url=new URL(href,'http://flow.local');
 url.pathname=url.pathname.replace(/\/content$/,'/preview');
 url.searchParams.set('page',String(page));
 return url.pathname+url.search;
}

export function resolveRelativeImagePath(documentPath,relative){
 if(typeof documentPath!=='string'||typeof relative!=='string'||!documentPath||!relative||
    relative.startsWith('/')||relative.includes('\\')||/[\u0000-\u001f\u007f?#:]/.test(relative))return null;
 const base=documentPath.split('/');
 if(base.some(part=>!part||part==='.'||part==='..'))return null;
 base.pop();
 for(const encoded of relative.split('/')){
  let part;
  try{part=decodeURIComponent(encoded)}catch{return null}
  if(!part||part==='.')continue;
  if(part==='..'){
   if(!base.length)return null;
   base.pop();
  }else if(part.includes('/')||part.includes('\\')||/[\u0000-\u001f\u007f:]/.test(part))return null;
  else base.push(part);
 }
 const result=base.join('/');
 return /\.(?:png|jpe?g|gif|webp|avif)$/i.test(result)?result:null;
}

export function markdownImageHref(fileHref,relative){
 const href=inlineTextHref(fileHref);
 if(!href)return null;
 const url=new URL(href,'http://flow.local');
 const path=resolveRelativeImagePath(url.searchParams.get('path')||'',relative);
 if(!path)return null;
 url.searchParams.set('path',path);
 return url.pathname+url.search;
}
