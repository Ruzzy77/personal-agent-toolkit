import React,{useEffect,useState} from 'react';
import {B,WorkCanvas,contentRenderers} from './work-surface/index.js';
import {readCache,downloadText} from './useWorkspace.js';
import {STORAGE_KEY,V2_STORAGE_KEY} from './model.js';

export function LegacyBridge(){
 const [cache]=useState(()=>readCache()),[manifest,setManifest]=useState(null),[error,setError]=useState(cache.error||''),[busy,setBusy]=useState(false),[recovered,setRecovered]=useState(false),[attempt,setAttempt]=useState(0);
 useEffect(()=>{
  const controller=new AbortController();
  fetch('/api/flow/workspace',{signal:controller.signal}).then(async response=>{if(!response.ok)throw new Error();return response.json()}).then(value=>{if(!controller.signal.aborted)setManifest(value)}).catch(()=>{if(!controller.signal.aborted)setError('작업 서비스에 연결하지 못했습니다.')});
  return()=>controller.abort();
 },[attempt]);
 const dirty=cache.dirty&&!recovered;
 const href=(()=>{if(!manifest?.webUrl)return null;const url=new URL(manifest.webUrl),params=new URLSearchParams(location.search);for(const key of ['work','artifact','screen'])if(params.has(key))url.searchParams.set(key,params.get(key));const current=cache.state.works.find(work=>work.id===cache.state.activeId);if(!params.has('work')&&current){url.searchParams.set('work',current.id);if(current.activeArtifactId)url.searchParams.set('artifact',current.activeArtifactId)}return url.href})();
 const changed=cache.state.works.filter(work=>!cache.base||JSON.stringify(work)!==JSON.stringify(cache.base.works.find(item=>item.id===work.id)));
 async function recover(){
  if(!cache.base||busy)return;setBusy(true);setError('');
  try{
   const tokenKey='flow-recovery-request',stored=sessionStorage.getItem(tokenKey),payload=JSON.stringify({base:cache.base,state:cache.state});
   const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(payload)),fingerprint=Array.from(new Uint8Array(digest),byte=>byte.toString(16).padStart(2,'0')).join('');
   const previous=stored?JSON.parse(stored):null;
   const idempotencyKey=previous?.fingerprint===fingerprint?previous.key:crypto.randomUUID();
   sessionStorage.setItem(tokenKey,JSON.stringify({fingerprint,key:idempotencyKey}));
   const response=await fetch('/api/flow/workspace/recover',{method:'POST',headers:{'Content-Type':'application/json','X-Toolkit-Flow':'1'},body:JSON.stringify({base:cache.base,state:cache.state,idempotencyKey})});
   const result=await response.json();if(!response.ok||!result.recovered)throw new Error(result.error||'수정 내용을 복구하지 못했습니다.');
   // Retain the local document; only mark this successfully recovered draft as clean.
   localStorage.setItem(STORAGE_KEY,JSON.stringify({state:cache.state,base:null,dirty:false,revision:result.revision}));
   for(const key of [STORAGE_KEY,V2_STORAGE_KEY])sessionStorage.removeItem(key+'-draft');
   sessionStorage.removeItem(tokenKey);setRecovered(true);
  }catch(e){setError(e.message||'수정 내용을 복구하지 못했습니다. 이 브라우저의 내용은 그대로 남아 있습니다.')}
  finally{setBusy(false)}
 }
 return <main className="su-page su-stack" data-gap="section">
  <header><h1>Toolkit</h1><p>작업은 Toolkit 웹에서 이어갈 수 있습니다.</p></header>
  {dirty&&<section className="su-stack" data-gap="section"><h2>이 브라우저에 저장하지 않은 수정이 있습니다.</h2>
   {changed.map(work=><details key={work.id}><summary>{work.name}</summary><WorkCanvas work={work} renderers={contentRenderers}/></details>)}
   {!cache.base&&<p>변경 전 저장본을 확인할 수 없어 자동으로 합칠 수 없습니다. 수정 내용을 보관한 뒤 원본과 비교해 주세요.</p>}
   <div className="su-row">{cache.base&&<B disabled={busy} onClick={()=>void recover()}>{busy?'복구하는 중':'수정 내용 복구'}</B>}<B variant="ghost" onClick={()=>downloadText('toolkit-workspace-draft.json',cache.raw||JSON.stringify(cache))}>수정 내용 내려받기</B></div>
  </section>}
  {recovered&&<p role="status">수정 내용을 복구했습니다.</p>}
  {error&&<div className="su-stack"><p role="alert">{error}</p><div><B variant="ghost" onClick={()=>{setError('');setAttempt(value=>value+1)}}>다시 연결</B></div></div>}
  {href&&<div><a href={href}>Toolkit에서 계속하기</a></div>}
 </main>;
}
