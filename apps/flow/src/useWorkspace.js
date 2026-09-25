import {useEffect,useRef,useState,useCallback} from 'react';
import {STORAGE_KEY,V2_STORAGE_KEY,LEGACY_STORAGE_KEY,emptyState,migrateState} from './model.js';
import {mergeStates} from './merge.js';

export function downloadText(name,text,type='application/json'){
 const url=URL.createObjectURL(new Blob([text],{type})),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
export function readCache(storage=localStorage,drafts=sessionStorage){
 let raw=null;
 try{
  const unpack=(value)=>{const parsed=JSON.parse(value),wrapped=parsed&&typeof parsed==='object'&&'state' in parsed;
   return wrapped?{...parsed,state:migrateState(parsed.state),base:parsed.base?migrateState(parsed.base):null,error:null}:{state:migrateState(parsed),base:null,revision:null,dirty:true,error:null};
  };
  for(const key of [STORAGE_KEY,V2_STORAGE_KEY]){
   const draft=drafts.getItem(key+'-draft');
   if(draft){raw=draft;const value=unpack(draft);if(value.dirty)return value}
  }
  for(const key of [STORAGE_KEY,V2_STORAGE_KEY]){
   raw=storage.getItem(key);
   if(raw)return unpack(raw);
  }
  raw=storage.getItem(LEGACY_STORAGE_KEY);
  return raw?unpack(raw):{state:emptyState(),base:null,revision:null,dirty:false,error:null};
 }catch{return {state:emptyState(),base:null,revision:null,dirty:false,error:'저장된 작업을 읽지 못했습니다. 기존 저장 내용은 그대로 남아 있습니다.',raw}}
}

async function api(path,options={}){
 const response=await fetch('/api/flow/'+path,{...options,headers:{'X-Toolkit-Flow':'1',...options.headers},signal:AbortSignal.timeout(8000)});
 if(!response.headers.get('content-type')?.includes('application/json'))throw Object.assign(new Error('작업 서비스에 연결되지 않았습니다.'),{unavailable:true});
 const data=await response.json();
 if(!response.ok)throw Object.assign(new Error(data.error||'작업공간에 저장하지 못했습니다.'),{status:response.status});
 return data;
}
export function useWorkspace(){
 const initial=useRef();if(!initial.current)initial.current=readCache();
 const cache=useRef(initial.current),[store,render]=useState(initial.current.state);
 const [status,setStatus]=useState({phase:'loading',remote:false,message:''}),statusRef=useRef(status);
 const [workspaceName,setWorkspaceName]=useState('작업공간'),[toolkitUrl,setToolkitUrl]=useState('');
 const ready=useRef(false),mounted=useRef(false),running=useRef(false),timer=useRef(),flushRef=useRef();
 const report=useCallback(next=>{statusRef.current=next;if(mounted.current)setStatus(next)},[]);
 function cacheNow(){
  const value=JSON.stringify({state:cache.current.state,base:cache.current.dirty?cache.current.base:null,revision:cache.current.revision,dirty:cache.current.dirty});
  let cached=true;
  try{if(cache.current.dirty)sessionStorage.setItem(STORAGE_KEY+'-draft',value);else sessionStorage.removeItem(STORAGE_KEY+'-draft')}catch{cached=false}
  try{localStorage.setItem(STORAGE_KEY,value)}catch{cached=false}
  cache.current.cached=cached;return cached;
 }
 function flush(){
  if(running.current||!ready.current||!statusRef.current.remote||statusRef.current.phase==='conflict'||!cache.current.dirty)return;
  running.current=true;const snapshot=cache.current.state,base=cache.current.base,revision=cache.current.revision;
  report({phase:'saving',remote:true,message:''});
  (base?api('workspace/merge',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({base,state:snapshot})}):api('workspace',{method:'PUT',headers:{'Content-Type':'application/json','If-Match':revision||'empty'},body:JSON.stringify(snapshot)}))
   .then(result=>{
    const saved=result.state||snapshot;
    try{const current=cache.current.state===snapshot?saved:mergeStates(snapshot,cache.current.state,saved);cache.current={...cache.current,state:current,base:saved,revision:result.revision,dirty:current!==saved};render(current)}
    catch{report({phase:'conflict',remote:true,message:'같은 부분이 변경되었습니다. 현재 수정은 이 브라우저에 남아 있습니다.'});return}
    const cached=cacheNow();
    report({phase:cache.current.dirty?'saving':'saved',remote:true,message:cached?'':'브라우저 임시 저장 공간이 부족합니다.'});
   }).catch(e=>report({phase:e.status===409?'conflict':'offline',remote:e.status===409,message:!cache.current.cached?'현재 변경을 저장하지 못했습니다. 파일로 내려받아 보관해 주세요.':e.status===409?'다른 화면에서 작업이 변경되었습니다. 현재 변경은 이 브라우저에 남아 있습니다.':'작업공간에 저장하지 못했습니다. 현재 변경은 이 브라우저에 남아 있습니다.'}))
   .finally(()=>{running.current=false;if(mounted.current&&cache.current.dirty&&statusRef.current.phase==='saving')timer.current=setTimeout(()=>flushRef.current(),0)});
 }
 flushRef.current=flush;
 async function connect(ignoreLocal=false,quiet=false){
  if(cache.current.error){report({phase:'error',remote:false,message:cache.current.error});return}
  if(!quiet)report({phase:'loading',remote:false,message:''});
  if(location.protocol==='file:'){ready.current=true;const cached=cacheNow();report({phase:cached?'browser':'error',remote:false,message:cached?'':'변경 내용을 저장할 공간이 부족합니다. 파일로 내려받아 보관해 주세요.'});return}
  try{
   const remote=await api('workspace');
   if(!mounted.current)return;
   if(remote.api!=='toolkit-flow-v4')throw Object.assign(new Error('화면과 저장 서비스의 버전이 다릅니다. 새로고침 후 다시 확인해 주세요.'),{incompatible:true});
   if(typeof remote.displayName==='string')setWorkspaceName(remote.displayName);
   if(typeof remote.toolkitUrl==='string')setToolkitUrl(remote.toolkitUrl);
   if(!ignoreLocal&&remote.state&&cache.current.dirty&&cache.current.revision!==remote.revision&&!cache.current.base){
    ready.current=true;
    report({phase:'conflict',remote:true,message:'다른 저장본이 있습니다. 현재 변경은 이 브라우저에 남아 있습니다.'});return;
   }
   if(remote.state&&(!cache.current.dirty||ignoreLocal)){
    cache.current={state:migrateState(remote.state),base:migrateState(remote.state),revision:remote.revision,dirty:false,error:null};render(cache.current.state);
   }else cache.current={...cache.current,base:cache.current.base||remote.state||null,revision:remote.revision};
   cacheNow();ready.current=true;report({phase:'saved',remote:true,message:''});flushRef.current();
  }catch(error){
   if(!mounted.current)return;
   if(error.incompatible){ready.current=false;report({phase:'error',remote:false,message:error.message});return;}
   ready.current=true;const cached=cacheNow();
   report({phase:!cached?'error':cache.current.revision?'offline':'browser',remote:false,message:!cached?'변경 내용을 저장할 공간이 부족합니다. 파일로 내려받아 보관해 주세요.':cache.current.revision?'작업공간에 연결하지 못했습니다. 이 브라우저의 저장본을 열었습니다.':''});
  }
 }
 const setStore=useCallback(update=>{
  if(!ready.current||cache.current.error)return;
  const state=typeof update==='function'?update(cache.current.state):update;
  if(state===cache.current.state)return;
  cache.current={...cache.current,state,dirty:true};render(state);
  if(!cacheNow())report({...statusRef.current,phase:'error',message:'브라우저 임시 저장 공간이 부족합니다. 변경 내용을 내려받아 보관해 주세요.'});
  else if(statusRef.current.phase!=='conflict'&&statusRef.current.remote)report({phase:'saving',remote:true,message:''});
  clearTimeout(timer.current);timer.current=setTimeout(()=>flushRef.current(),350);
 },[]);
 useEffect(()=>{
  mounted.current=true;connect();
  const refreshTimer=setInterval(()=>{if(!cache.current.dirty&&!running.current&&statusRef.current.remote)connect(false,true)},5000);
  const leave=e=>{if(cache.current.dirty&&(running.current||statusRef.current.phase==='saving'||['error','conflict','offline'].includes(statusRef.current.phase))){e.preventDefault();e.returnValue=''}};
  window.addEventListener('beforeunload',leave);
  return()=>{mounted.current=false;clearTimeout(timer.current);clearInterval(refreshTimer);window.removeEventListener('beforeunload',leave)};
 },[]);
 async function recover(){
  if(running.current)return;
  const remote=await api('workspace');
  if(!remote.state)throw new Error('작업공간 저장본이 없습니다.');
  downloadText('toolkit-workspace.json',cache.current.raw||JSON.stringify(cache.current.state,null,2));
  cache.current={state:migrateState(remote.state),base:migrateState(remote.state),revision:remote.revision,dirty:false,error:null};
  cacheNow();ready.current=true;render(cache.current.state);report({phase:'saved',remote:true,message:''});
 }
 function exportCurrent(){downloadText('toolkit-workspace.json',cache.current.raw||JSON.stringify(cache.current.state,null,2))}
 async function uploadImage(file){
  if(!['image/png','image/jpeg','image/webp','image/gif'].includes(file.type))throw new Error('PNG, JPEG, WebP, GIF 이미지를 선택해 주세요.');
  if(file.size>20*1024*1024)throw new Error('20MB 이하의 이미지를 선택해 주세요.');
  const temp=URL.createObjectURL(file);
  let size;
  try{const img=new Image();img.src=temp;await img.decode();size={width:img.naturalWidth,height:img.naturalHeight}}catch{throw new Error('이미지 내용을 읽지 못했습니다.')}finally{URL.revokeObjectURL(temp)}
  if(statusRef.current.remote){
   const result=await api('assets',{method:'POST',headers:{'Content-Type':file.type},body:file});
   return {...result,...size,originalName:file.name,alt:''};
  }
  if(file.size>2*1024*1024)throw new Error('작업공간에 연결되지 않았습니다. 브라우저 저장은 2MB 이하의 이미지만 지원합니다.');
  const src=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(new Error('이미지를 읽지 못했습니다.'));reader.readAsDataURL(file)});
  return {src,...size,originalName:file.name,mime:file.type,alt:''};
 }
 return {store,setStore,status,workspaceName,toolkitUrl,ready:ready.current,retry:()=>connect(),recover,exportCurrent,uploadImage};
}
