import {validLibraryEntry,libraryEntryKey,libraryEntryVisible} from '../src/library.js';
import {sourceCatalog} from './reads.mjs';
import {randomUUID,createHash} from 'node:crypto';
import {activeArtifact,reviewCounts,validArtifact,validState,validLinkedResources,reviseArtifact,documentArtifact,libraryItems,saveSnapshot,sourceItems,validRect} from '../src/model.js';
import {contentAssetSources} from '../src/work-surface/validation.js';
import {canConvertArtifactToContent,keepsOriginalImage} from '../src/work-surface/artifact-conversion.js';
import {validSurfaceLayout} from '../src/work-surface/artifact-layout.js';

const fail=(status,message)=>{const e=new Error(message);e.status=status;return e};
const name=v=>typeof v==='string'&&v.trim()&&v.length<=160;
const key=v=>typeof v==='string'&&v.length>=8&&v.length<=160;
const id=v=>typeof v==='string'&&v.length>0&&v.length<=160;
const clone=v=>structuredClone(v);
const canonical=v=>Array.isArray(v)?v.map(canonical):v&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,canonical(v[k])])):v;
const fingerprint=v=>createHash('sha256').update(JSON.stringify(canonical(v))).digest('hex');
const sourceSummary=({id,title,kind,collection,artifactId,artifact,scope,reference,sourceVersion,example})=>({id,title,kind,collection,...(artifact?{artifactId,artifactRevision:artifact.revision}:{}),...(scope?{scope}:{}),...(reference?{reference}:{}),...(sourceVersion?{sourceVersion}:{}),...(example===true?{example:true}:{})});

export function createFlowDomain({read,mutate,workspaceId='workspace',displayName='작업공간',publicUrl='',webLink,assetExists=async()=>false}) {
 const link=(workId,artifactId)=>webLink?webLink(workId,artifactId):publicUrl?publicUrl.replace(/\/$/,'')+'/?work='+encodeURIComponent(workId)+(artifactId?'&artifact='+encodeURIComponent(artifactId):''):null;
 const checkWorkspace=input=>{if(input!==workspaceId)throw fail(404,'등록된 작업공간이 아닙니다.')};
 const getWork=(state,workId)=>{const work=state?.works.find(w=>w.id===workId);if(!work)throw fail(404,'작업을 찾지 못했습니다.');return work};
 const receipt=(state,token)=>state?.changes?.find(c=>c.idempotencyKey===token);
 const approvedAssets=async artifact=>{
  if(artifact?.kind==='html')return Array.isArray(artifact.assets)&&(await Promise.all(artifact.assets.map(asset=>assetExists(asset?.src)))).every(Boolean);
  if(artifact?.kind==='image')return assetExists(artifact.src);
  if(artifact?.kind==='content')return (await Promise.all(contentAssetSources(artifact.composition).map(assetExists))).every(Boolean);
  return true;
 };
 function result(c){const {idempotencyKey,inputHash,...visible}=c;return {...visible,link:link(c.workId,c.artifactId)}}
 async function workspaceList(){return {workspaces:[{id:workspaceId,name:displayName,capabilities:['work.read','work.write','change.submit','change.review'],url:publicUrl||null}]}}
 async function workList({workspaceId:given,query=''}){checkWorkspace(given);const {state,revision}=await read(),counts=reviewCounts(state?.changes);return {stateRevision:revision,works:(state?.works||[]).filter(w=>w.name.toLowerCase().includes(String(query).toLowerCase())).map(w=>{const artifact=activeArtifact(w);return {id:w.id,name:w.name,revision:w.revision||0,purpose:w.purpose||'',artifact:{id:artifact.id,title:artifact.title,kind:artifact.kind,...(artifact.kind==='document'?{format:artifact.format}:{}),revision:artifact.revision},reviewCount:counts.get(w.id)||0,link:link(w.id)}})}}
 async function workRead({workspaceId:given,workId}){checkWorkspace(given);const {state,revision}=await read(),w=getWork(state,workId);return {stateRevision:revision,work:clone(w),sources:sourceItems(state).filter(item=>w.sourceIds.includes(item.id)).map(clone),sourceCatalog:libraryItems(state).filter(item=>!item.scope||libraryEntryVisible(item,workId)||w.sourceIds.includes(item.id)).map(sourceSummary),changes:(state.changes||[]).filter(c=>c.kind==='change'&&c.workId===workId).map(result),link:link(w.id)}}
 async function snapshotList({workspaceId:given,query='',offset=0,limit=50}) {
  checkWorkspace(given);
  if(typeof query!=='string'||query.length>200||!Number.isSafeInteger(offset)||offset<0||!Number.isSafeInteger(limit)||limit<1||limit>100)
   throw fail(422,'검색 범위를 확인해 주세요.');
  const {state}=await read();
  const matching=(state?.librarySnapshots||[]).filter(item=>item.artifact.title.toLocaleLowerCase('ko').includes(query.trim().toLocaleLowerCase('ko'))).slice().reverse();
  return {sources:matching.slice(offset,offset+limit).map(item=>sourceSummary({id:item.id,title:item.artifact.title,kind:'결과물',collection:'보관한 작업물',artifactId:item.artifactId,artifact:item.artifact})),nextOffset:offset+limit<matching.length?offset+limit:null};
 }
 async function snapshotRead({workspaceId:given,sourceId}) {
  checkWorkspace(given);
  if(!id(sourceId))throw fail(422,'보관한 작업물을 선택해 주세요.');
  const {state}=await read(),snapshot=state?.librarySnapshots?.find(item=>item.id===sourceId);
  if(!snapshot)throw fail(404,'보관한 작업물을 찾지 못했습니다.');
  return {source:{...sourceSummary({id:snapshot.id,title:snapshot.artifact.title,kind:'결과물',collection:'보관한 작업물',artifactId:snapshot.artifactId,artifact:snapshot.artifact}),artifact:clone(snapshot.artifact)}};
 }
 async function workCreate({workspaceId:given,name:workName,artifact,sourceId,linkedResources,idempotencyKey}) {
  checkWorkspace(given);
  if(!name(workName)||!key(idempotencyKey)||sourceId!==undefined&&(!id(sourceId)||artifact!==undefined)||
   linkedResources!==undefined&&!validLinkedResources(linkedResources))
   throw fail(422,'작업 이름과 자료, 재시도 식별자를 확인해 주세요.');
  const inputHash=fingerprint({workspaceId:given,name:workName,artifact,sourceId,linkedResources});
  let sourceHash=null;
  if(sourceId){
   const {state}=await read(),previous=receipt(state,idempotencyKey);
   if(!previous){
    const source=state?.librarySnapshots?.find(item=>item.id===sourceId);
    if(!source)throw fail(422,'보관한 작업물을 찾지 못했습니다.');
    sourceHash=fingerprint(source.artifact);
    if(!(await approvedAssets(source.artifact)))throw fail(422,'작업물에 필요한 파일이 등록되어 있지 않습니다.');
   }
  }else if(artifact&&!(await approvedAssets(artifact)))throw fail(422,'작업물에 필요한 파일이 등록되어 있지 않습니다.');
  return mutate(state=>{
   const previous=receipt(state,idempotencyKey);if(previous){if(previous.kind!=='create'||previous.inputHash!==inputHash)throw fail(409,'재시도 식별자가 다른 변경에 사용되었습니다.');return {state,result:{work:getWork(state,previous.workId),link:link(previous.workId,previous.artifactId)}}}
   const source=sourceId?state?.librarySnapshots?.find(item=>item.id===sourceId):null;
   if(sourceId&&(!source||fingerprint(source.artifact)!==sourceHash))throw fail(409,'보관한 작업물이 바뀌었습니다. 다시 확인해 주세요.');
   const initial=source?.artifact??artifact;
   const a=initial?{...clone(initial),id:randomUUID(),revision:0}:{id:randomUUID(),kind:'blank',revision:0,title:''};
   if(!validArtifact(a))throw fail(422,'첫 작업물의 형식이 올바르지 않습니다.');
   const w={id:randomUUID(),revision:0,name:workName.trim(),purpose:'',sourceIds:sourceId?[sourceId]:[],linkedResources:linkedResources??[],activeArtifactId:a.id,artifacts:[a]};
   const base=state||{version:4,libraryEntries:[],workspaceId,theme:'light',activeId:w.id,savedIds:[],works:[],librarySnapshots:[],changes:[]};
   const next={...base,works:[...base.works,w],changes:[...(base.changes||[]),{id:randomUUID(),kind:'create',idempotencyKey,inputHash,workId:w.id,artifactId:a.id,status:'completed'}]};
   return {state:next,result:{work:w,link:link(w.id,a.id)}};
  });
 }
 async function workUpdate({workspaceId:given,workId,expectedRevision,idempotencyKey,...patch}){
  checkWorkspace(given);if(!key(idempotencyKey)||!Number.isSafeInteger(expectedRevision))throw fail(422,'기준 버전과 재시도 식별자가 필요합니다.');
  const allowed=['name','purpose','sourceIds','linkedResources','surfaceLayout','activeArtifactId'];if(!Object.keys(patch).length)throw fail(422,'수정할 작업 맥락이 없습니다.');if(Object.keys(patch).some(k=>!allowed.includes(k)))throw fail(422,'수정 범위를 확인해 주세요.');
  if('name' in patch&&!name(patch.name)||'purpose' in patch&&(typeof patch.purpose!=='string'||patch.purpose.length>10000)||'sourceIds' in patch&&(!Array.isArray(patch.sourceIds)||!patch.sourceIds.every(id))||'linkedResources' in patch&&!validLinkedResources(patch.linkedResources)||'surfaceLayout' in patch&&(!patch.surfaceLayout||Object.keys(patch.surfaceLayout).sort().join(',')!=='order,spans'||!validSurfaceLayout(patch.surfaceLayout)))throw fail(422,'작업 맥락의 형식이 올바르지 않습니다.');
  const inputHash=fingerprint({workspaceId:given,workId,expectedRevision,patch});
  return mutate(state=>{
   const previous=receipt(state,idempotencyKey);if(previous){if(previous.kind!=='work_update'||previous.inputHash!==inputHash)throw fail(409,'재시도 식별자가 다른 변경에 사용되었습니다.');return {state,result:{work:getWork(state,previous.workId),link:link(previous.workId)}}}
   const w=getWork(state,workId);if(patch.activeArtifactId&&!w.artifacts.some(a=>a.id===patch.activeArtifactId))throw fail(422,'작업물을 찾지 못했습니다.');if(patch.sourceIds){const available=new Set(sourceCatalog(state).map(item=>item.id));if(patch.sourceIds.some(value=>!available.has(value)))throw fail(422,'등록되지 않은 자료는 연결할 수 없습니다.')}if(w.revision!==expectedRevision)throw fail(409,'작업 맥락이 변경되었습니다.');
   if(patch.surfaceLayout){const ids=new Set(w.artifacts.map(item=>item.id));if(patch.surfaceLayout.order.some(item=>!ids.has(item))||Object.keys(patch.surfaceLayout.spans).some(item=>!ids.has(item)))throw fail(422,'배치할 작업물을 다시 확인해 주세요.');}
   const updated={...w,...patch,revision:w.revision+1};
   const next={...state,works:state.works.map(item=>item.id===w.id?updated:item),changes:[...(state.changes||[]),{id:randomUUID(),kind:'work_update',idempotencyKey,inputHash,workId:w.id,status:'completed'}]};
   return {state:next,result:{work:updated,link:link(w.id)}};
  });
 }
 async function snapshotCreate({workspaceId:given,workId,artifactId,expectedRevision,idempotencyKey}){
  checkWorkspace(given);
  if(!id(workId)||!id(artifactId)||!Number.isSafeInteger(expectedRevision)||expectedRevision<0||!key(idempotencyKey))throw fail(422,'작업물 버전과 재시도 식별자를 확인해 주세요.');
  const inputHash=fingerprint({workspaceId:given,workId,artifactId,expectedRevision});
  return mutate(state=>{
   const previous=receipt(state,idempotencyKey);
   if(previous){
    if(previous.kind!=='snapshot_create'||previous.inputHash!==inputHash)throw fail(409,'재시도 식별자가 다른 변경에 사용되었습니다.');
    const source=libraryItems(state).find(item=>item.id===previous.sourceId);
    if(!source)throw fail(409,'보관한 작업물을 찾지 못했습니다.');
    return {state,result:{source:sourceSummary(source),link:link(workId,artifactId)}};
   }
   const work=getWork(state,workId),artifact=work.artifacts.find(item=>item.id===artifactId);
   if(!artifact)throw fail(404,'작업물을 찾지 못했습니다.');
   if(artifact.revision!==expectedRevision)throw fail(409,'작업물이 바뀌었습니다. 현재 내용을 확인해 주세요.');
   if(artifact.kind==='blank')throw fail(422,'내용이 없는 작업물은 보관할 수 없습니다.');
   const existing=state.librarySnapshots?.find(item=>item.artifactId===artifactId&&item.artifact.revision===artifact.revision);
   const saved=existing?state:saveSnapshot(state,workId,artifactId),snapshot=existing||saved.librarySnapshots.at(-1);
   const source=libraryItems(saved).find(item=>item.id===snapshot.id);
   const next={...saved,changes:[...(saved.changes||[]),{id:randomUUID(),kind:'snapshot_create',idempotencyKey,inputHash,workId,artifactId,sourceId:snapshot.id,status:'completed'}]};
   return {state:next,result:{source:sourceSummary(source),link:link(workId,artifactId)}};
  });
 }
 function prepare(input,artifact){
  const {mode,selection}=input;
  if(mode==='add'){
   const a={...clone(input.artifact||{}),id:randomUUID(),revision:0};
   if(!validArtifact(a))throw fail(422,'추가할 작업물의 형식이 올바르지 않습니다.');
   return {artifact:a};
  }
  if(!artifact)throw fail(404,'작업물을 찾지 못했습니다.');
  if(!Number.isSafeInteger(input.baseRevision)||input.baseRevision<0)throw fail(422,'작업물 기준 버전이 필요합니다.');
  if(mode==='initialize'){
   if(artifact.kind!=='blank')throw fail(422,'빈 작업물만 처음 내용을 넣을 수 있습니다.');
   const a={...clone(input.artifact||{}),id:artifact.id,revision:input.baseRevision};
   if(a.kind==='blank'||!validArtifact(a))throw fail(422,'처음 넣을 내용의 형식을 확인해 주세요.');
   return {artifact:a};
  }
  if(mode==='proposal'||mode==='replace'){
   const requested=input.artifact?.kind??artifact.kind;
   if(requested!==artifact.kind&&!(requested==='html'&&mode==='replace')&&!(requested==='content'&&canConvertArtifactToContent(artifact)))
    throw fail(422,'이 작업물은 작업 화면으로 전환할 수 없습니다.');
   const a={...clone(input.artifact||{}),id:artifact.id,kind:requested,revision:input.baseRevision};
   if(artifact.kind==='image'&&requested==='content'){
    if(!keepsOriginalImage(artifact,a.composition))throw fail(422,'원본 이미지를 작업 화면에 유지해 주세요.');
   }else if(artifact.kind==='image'&&requested==='image'&&(a.src!==artifact.src||a.width!==artifact.width||a.height!==artifact.height))
    throw fail(422,'이미지 원본은 변경할 수 없습니다.');
   if(!validArtifact(a))throw fail(422,'변경안의 형식이 올바르지 않습니다.');
   return {artifact:a};
  }
  if(mode!=='selection'||!selection||selection.artifactId!==artifact.id)throw fail(422,'선택 범위를 확인해 주세요.');
  if(artifact.kind==='blank')throw fail(422,'빈 작업물에는 먼저 내용을 추가해 주세요.');
  if(artifact.kind==='html'||artifact.kind==='content')throw fail(422,'이 작업물은 기준 버전을 지정해 전체 내용을 수정해 주세요.');
  if(artifact.kind==='document'){
   const block=artifact.blocks.find(b=>b.id===selection.blockId);
   if(!id(selection.blockId)||typeof selection.text!=='string'||!selection.text||(input.baseRevision===artifact.revision&&(!block||block.text.split(selection.text).length!==2))||typeof input.replacement!=='string'||input.replacement.length>20000)throw fail(422,'문서 선택 범위를 확인해 주세요.');
   return {replacement:input.replacement};
  }
  const changes=input.changes;
  if(!changes||typeof changes!=='object'||Array.isArray(changes)||!Object.keys(changes).length)throw fail(422,'수정할 필드를 확인해 주세요.');
  const allowed=artifact.kind==='image'?['crop','alt']:selection.objectKind==='node'?['label','detail']:['label'];
  if(Object.keys(changes).some(k=>!allowed.includes(k)))throw fail(422,'선택하지 않은 부분은 수정할 수 없습니다.');
  if(artifact.kind==='image'){
   if(!validRect(selection.rect)||('crop' in changes&&changes.crop!==null&&!validRect(changes.crop)))throw fail(422,'이미지 선택 영역을 다시 확인해 주세요.');
   if('alt' in changes&&(typeof changes.alt!=='string'||changes.alt.length>1000))throw fail(422,'이미지 설명이 올바르지 않습니다.');
  }else{
   const item=(selection.objectKind==='node'?artifact.nodes:artifact.edges).find(v=>v.id===selection.objectId);
   if((input.baseRevision===artifact.revision&&!item)||Object.values(changes).some(v=>typeof v!=='string'||v.length>2000))throw fail(422,'도식 선택 객체를 확인해 주세요.');
  }
  return {changes};
 }
 function applyProposal(state,c){
  const w=getWork(state,c.workId),a=w.artifacts.find(v=>v.id===c.artifactId);
  if(!a||a.revision!==c.baseRevision)return {state,change:{...c,status:'conflict'}};
  let updates;
  if(c.mode==='selection'){
   if(a.kind==='document'){
    const block=a.blocks.find(b=>b.id===c.selection.blockId),text=c.selection.text;
    if(!block||block.text.split(text).length!==2)return {state,change:{...c,status:'conflict'}};
    updates={blocks:a.blocks.map(b=>b.id===block.id?{...b,text:b.text.replace(text,c.proposal.replacement)}:b)};
   }else if(a.kind==='image')updates=c.proposal.changes;
   else{
    const field=c.selection.objectKind==='node'?'nodes':'edges',item=a[field].find(v=>v.id===c.selection.objectId);
    if(!item)return {state,change:{...c,status:'conflict'}};
    updates={[field]:a[field].map(v=>v.id===item.id?{...v,...c.proposal.changes}:v)};
   }
  }else updates=c.proposal.artifact;
  const next=reviseArtifact(state,w.id,a.id,updates,c.baseRevision);
  return {state:next,change:{...c,status:'completed',before:clone(a),appliedRevision:a.revision+1}};
 }
 async function changeSubmit(input){
  checkWorkspace(input.workspaceId);if(['add','proposal','initialize','replace'].includes(input.mode)&&input.artifact&&!(await approvedAssets(input.artifact)))throw fail(422,'작업물에 필요한 파일이 등록되어 있지 않습니다.');if(!key(input.idempotencyKey)||!id(input.workId))throw fail(422,'작업과 재시도 식별자가 필요합니다.');
  const {idempotencyKey,...logical}=input,inputHash=fingerprint(logical);
  return mutate(state=>{
   const prior=receipt(state,input.idempotencyKey);if(prior){if(prior.kind!=='change'||prior.inputHash!==inputHash)throw fail(409,'재시도 식별자가 다른 변경에 사용되었습니다.');return {state,result:{change:result(prior)}}}
   const w=getWork(state,input.workId),a=w.artifacts.find(v=>v.id===input.artifactId);
   if(['initialize','replace'].includes(input.mode)&&a&&a.revision!==input.baseRevision)throw fail(409,'작업물이 바뀌었습니다. 현재 내용을 확인해 주세요.');
   const proposal=prepare(input,a);
   if(input.mode==='add'){
    if(w.artifacts.some(v=>v.id===proposal.artifact.id))throw fail(409,'작업물 ID가 중복되었습니다.');
    const updated={...w,artifacts:[...w.artifacts,proposal.artifact],activeArtifactId:proposal.artifact.id};
    const c={id:randomUUID(),kind:'change',idempotencyKey:input.idempotencyKey,inputHash,workId:w.id,artifactId:proposal.artifact.id,mode:'add',status:'completed'};
    const next={...state,works:state.works.map(v=>v.id===w.id?updated:v),changes:[...(state.changes||[]),c]};
    return {state:next,result:{change:result(c)}};
   }
   const c={id:randomUUID(),kind:'change',idempotencyKey:input.idempotencyKey,inputHash,workId:w.id,artifactId:a.id,mode:input.mode,baseRevision:input.baseRevision,selection:input.selection||null,proposal,status:input.mode==='proposal'?'review':'pending'};
   const applied=['selection','initialize','replace'].includes(input.mode)?applyProposal(state,c):{state,change:c};
   const next={...applied.state,changes:[...(applied.state.changes||[]),applied.change]};
   return {state:next,result:{change:result(applied.change)}};
  });
 }
 async function changeAction({workspaceId:given,changeId,action}){
  checkWorkspace(given);
  return mutate(state=>{
   const c=state?.changes?.find(v=>v.id===changeId);if(!c||c.kind!=='change')throw fail(404,'변경안을 찾지 못했습니다.');
   if(action==='apply'&&c.status==='completed'||action==='undo'&&c.status==='undone')return {state,result:{change:result(c)}};
   let next=state,updated;
   if(action==='apply'){
    if(!['review','conflict'].includes(c.status))throw fail(409,'적용할 수 없는 상태입니다.');
    ({state:next,change:updated}=applyProposal(state,c));
   }else if(action==='undo'){
    if(c.status!=='completed'||(!c.before&&c.mode!=='add'))throw fail(409,'되돌릴 수 없는 변경입니다.');
    const w=getWork(state,c.workId),a=w.artifacts.find(v=>v.id===c.artifactId);
    if(c.mode==='add'){if(!a||a.revision!==0||w.artifacts.length<2)throw fail(409,'그 뒤에 작업물이 수정되었습니다.');const remaining=w.artifacts.filter(v=>v.id!==a.id);next={...state,works:state.works.map(v=>v.id===w.id?{...w,artifacts:remaining,activeArtifactId:w.activeArtifactId===a.id?remaining[0].id:w.activeArtifactId}:v)};}
    else{if(!a||a.revision!==c.appliedRevision)throw fail(409,'그 뒤에 작업물이 수정되었습니다.');next=reviseArtifact(state,w.id,a.id,c.before,c.appliedRevision)}
    updated={...c,status:'undone'};
   }else throw fail(422,'변경 동작을 확인해 주세요.');
   next={...next,changes:next.changes.map(v=>v.id===c.id?updated:v)};
   return {state:next,result:{change:result(updated)}};
  });
 }

 async function libraryList({workspaceId:given,query='',workId,offset=0,limit=50}){
  checkWorkspace(given);
  if(typeof query!=='string'||query.length>200||!Number.isSafeInteger(offset)||offset<0||!Number.isSafeInteger(limit)||limit<1||limit>100)throw fail(422,'검색 범위를 확인해 주세요.');
  const {state}=await read();if(workId)getWork(state,workId);
  const term=query.trim().toLocaleLowerCase('ko');
  const entries=(state?libraryItems(state):[]).filter(e=>(!e.scope||libraryEntryVisible(e,workId))&&[e.title,e.body||''].join(' ').toLocaleLowerCase('ko').includes(term));
  return {entries:entries.slice(offset,offset+limit).map(({body,artifact,blocks,...entry})=>entry),nextOffset:offset+limit<entries.length?offset+limit:null};
 }
 async function libraryRead({workspaceId:given,entryId}){
  checkWorkspace(given);const {state}=await read();
  const entry=state?.libraryEntries?.find(e=>e.id===entryId)||(state&&libraryItems(state).find(e=>e.id===entryId));if(!entry)throw fail(404,'자료를 찾지 못했습니다.');
  return {entry:clone(entry)};
 }
 async function libraryUpsert({workspaceId:given,entry,expectedRevision,idempotencyKey}){
  checkWorkspace(given);
  if(!key(idempotencyKey)||!validLibraryEntry({...entry,revision:0})||!Number.isSafeInteger(expectedRevision)||expectedRevision<0)throw fail(422,'자료 내용과 기준 버전을 확인해 주세요.');
  const inputHash=fingerprint({workspaceId:given,entry,expectedRevision});
  return mutate(state=>{
   if(!state)throw fail(409,'먼저 작업공간을 열어 주세요.');
   const prior=receipt(state,idempotencyKey);
   if(prior){if(prior.kind!=='library_upsert'||prior.inputHash!==inputHash)throw fail(409,'재시도 식별자가 다른 변경에 사용되었습니다.');return {state,result:{entry:state.libraryEntries.find(e=>e.id===prior.entryId)}}}
   if(entry.scope.kind==='work')getWork(state,entry.scope.workId);
   const identity=libraryEntryKey(entry);
   const existing=state.libraryEntries.find(e=>e.id===entry.id);
   if(!existing&&sourceCatalog(state).some(item=>item.id===entry.id))throw fail(409,'기존 자료와 식별자가 겹칩니다.');
   const duplicate=identity&&state.libraryEntries.find(e=>e.id!==entry.id&&libraryEntryKey(e)===identity&&(e.body||'').trim()===(entry.body||'').trim());
   if(duplicate)throw fail(409,'같은 자료가 이미 있습니다. 기존 자료를 확인해 주세요.');
   if((existing?.revision??0)!==expectedRevision)throw fail(409,'자료가 바뀌었습니다. 현재 내용을 확인해 주세요.');
   const updated={...clone(entry),revision:(existing?.revision??0)+1};
   const next={...state,libraryEntries:existing?state.libraryEntries.map(e=>e.id===entry.id?updated:e):[...state.libraryEntries,updated],
    changes:[...(state.changes||[]),{id:randomUUID(),kind:'library_upsert',entryId:entry.id,idempotencyKey,inputHash,status:'completed'}]};
   return {state:next,result:{entry:clone(updated)}};
  });
 }

 return {libraryList,libraryRead,libraryUpsert,workspaceList,workList,workRead,workCreate,workUpdate,snapshotList,snapshotRead,snapshotCreate,changeSubmit,changeAction};
}
