import {artifactSummary,compactWork,compactChange,chunkArtifact} from './store.mjs';
import {sourceItems} from '../src/model.js';
import {libraryEntrySource,libraryEntryVisible} from '../src/library.js';
const fail=(status,message)=>Object.assign(new Error(message),{status});
const page=(items,offset=0,limit=50)=>{
 if(!Number.isSafeInteger(offset)||offset<0||!Number.isSafeInteger(limit)||limit<1||limit>100)throw fail(422,'조회 범위를 확인해 주세요.');
 return {items:items.slice(offset,offset+limit),nextOffset:offset+limit<items.length?offset+limit:null};
};
export function sourceSummary(item){
 const artifact=item.artifact,rest=Object.fromEntries(Object.keys(item).filter(key=>!['body','blocks','artifact'].includes(key)).map(key=>[key,item[key]]));
 return {...rest,...(artifact?{artifactRef:artifactSummary(artifact),artifactId:artifact.id,artifactRevision:artifact.revision}:{})};
}
export function sourceCatalog(state){
 if(!state)return [];
 const plain=sourceItems({...state,works:[],savedIds:[],libraryEntries:[],librarySnapshots:[]});
 const saved=(state.librarySnapshots||[]).map(s=>({id:s.id,workId:s.workId,artifactId:s.artifactId,title:s.artifact.title,kind:'결과물',collection:'보관한 작업물',artifactRef:artifactSummary(s.artifact),artifactRevision:s.artifact.revision}));
 const known=new Set(saved.map(s=>s.artifactId));
 for(const w of state.works)for(const a of w.artifacts)if(state.savedIds.includes(a.id)&&!known.has(a.id))saved.push({id:a.id,workId:w.id,artifactId:a.id,title:a.title,kind:'결과물',collection:'보관한 작업물',artifactRef:artifactSummary(a),artifactRevision:a.revision});
 return [...(state.libraryEntries||[]).map(entry=>{const summary=libraryEntrySource({...sourceSummary(entry),body:''});Object.defineProperty(summary,'body',{enumerable:true,get:()=>entry.body||''});return summary}),...plain,...saved];
}
export function createFlowReads(store,{workspaceId,displayName,toolkitUrl,publicUrl}){
 const link=(workId,artifactId)=>toolkitUrl?toolkitUrl.replace(/\/$/,'')+'/flow?'+new URLSearchParams({workspace:workspaceId,...(workId?{work:workId}:{}),...(artifactId?{artifact:artifactId}:{})}):(publicUrl?publicUrl.replace(/\/$/,'')+'/?'+new URLSearchParams({...(workId?{work:workId}:{}),...(artifactId?{artifact:artifactId}:{})}):null);
 const check=id=>{if(id!==workspaceId)throw fail(404,'등록된 작업공간이 아닙니다.')};
 const get=(state,id)=>{const work=state?.works.find(w=>w.id===id);if(!work)throw fail(404,'작업을 찾지 못했습니다.');return work};
 const changes=(state,workId)=>(state?.changes||[]).filter(c=>c.kind==='change'&&c.workId===workId);
 function workList({workspaceId:id,query='',offset=0,limit=50}){
  check(id);if(typeof query!=='string'||query.length>200)throw fail(422,'검색어를 확인해 주세요.');const {state,revision}=store.read();
  const matches=(state?.works||[]).filter(w=>w.name.toLocaleLowerCase('ko').includes(query.toLocaleLowerCase('ko')));
  const result=page(matches,offset,limit);
  return {stateRevision:revision,works:result.items.map(w=>({id:w.id,name:w.name,revision:w.revision,purpose:w.purpose||'',artifact:artifactSummary(w.artifacts.find(a=>a.id===w.activeArtifactId)||w.artifacts[0]),reviewCount:changes(state,w.id).filter(c=>['review','conflict'].includes(c.status)).length,link:link(w.id)})),nextOffset:result.nextOffset};
 }
 function workRead({workspaceId:id,workId,offset=0,limit=100}){
  check(id);const {state,revision}=store.read(),w=get(state,workId);
  const history=changes(state,workId),pending=history.filter(c=>['review','conflict'].includes(c.status));
  const undo=history.findLast(c=>c.status==='completed'&&c.before&&w.artifacts.some(a=>a.id===c.artifactId&&a.revision===c.appliedRevision));
  const byId=new Map(sourceCatalog(state).map(s=>[s.id,s]));
  const refs=page(w.sourceIds,offset,limit);
  return {stateRevision:revision,work:compactWork(w),sources:refs.items.map(id=>sourceSummary(byId.get(id)||{id,title:'자료를 찾지 못했습니다.',unavailable:true})),nextSourceOffset:refs.nextOffset,changes:pending.slice(0,100).map(compactChange),nextChangeOffset:pending.length>100?100:null,undo:undo?compactChange(undo):null,link:link(w.id)};
 }
 function libraryList({workspaceId:id,query='',workId,offset=0,limit=50}){
  check(id);const {state}=store.read();if(workId)get(state,workId);
  if(typeof query!=='string'||query.length>200)throw fail(422,'검색어를 확인해 주세요.');
  const term=query.toLocaleLowerCase('ko');
  const result=page(sourceCatalog(state).filter(s=>(!s.scope||libraryEntryVisible(s,workId))&&(!term||(s.title+' '+(s.body||'')).toLocaleLowerCase('ko').includes(term))),offset,limit);
  return {entries:result.items.map(sourceSummary),nextOffset:result.nextOffset};
 }
 function libraryRead({workspaceId:id,entryId}){
  check(id);const {state}=store.read(),entry=sourceCatalog(state).find(s=>s.id===entryId);
  if(!entry)throw fail(404,'자료를 찾지 못했습니다.');return {entry};
 }
 function artifactRead({workspaceId:id,...input}){
  check(id);
  if([input.artifactId,input.sourceId,input.changeId].filter(Boolean).length!==1)throw fail(422,'작업물이나 보관본, 수정안 중 하나를 선택해 주세요.');
  const artifact=store.artifact(input);if(input.revision!==undefined&&artifact.revision!==input.revision)throw fail(409,'작업물 버전이 바뀌었습니다.');
  return chunkArtifact(artifact,input);
 }
 function changeList({workspaceId:id,workId,offset=0,limit=50,status}){
  check(id);const {state}=store.read();get(state,workId);
  const result=page(changes(state,workId).filter(c=>!status||c.status===status).reverse(),offset,limit);
  return {changes:result.items.map(compactChange),nextOffset:result.nextOffset};
 }
 function changeRead({workspaceId:id,changeId}){
  check(id);const {state}=store.read(),change=state?.changes?.find(c=>c.kind==='change'&&c.id===changeId);
  if(!change)throw fail(404,'변경 내용을 찾지 못했습니다.');return {change:compactChange(change)};
 }
 function workspaceList(){return {workspaces:[{id:workspaceId,name:displayName,apiVersion:5,capabilities:['work.read','work.write','artifact.read.chunks','change.list','change.read','change.submit','change.review','library.read','library.write'],url:link()}]}}
 return {workList,workRead,artifactRead,libraryList,libraryRead,changeList,changeRead,workspaceList,link};
}
