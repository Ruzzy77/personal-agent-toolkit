import {validHtmlArtifact} from './work-surface/html-artifact.js';
import {validLibraryEntry,libraryEntrySource} from './library.js';
import {initialFiles,workspaceFiles,fileSource} from './file-model.js';
import {validSurfaceLayout} from './surface-layout.js';
import {validContentComposition} from './work-surface/validation.js';
import {validLinkedResources} from './work-surface/resource-reference.js';
import {validImageRegion} from './work-surface/image-region.js';
import {stackComposition} from './work-surface/composition.js';
import {newContentBlock,workspaceFileBlock} from './work-surface/composition-editor.js';
import {fileContentUrl} from './file-preview.js';
import {layoutDiagramNodes} from './work-surface/diagram-layout.js';
export const STORAGE_KEY = 'toolkit-flow-v3';
export const V2_STORAGE_KEY = 'toolkit-flow-v2';
export const LEGACY_STORAGE_KEY = 'toolkit-flow-v1';
export const references = [
 {id:'observation',kind:'자료',collection:'검사 연구',title:'현장 관찰 메모',body:'새로운 제품과 공정 조건에서는 기존 모델의 판단이 달라질 수 있다. 이미 쌓인 검사 결과를 다시 살펴보고, 어떤 차이가 생기는지부터 확인한다.\n\n제품, 촬영 조건, 오류 유형을 나누어 관찰한다. 비교할 대상을 좁힌 뒤 필요한 데이터를 정리한다.'},
 {id:'questions',kind:'자료',collection:'검사 연구',title:'비교할 조건과 질문',body:'어떤 조건에서 판단이 달라지는가?\n이미 모인 검사 결과로 무엇을 확인할 수 있는가?\n새로 수집해야 할 데이터는 무엇인가?\n\n관찰한 차이를 바탕으로 확인할 질문과 비교 방법을 정한다.'},
 {id:'presentation',kind:'디자인',collection:'표현',title:'간결한 발표 구성',body:'한 화면에서 하나의 질문을 다룹니다. 제목으로 논점을 먼저 보여주고, 본문은 그 설명을 맡습니다.',format:'slides'},
 {id:'writing',kind:'자료',collection:'표현',title:'문장을 덜어내는 기준',body:'독자가 이미 아는 설명은 반복하지 않는다. 한 문장에는 하나의 중심 내용을 남긴다. 뜻을 바꾸지 않는 범위에서 수식어와 같은 의미의 표현을 덜어낸다.'},
];

function legacySeed(){
 return {version:1,activeId:'research',theme:'light',savedIds:[],works:[{
  id:'research',name:'연구 발표',title:'검사 데이터를 다시 보는 이유',kind:'발표 구성',
  sourceIds:['observation','questions'],format:'document',lastBlockId:'compare',
  purpose:'기존 검사 데이터를 다시 살펴볼 이유와 다음 실험의 질문을 발표로 정리한다.',
  blocks:[
   {id:'problem',heading:'문제에서 시작하기',text:'새로운 제품과 공정 조건에서는 기존 모델의 판단이 달라질 수 있다. 이미 쌓인 검사 결과를 다시 살펴보고, 어떤 차이가 생기는지부터 확인한다.'},
   {id:'compare',heading:'비교할 조건 정하기',text:'제품, 촬영 조건, 오류 유형을 나누어 관찰한다. 비교할 대상을 좁힌 뒤 필요한 데이터를 정리한다.'},
   {id:'experiment',heading:'다음 실험으로 연결하기',text:'관찰한 차이를 바탕으로 확인할 질문과 비교 방법을 정한다.'},
  ]
 }]};
}

export const activeArtifact = work => work.artifacts.find(a=>a.id===work.activeArtifactId)||work.artifacts[0];
export const artifactKind = a => ({document:a.format==='slides'?'발표 자료':'문서',image:'이미지',diagram:'도식',content:'작업 화면'})[a.kind]||'작업물';
export function artifactText(a){
 if(a.kind==='document')return a.blocks.map(b=>b.heading+'\n'+b.text).join('\n\n');
 if(a.kind==='diagram')return a.nodes.map(n=>n.label).join('\n');
 if(a.kind==='content')return a.composition.blocks.map(block=>{const c=block.content;return [c.title,c.heading,c.description,c.caption,c.name,c.code,...(c.paragraphs||[]),...(c.items||[]).map(item=>[item.title,item.label,item.detail,item.value].filter(v=>v!==undefined).join(' ')),...(c.steps||[]).map(step=>[step.title,step.text].filter(Boolean).join(' ')),...(c.nodes||[]).map(node=>[node.label,node.detail].filter(Boolean).join(' ')),...(c.edges||[]).map(edge=>edge.label).filter(Boolean)].filter(Boolean).join('\n')}).filter(Boolean).join('\n\n');
 if(a.kind==='html')return a.title;
 if(a.kind==='blank')return a.draftText||a.title;
 return a.alt||a.title;
}
export function initialState(){return migrateState(legacySeed())}
export function emptyState(workspaceId='workspace'){const work=newWork('새 작업');return {version:4,libraryEntries:[],workspaceId,theme:'light',activeId:work.id,savedIds:[],works:[work],librarySnapshots:[],changes:[],files:[],libraryFileIds:[],linkedFiles:[],collectedFiles:[]}}
export function migrateState(state,workspaceId='workspace'){
 if(state?.version===4){if(!validState(state))throw new Error('저장 형식을 읽을 수 없습니다.');return state}
 if(state?.version===3){const next={...state,version:4,libraryEntries:[]};if(!validState(next))throw new Error('기존 작업을 읽을 수 없습니다.');return withSnapshots(next)}
 if(state?.version===2){const next={...state,version:4,libraryEntries:[],workspaceId,legacyReferences:true,works:state.works.map(w=>({...w,revision:0}))};if(!validState(next))throw new Error('저장 형식을 읽을 수 없습니다.');return withSnapshots(next)}
 if(state?.version!==1||!Array.isArray(state.works)||!state.works.length||!state.works.every(w=>Array.isArray(w.blocks)&&w.blocks.length))throw new Error('기존 작업을 읽을 수 없습니다.');
 const result=withFiles({...state,version:4,libraryEntries:[],workspaceId,legacyReferences:true,works:state.works.map(w=>{
  const {title,kind,format,blocks,lastBlockId,...context}=w;
  return {...context,revision:0,activeArtifactId:w.id,artifacts:[{id:w.id,kind:'document',title,format:format||'document',blocks,lastBlockId:lastBlockId||null,revision:0}]};
 })});
 if(!validState(result))throw new Error('기존 작업을 변환하지 못했습니다.');
 return withSnapshots(result);
}
function withSnapshots(state){
 if(Array.isArray(state.librarySnapshots))return state;
 const librarySnapshots=state.works.flatMap(w=>w.artifacts.filter(a=>state.savedIds.includes(a.id)).map(a=>({id:a.id,workId:w.id,artifactId:a.id,artifact:structuredClone(a)})));
 return {...state,librarySnapshots};
}
export function saveSnapshot(state,workId,artifactId){
 const artifact=state.works.find(w=>w.id===workId)?.artifacts.find(a=>a.id===artifactId);
 if(!artifact)throw new Error('작업물을 찾지 못했습니다.');
 const snapshot={id:'snapshot:'+crypto.randomUUID(),workId,artifactId,artifact:structuredClone(artifact),createdAt:new Date().toISOString()};
 return {...state,savedIds:state.savedIds.includes(artifactId)?state.savedIds:[...state.savedIds,artifactId],librarySnapshots:[...(state.librarySnapshots||[]),snapshot]};
}
const text=v=>typeof v==='string';
const unique=items=>new Set(items).size===items.length;
export {validContextLocator,resourceReferenceKey as linkedResourceKey,validLinkedResources} from './work-surface/resource-reference.js';
export const validRect=validImageRegion;
export function validArtifact(a){
 if(!a||!text(a.id)||!text(a.title)||!Number.isSafeInteger(a.revision)||a.revision<0)return false;
 if(a.kind==='html')return validHtmlArtifact(a);
 if(a.kind==='blank')return a.draftText===undefined||text(a.draftText);
 if(a.kind==='document')return ['document','slides'].includes(a.format)&&Array.isArray(a.blocks)&&a.blocks.length>0&&unique(a.blocks.map(b=>b.id))&&a.blocks.every(b=>text(b.id)&&text(b.heading)&&text(b.text));
 if(a.kind==='image')return text(a.src)&&(/^\/api\/flow\/assets\/[0-9a-f-]+\.(png|jpg|webp|gif)$/.test(a.src)||/^data:image\/(png|jpeg|webp|gif);base64,[A-Za-z0-9+/=]+$/.test(a.src))&&Number.isFinite(a.width)&&a.width>0&&Number.isFinite(a.height)&&a.height>0&&(!a.crop||validRect(a.crop));
 if(a.kind==='diagram')return Array.isArray(a.nodes)&&Array.isArray(a.edges)&&unique(a.nodes.map(n=>n.id))&&unique(a.edges.map(e=>e.id))&&a.nodes.every(n=>text(n.id)&&text(n.label)&&Number.isFinite(n.x)&&Number.isFinite(n.y)&&n.x>=0&&n.x<=1000&&n.y>=0&&n.y<=600)&&a.edges.every(e=>text(e.id)&&text(e.label)&&a.nodes.some(n=>n.id===e.from)&&a.nodes.some(n=>n.id===e.to));
 if(a.kind==='content')return validContentComposition(a.composition);
 return false;
}
export function validState(s){
 return s?.version===4&&text(s.workspaceId)&&['light','dark'].includes(s.theme)&&Array.isArray(s.libraryEntries)&&s.libraryEntries.every(validLibraryEntry)&&unique(s.libraryEntries.map(e=>e.id))&&Array.isArray(s.works)&&s.libraryEntries.every(e=>e.scope.kind!=='work'||s.works.some(w=>w.id===e.scope.workId))&&s.works.length>0&&Array.isArray(s.savedIds)&&s.savedIds.every(text)&&unique(s.works.map(w=>w.id))&&s.works.some(w=>w.id===s.activeId)&&
 s.works.every(w=>text(w.id)&&Number.isSafeInteger(w.revision)&&w.revision>=0&&text(w.name)&&text(w.purpose||'')&&Array.isArray(w.sourceIds)&&w.sourceIds.every(text)&&(w.linkedResources===undefined||validLinkedResources(w.linkedResources))&&Array.isArray(w.artifacts)&&w.artifacts.length>0&&w.artifacts.some(a=>a.id===w.activeArtifactId)&&w.artifacts.every(validArtifact)&&validSurfaceLayout(w.surfaceLayout))&&unique(s.works.flatMap(w=>w.artifacts.map(a=>a.id)))&&
 (!s.librarySnapshots||Array.isArray(s.librarySnapshots)&&s.librarySnapshots.every(v=>text(v.id)&&text(v.workId)&&text(v.artifactId)&&validArtifact(v.artifact)))&&
 (!s.linkedFiles||Array.isArray(s.linkedFiles)&&s.linkedFiles.every(v=>text(v.id)&&text(v.filePath)&&text(v.title)&&text(v.body)))&&
 (!s.collectedFiles||Array.isArray(s.collectedFiles)&&s.collectedFiles.every(v=>text(v.id)&&text(v.filePath)&&text(v.title)&&text(v.body)));
}
export function blankArtifact(title=''){return {id:crypto.randomUUID(),kind:'blank',revision:0,title:title.trim()}}
export function contentArtifact(title=''){
 const block=newContentBlock('text',crypto.randomUUID());
 return {id:crypto.randomUUID(),kind:'content',revision:0,title:title.trim(),composition:stackComposition([block])};
}
export function uploadedImageArtifact(title,name,uploaded){
 const block=newContentBlock('image',crypto.randomUUID());
 block.content={...block.content,src:uploaded?.src,alt:name,width:uploaded?.width,height:uploaded?.height};
 const artifact={id:crypto.randomUUID(),kind:'content',revision:0,title:title.trim(),composition:stackComposition([block])};
 if(!validArtifact(artifact))throw new Error('이 이미지를 작업 화면에 넣을 수 없습니다.');
 return artifact;
}
export function workspaceFileSource(file){
 if(!file||typeof file.name!=='string'||!file.name||typeof file.path!=='string'||!file.path)throw new Error('파일을 확인해 주세요.');
 return {id:'workspace-file:'+file.path,filePath:file.path,title:file.name,kind:'자료',collection:'연결한 파일',body:'',live:true};
}
export function workspaceFileArtifact(file,workspaceId){
 if(!file||typeof file.name!=='string'||!file.name||typeof file.path!=='string')throw new Error('파일을 확인해 주세요.');
 const block=workspaceFileBlock(file.path,fileContentUrl(workspaceId,file.path));
 const artifact={id:crypto.randomUUID(),kind:'content',revision:0,title:file.name.replace(/\.[^.]+$/,''),composition:stackComposition([block])};
 if(!validArtifact(artifact))throw new Error('이 파일을 작업 화면에 넣을 수 없습니다.');
 return artifact;
}
export function documentArtifact(title,source){
 const id=crypto.randomUUID(),paragraphs=source?.blocks||(source?.body||'').split(/\n\s*\n/).filter(Boolean).map(text=>({heading:'',text}));
 return {id,kind:'document',revision:0,title:title.trim(),format:'document',lastBlockId:null,blocks:(paragraphs.length?paragraphs:[{heading:'',text:''}]).map((b,i)=>({...b,id:id+'-'+i}))};
}
export function sourceContentArtifact(title,source){
 if(typeof title!=='string'||!source||typeof source!=='object')return null;
 const parts=Array.isArray(source.blocks)&&source.blocks.length
  ?source.blocks.map(block=>({id:crypto.randomUUID(),kind:'text',content:{heading:block?.heading,paragraphs:[block?.text]}}))
  :[{id:crypto.randomUUID(),kind:'text',content:{
   heading:typeof source.title==='string'&&source.title!==title?source.title:'',
   paragraphs:[source.body??'']
  }}];
 try{
  const artifact={id:crypto.randomUUID(),kind:'content',revision:0,title:title.trim(),composition:stackComposition(parts)};
  return validArtifact(artifact)?artifact:null;
 }catch{return null}
}
export function reviewCounts(changes){
 const counts=new Map();
 for(const change of Array.isArray(changes)?changes:[]){
  if(change?.kind!=='change'||!['review','conflict'].includes(change.status)||typeof change.workId!=='string')continue;
  counts.set(change.workId,(counts.get(change.workId)||0)+1);
 }
 return counts;
}
export function workPickerGroups(works,currentId,recentIds=[]){
 const byId=new Map(works.map(item=>[item.id,item])),seen=new Set(),recent=[];
 for(const id of [currentId,...(Array.isArray(recentIds)?recentIds:[])]){
  const item=byId.get(id);
  if(item&&!seen.has(id)){seen.add(id);recent.push(item)}
  if(recent.length===5)break;
 }
 return {recent,other:works.filter(item=>!seen.has(item.id)).slice().reverse()};
}
export function newWork(name,source){
 const a=source?.artifact?{...structuredClone(source.artifact),id:crypto.randomUUID(),revision:0}:blankArtifact();
 return {id:crypto.randomUUID(),revision:0,name:name.trim(),purpose:'',sourceIds:source?[source.id]:[],linkedResources:[],activeArtifactId:a.id,artifacts:[a]};
}
export function diagramArtifact(title,document){
 const blocks=document?.blocks||[];
 const nodes=(blocks.length?blocks:[{heading:'',text:''}]).map((b,i)=>({id:crypto.randomUUID(),label:b.heading||(document?'항목 '+(i+1):'새 항목'),detail:b.text||'',x:500,y:100+i*160}));
 const laid=layoutDiagramNodes(nodes,'vertical');
 return {id:crypto.randomUUID(),title,kind:'diagram',revision:0,nodes:laid,edges:laid.slice(1).map((n,i)=>({id:crypto.randomUUID(),from:laid[i].id,to:n.id,label:''}))};
}
export const layoutNodes=layoutDiagramNodes;
export function reviseArtifact(state,workId,artifactId,updates,expectedRevision){
 return {...state,works:state.works.map(w=>w.id!==workId?w:{...w,artifacts:w.artifacts.map(a=>{
  if(a.id!==artifactId)return a;
  if(expectedRevision!==undefined&&a.revision!==expectedRevision)throw new Error('작업물이 변경되었습니다. 현재 내용을 확인한 뒤 다시 수정해 주세요.');
  const next={...a,...(typeof updates==='function'?updates(a):updates),id:a.id,revision:a.revision+1};
  if(a.kind!==next.kind){
   const fields={blank:['draftText'],document:['format','blocks','lastBlockId'],image:['src','alt','width','height','crop'],diagram:['nodes','edges'],content:['composition'],html:['html','assets']};
   for(const [kind,keys] of Object.entries(fields))if(kind!==next.kind)for(const key of keys)delete next[key];
  }
  if(next.kind==='blank'&&a.kind!=='blank'){const blank={id:a.id,kind:'blank',revision:next.revision,title:next.title,...(next.draftText===undefined?{}:{draftText:next.draftText})};if(!validArtifact(blank))throw new Error('이 변경을 적용할 수 없습니다.');return blank}
  if(!validArtifact(next))throw new Error('이 변경을 적용할 수 없습니다.');
  return next;
 })})};
}
export function withFiles(state){
 return {...state,screen:['work','library','files'].includes(state.screen)?state.screen:'work',
  files:Array.isArray(state.files)?state.files:state.legacyReferences?initialFiles(references):[],
  libraryFileIds:Array.isArray(state.libraryFileIds)?state.libraryFileIds:[],
  filesLocation:state.filesLocation||{path:'',selected:null}};
}
export function libraryItems(state){
 const files=workspaceFiles(state);
 const base=(state.legacyReferences?references:[]).map(ref=>{const file=files.find(f=>f.sourceId===ref.id),source=file?{...ref,fileId:file.id,path:file.path,body:file.content}:ref;return {...source,example:true,collection:'예시 자료'}});
 const snapshots=state.librarySnapshots||[];
 const saved=snapshots.map(v=>({id:v.id,workId:v.workId,artifactId:v.artifactId,artifact:v.artifact,kind:'결과물',collection:'보관한 작업물',title:v.artifact.title,body:artifactText(v.artifact),blocks:v.artifact.blocks}));
 const unsnapshotted=state.works.flatMap(w=>w.artifacts.filter(a=>state.savedIds.includes(a.id)&&!snapshots.some(v=>v.artifactId===a.id)).map(a=>({id:a.id,workId:w.id,artifactId:a.id,artifact:a,kind:'결과물',collection:'보관한 작업물',title:a.title,body:artifactText(a),blocks:a.blocks})));
 const collected=(state.libraryFileIds||[]).map(id=>files.find(f=>f.id===id)).filter(Boolean).map(fileSource).filter(item=>!base.some(b=>b.id===item.id)&&!saved.some(b=>b.id===item.id));
 return [...(state.libraryEntries||[]).map(libraryEntrySource),...base,...saved,...unsnapshotted,...(state.collectedFiles||[]),...collected];
}
export function sourceItems(state){
 const items=libraryItems(state);
 return [...items,...(state.linkedFiles||[]).filter(f=>!items.some(i=>i.id===f.id)),...workspaceFiles(state).filter(f=>f.type==='file').map(fileSource).filter(f=>!items.some(i=>i.id===f.id))];
}
