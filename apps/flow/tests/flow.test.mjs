import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm,writeFile,readFile,mkdir} from 'node:fs/promises';
import {createServer} from 'node:http';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {randomUUID,createHash} from 'node:crypto';
import {createWorkspaceService} from '../server/workspace.mjs';
import {initialState,newWork,workspaceFileSource,saveSnapshot,blankArtifact,contentArtifact,documentArtifact,diagramArtifact,workspaceFileArtifact,artifactText,validArtifact,validState,reviseArtifact,workPickerGroups,reviewCounts} from '../src/model.js';
import {catalogSections} from '../src/examples/catalog.js';
import {stackComposition} from '../src/work-surface/composition.js';
import {appendContentBlock,newContentBlock} from '../src/work-surface/composition-editor.js';
import {contentFromArtifact} from '../src/work-surface/artifact-conversion.js';
import {mergeStates} from '../src/merge.js';

async function fixture(fn){
 const directory=await mkdtemp(join(tmpdir(),'flow-domain-'));
 try{
  const service=createWorkspaceService(directory,{workspaceId:'workspace',publicUrl:'http://127.0.0.1:4176'});
  await service.put(initialState(),'empty');
  return await fn(service,directory);
 }finally{await rm(directory,{recursive:true,force:true})}
}
test('work picker keeps other works reachable after a recent selection',()=>{
 const works=[{id:'first',name:'첫 작업'},{id:'second',name:'다른 작업'}];
 const selected=workPickerGroups(works,'second',['second']);
 assert.deepEqual(selected.recent.map(item=>item.id),['second']);
 assert.deepEqual(selected.other.map(item=>item.id),['first']);
 const returned=workPickerGroups(works,'first',['first','second']);
 assert.deepEqual(returned.recent.map(item=>item.id),['first','second']);
 assert.deepEqual(returned.other,[]);
 assert.deepEqual(workPickerGroups(works,'first',{}).other.map(item=>item.id),['second']);
});

test('only open revision proposals count toward each work',()=>{
 const counts=reviewCounts([
  {kind:'change',workId:'first',status:'review'},
  {kind:'change',workId:'first',status:'conflict'},
  {kind:'change',workId:'first',status:'completed'},
  {kind:'change',workId:'second',status:'review'},
  {kind:'work_update',workId:'second',status:'review'},
 ]);
 assert.equal(counts.get('first'),2);
 assert.equal(counts.get('second'),1);
 assert.equal(counts.has('other'),false);
});
test('Flow Library sources can be discovered and linked without copying their content into the catalog',async()=>fixture(async({domain,read,put})=>{
 const current=await read();
 const work=current.state.works[0],artifact=work.artifacts[0];
 const saved=saveSnapshot(current.state,work.id,artifact.id);
 await put(saved,current.revision);
 const before=await domain.workRead({workspaceId:'workspace',workId:work.id});
 const source=saved.librarySnapshots.at(-1);
 const candidate=before.sourceCatalog.find(item=>item.id===source.id);
 assert.deepEqual(candidate,{id:source.id,title:artifact.title,kind:'결과물',collection:'보관한 작업물',artifactId:artifact.id,artifactRevision:artifact.revision});
 assert.ok(before.sourceCatalog.every(item=>!('artifact' in item)&&!('body' in item)&&!('composition' in item)));
 const updated=await domain.workUpdate({workspaceId:'workspace',workId:work.id,expectedRevision:before.work.revision,
  idempotencyKey:randomUUID(),sourceIds:[source.id]});
 assert.deepEqual(updated.work.sourceIds,[source.id]);
 const after=await domain.workRead({workspaceId:'workspace',workId:work.id});
 assert.deepEqual(after.sources[0].artifact,artifact);
 assert.equal(after.sources[0].id,source.id);
}));
test('work list revision changes when a review proposal arrives',async()=>fixture(async({domain,read})=>{
 const {state}=await read(),work=state.works[0],artifact=work.artifacts[0];
 const beforeList=await domain.workList({workspaceId:'workspace'});
 const beforeRead=await domain.workRead({workspaceId:'workspace',workId:work.id});
 assert.equal(beforeList.stateRevision,beforeRead.stateRevision);
 assert.equal(beforeList.works.find(item=>item.id===work.id).reviewCount,0);
 const proposal={...artifact,title:artifact.title+' 수정안'};
 const submitted=await domain.changeSubmit({workspaceId:'workspace',workId:work.id,mode:'proposal',
  artifactId:artifact.id,baseRevision:artifact.revision,artifact:proposal,idempotencyKey:randomUUID()});
 assert.equal(submitted.change.status,'review');
 const afterList=await domain.workList({workspaceId:'workspace'});
 const afterRead=await domain.workRead({workspaceId:'workspace',workId:work.id});
 assert.notEqual(afterList.stateRevision,beforeList.stateRevision);
 assert.equal(afterList.stateRevision,afterRead.stateRevision);
 assert.equal(afterList.works.find(item=>item.id===work.id).revision,beforeList.works.find(item=>item.id===work.id).revision);
 assert.equal(afterList.works.find(item=>item.id===work.id).reviewCount,1);
 assert.ok(afterRead.changes.some(change=>change.id===submitted.change.id&&change.status==='review'));
 await domain.changeAction({workspaceId:'workspace',changeId:submitted.change.id,action:'apply'});
 assert.equal((await domain.workList({workspaceId:'workspace'})).works.find(item=>item.id===work.id).reviewCount,0);
}));
test('work list describes the active artifact, not the first artifact',async()=>fixture(async({domain,read})=>{
 const {state}=await read(),work=state.works[0];
 const original=work.artifacts[0];
 const added=await domain.changeSubmit({workspaceId:'workspace',workId:work.id,mode:'add',
  artifact:contentArtifact('새 작업 화면'),idempotencyKey:randomUUID()});
 const listed=(await domain.workList({workspaceId:'workspace'})).works.find(item=>item.id===work.id);
 assert.notEqual(listed.artifact.id,original.id);
 assert.equal(listed.artifact.id,added.change.artifactId);
 assert.equal(listed.artifact.kind,'content');
 assert.equal(listed.artifact.title,'새 작업 화면');
}));
test('the Flow HTTP list and read expose the same state revision',async()=>fixture(async service=>{
 const work=(await service.domain.workList({workspaceId:'workspace'})).works[0];
 const server=createServer((request,response)=>service.handler(request,response,()=>{response.writeHead(404);response.end()}));
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const base='http://127.0.0.1:'+server.address().port;
 try{
  const listResponse=await fetch(base+'/api/flow/works?workspaceId=workspace');
  const readResponse=await fetch(base+'/api/flow/works/'+encodeURIComponent(work.id)+'?workspaceId=workspace');
  assert.equal(listResponse.status,200);
  assert.equal(readResponse.status,200);
  const list=await listResponse.json(),read=await readResponse.json();
  assert.match(list.stateRevision,/^[a-f0-9]{64}$/);
  assert.equal(read.stateRevision,list.stateRevision);
  assert.equal(read.work.id,work.id);
 }finally{await new Promise(resolve=>server.close(resolve))}
}));
test('saved Flow work is searchable and readable before any work is selected',async()=>fixture(async({domain})=>{
 const first=await domain.workCreate({workspaceId:'workspace',name:'첫 작업',artifact:contentArtifact('첫 화면'),idempotencyKey:randomUUID()});
 const older=await domain.snapshotCreate({workspaceId:'workspace',workId:first.work.id,artifactId:first.work.artifacts[0].id,expectedRevision:0,idempotencyKey:randomUUID()});
 const second=await domain.workCreate({workspaceId:'workspace',name:'둘째 작업',artifact:contentArtifact('둘째 화면'),idempotencyKey:randomUUID()});
 const newer=await domain.snapshotCreate({workspaceId:'workspace',workId:second.work.id,artifactId:second.work.artifacts[0].id,expectedRevision:0,idempotencyKey:randomUUID()});
 const page=await domain.snapshotList({workspaceId:'workspace',limit:1});
 assert.equal(page.sources[0].id,newer.source.id);
 assert.equal(page.nextOffset,1);
 assert.equal('artifact' in page.sources[0],false);
 const next=await domain.snapshotList({workspaceId:'workspace',offset:page.nextOffset,limit:1});
 assert.equal(next.sources[0].id,older.source.id);
 assert.equal(next.nextOffset,null);
 const searched=await domain.snapshotList({workspaceId:'workspace',query:'첫'});
 assert.deepEqual(searched.sources.map(source=>source.id),[older.source.id]);
 const read=await domain.snapshotRead({workspaceId:'workspace',sourceId:older.source.id});
 assert.deepEqual(read.source.artifact,first.work.artifacts[0]);
 assert.equal(read.source.id,older.source.id);
 await assert.rejects(domain.snapshotRead({workspaceId:'workspace',sourceId:'missing'}),{status:404});
 await assert.rejects(domain.snapshotList({workspaceId:'workspace',limit:101}),{status:422});
 await assert.rejects(domain.snapshotList({workspaceId:'wrong'}),{status:404});
}));
test('the Flow HTTP route reads saved work without a selected work ID',async()=>fixture(async service=>{
 const made=await service.domain.workCreate({workspaceId:'workspace',name:'원본 작업',artifact:contentArtifact('읽을 화면'),idempotencyKey:randomUUID()});
 const saved=await service.domain.snapshotCreate({workspaceId:'workspace',workId:made.work.id,artifactId:made.work.artifacts[0].id,expectedRevision:0,idempotencyKey:randomUUID()});
 const server=createServer((request,response)=>service.handler(request,response,()=>{response.writeHead(404);response.end()}));
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const base='http://127.0.0.1:'+server.address().port;
 try{
  const list=await fetch(base+'/api/flow/snapshots?workspaceId=workspace&limit=1');
  assert.equal(list.status,200);
  assert.equal((await list.json()).sources[0].id,saved.source.id);
  const read=await fetch(base+'/api/flow/snapshots/'+encodeURIComponent(saved.source.id)+'?workspaceId=workspace');
  assert.equal(read.status,200);
  const source=(await read.json()).source;assert.equal(source.artifactRef.id,made.work.artifacts[0].id);assert.equal(source.artifact,undefined);
  const chunk=await (await fetch(base+'/api/flow/artifact?'+new URLSearchParams({workspaceId:'workspace',sourceId:saved.source.id}))).json();
  assert.deepEqual(JSON.parse(chunk.content),made.work.artifacts[0]);
  const missing=await fetch(base+'/api/flow/snapshots/missing?workspaceId=workspace');
  assert.equal(missing.status,404);
 }finally{await new Promise(resolve=>server.close(resolve))}
}));
test('a saved Flow artifact starts a new work with its source linked in the same change',async()=>fixture(async({domain,read})=>{
 const original=contentArtifact('검사 화면');
 original.composition.blocks[0].content.paragraphs=['보관할 내용'];
 const sourceWork=await domain.workCreate({workspaceId:'workspace',name:'원본 작업',artifact:original,idempotencyKey:randomUUID()});
 const saved=await domain.snapshotCreate({workspaceId:'workspace',workId:sourceWork.work.id,
  artifactId:sourceWork.work.artifacts[0].id,expectedRevision:0,idempotencyKey:randomUUID()});
 const input={workspaceId:'workspace',name:'이어서 검토',sourceId:saved.source.id,idempotencyKey:randomUUID()};
 const first=await domain.workCreate(input),retry=await domain.workCreate(input);
 assert.equal(first.work.id,retry.work.id);
 assert.deepEqual(first.work.sourceIds,[saved.source.id]);
 assert.equal(first.work.artifacts.length,1);
 const snapshot=(await read()).state.librarySnapshots.find(item=>item.id===saved.source.id);
 assert.notEqual(first.work.artifacts[0].id,snapshot.artifact.id);
 assert.deepEqual(first.work.artifacts[0],{...snapshot.artifact,id:first.work.artifacts[0].id,revision:0});
 assert.deepEqual((await domain.workRead({workspaceId:'workspace',workId:first.work.id})).sources[0].artifact,snapshot.artifact);
 await assert.rejects(domain.workCreate({...input,idempotencyKey:randomUUID(),sourceId:'snapshot:missing'}),{status:422});
 await assert.rejects(domain.workCreate({...input,idempotencyKey:randomUUID(),artifact:original}),{status:422});
 await assert.rejects(domain.workCreate({...input,name:'다른 작업'}),{status:409});
}));
test('a Toolkit resource starts a new work with its resource block and original link',async()=>fixture(async({domain})=>{
 const reference={kind:'journal-item',id:'123e4567-e89b-42d3-a456-426614174000'};
 const block={id:randomUUID(),kind:'resource',content:{reference,title:'현장 기록',detail:'Journal · 진행 중'}};
 const artifact={id:randomUUID(),kind:'content',revision:0,title:'현장 기록',composition:stackComposition([block])};
 const input={workspaceId:'workspace',name:'현장 기록 검토',artifact,linkedResources:[reference],idempotencyKey:randomUUID()};
 const first=await domain.workCreate(input),retry=await domain.workCreate(input);
 assert.equal(first.work.id,retry.work.id);
 assert.deepEqual(first.work.linkedResources,[reference]);
 assert.deepEqual(first.work.artifacts[0].composition.blocks[0].content.reference,reference);
 const read=await domain.workRead({workspaceId:'workspace',workId:first.work.id});
 assert.deepEqual(read.work.linkedResources,[reference]);
 await assert.rejects(domain.workCreate({...input,idempotencyKey:randomUUID(),linkedResources:[{...reference,id:'bad'}]}),{status:422});
 await assert.rejects(domain.workCreate({...input,linkedResources:[]}),{status:409});
}));
test('a linked Flow Library artifact can be added to another work without changing its snapshot',async()=>fixture(async({domain})=>{
 const original=contentArtifact('재사용할 작업 화면');
 original.composition.blocks[0].content.paragraphs=['보관한 내용'];
 const sourceWork=await domain.workCreate({workspaceId:'workspace',name:'원본 작업',artifact:original,idempotencyKey:randomUUID()});
 const saved=await domain.snapshotCreate({workspaceId:'workspace',workId:sourceWork.work.id,
  artifactId:sourceWork.work.artifacts[0].id,expectedRevision:0,idempotencyKey:randomUUID()});
 const target=await domain.workCreate({workspaceId:'workspace',name:'새 작업',artifact:blankArtifact('새 작업'),idempotencyKey:randomUUID()});
 await domain.workUpdate({workspaceId:'workspace',workId:target.work.id,expectedRevision:0,
  sourceIds:[saved.source.id],idempotencyKey:randomUUID()});
 const linked=(await domain.workRead({workspaceId:'workspace',workId:target.work.id})).sources[0];
 const key=randomUUID(),input={workspaceId:'workspace',workId:target.work.id,mode:'add',
  artifact:linked.artifact,idempotencyKey:key};
 const first=await domain.changeSubmit(input),retry=await domain.changeSubmit(input);
 assert.equal(first.change.artifactId,retry.change.artifactId);
 const after=await domain.workRead({workspaceId:'workspace',workId:target.work.id});
 assert.equal(after.work.artifacts.length,2);
 const added=after.work.artifacts[1];
 assert.notEqual(added.id,linked.artifact.id);
 assert.deepEqual(added,{...linked.artifact,id:added.id,revision:0});
 assert.deepEqual(after.sources[0].artifact,linked.artifact);
 assert.deepEqual(after.work.sourceIds,[saved.source.id]);
}));
test('a saved work artifact keeps its version and an idempotent Flow Library identity',async()=>fixture(async({domain,read})=>{
 const before=await read(),work=before.state.works[0],artifact=work.artifacts[0];
 const input={workspaceId:'workspace',workId:work.id,artifactId:artifact.id,expectedRevision:artifact.revision,idempotencyKey:randomUUID()};
 const saved=await domain.snapshotCreate(input),retry=await domain.snapshotCreate(input);
 assert.deepEqual(retry,saved);
 assert.equal(saved.source.artifactId,artifact.id);
 assert.equal(saved.source.artifactRevision,artifact.revision);
 assert.equal(saved.source.kind,'결과물');
 assert.equal('artifact' in saved.source,false);
 assert.equal('body' in saved.source,false);
 const after=await read();
 assert.equal(after.state.librarySnapshots.length,before.state.librarySnapshots.length+1);
 const snapshot=after.state.librarySnapshots.at(-1);
 assert.equal(snapshot.id,saved.source.id);
 assert.deepEqual(snapshot.artifact,artifact);
 const readWork=await domain.workRead({workspaceId:'workspace',workId:work.id});
 assert.deepEqual(readWork.sourceCatalog.find(item=>item.id===snapshot.id),saved.source);
 await assert.rejects(domain.snapshotCreate({...input,idempotencyKey:randomUUID(),expectedRevision:artifact.revision+1}),{status:409});
 await assert.rejects(domain.snapshotCreate({...input,artifactId:'missing'}),{status:409});
 const blank=await domain.workCreate({workspaceId:'workspace',name:'빈 작업',artifact:blankArtifact('빈 작업'),idempotencyKey:randomUUID()});
 await assert.rejects(domain.snapshotCreate({workspaceId:'workspace',workId:blank.work.id,artifactId:blank.work.artifacts[0].id,expectedRevision:0,idempotencyKey:randomUUID()}),{status:422});
}));
test('the Flow HTTP route saves one work artifact snapshot',async()=>fixture(async service=>{
 const current=await service.read(),work=current.state.works[0],artifact=work.artifacts[0];
 const server=createServer((request,response)=>service.handler(request,response,()=>{response.writeHead(404);response.end()}));
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const base='http://127.0.0.1:'+server.address().port;
 try{
  const input={workspaceId:'workspace',workId:work.id,artifactId:artifact.id,expectedRevision:artifact.revision,idempotencyKey:randomUUID()};
  const post=body=>fetch(base+'/api/flow/snapshots',{method:'POST',headers:{'X-Toolkit-Flow':'1','Content-Type':'application/json'},body:JSON.stringify(body)});
  const first=await post(input);
  assert.equal(first.status,201);
  const saved=await first.json();
  const second=await post(input);
  assert.equal(second.status,201);
  assert.deepEqual(await second.json(),saved);
  assert.equal((await service.read()).state.librarySnapshots.at(-1).id,saved.source.id);
 }finally{await new Promise(resolve=>server.close(resolve))}
}));
test('a blank work accepts its first content without changing the artifact identity',async()=>fixture(async({domain})=>{
 const made=await domain.workCreate({workspaceId:'workspace',name:'검사 작업',artifact:{...blankArtifact('검사 작업'),draftText:'남겨 둔 초안'},idempotencyKey:randomUUID()});
 const before=made.work.artifacts[0];
 assert.equal(before.kind,'blank');
 const content=contentArtifact(before.title);
 content.composition.blocks[0].content.paragraphs=['첫 관찰'];
 const input={workspaceId:'workspace',workId:made.work.id,mode:'initialize',artifactId:before.id,baseRevision:0,artifact:content,idempotencyKey:randomUUID()};
 await assert.rejects(domain.changeSubmit({...input,idempotencyKey:randomUUID(),artifact:{
  id:randomUUID(),kind:'image',revision:0,title:'외부 이미지',src:'/api/flow/assets/'+'a'.repeat(64)+'.png',width:10,height:10,crop:null,alt:'',
 }}),{status:422});
 const first=await domain.changeSubmit(input),retry=await domain.changeSubmit(input);
 assert.equal(first.change.status,'completed');
 assert.equal(retry.change.id,first.change.id);
 const after=(await domain.workRead({workspaceId:'workspace',workId:made.work.id})).work.artifacts[0];
 assert.equal(after.id,before.id);assert.equal(after.kind,'content');assert.equal(after.revision,1);
 assert.equal(after.composition.blocks[0].content.paragraphs[0],'첫 관찰');
 await assert.rejects(domain.changeSubmit({...input,idempotencyKey:randomUUID(),baseRevision:0}),{status:409});
 await assert.rejects(domain.changeSubmit({...input,idempotencyKey:randomUUID(),baseRevision:1}),{status:422});
 await domain.changeAction({workspaceId:'workspace',changeId:first.change.id,action:'undo'});
 const restored=(await domain.workRead({workspaceId:'workspace',workId:made.work.id})).work.artifacts[0];
 assert.equal(restored.id,before.id);assert.equal(restored.kind,'blank');assert.equal(restored.revision,2);
 assert.equal(restored.composition,undefined);assert.equal(restored.draftText,'남겨 둔 초안');
 await assert.rejects(domain.changeSubmit({...input,idempotencyKey:randomUUID(),baseRevision:2,artifact:blankArtifact('다시 비움')}),{status:422});
}));
test('the Flow HTTP route initializes a blank artifact once',async()=>fixture(async service=>{
 const made=await service.domain.workCreate({workspaceId:'workspace',name:'새 작업',artifact:blankArtifact('새 작업'),idempotencyKey:randomUUID()});
 const first=made.work.artifacts[0],content=contentArtifact(first.title);
 content.composition.blocks[0].content.paragraphs=['현장 기록'];
 const server=createServer((request,response)=>service.handler(request,response,()=>{response.writeHead(404);response.end()}));
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const base='http://127.0.0.1:'+server.address().port;
 try{
  const payload={workspaceId:'workspace',workId:made.work.id,mode:'initialize',artifactId:first.id,baseRevision:first.revision,artifact:content,idempotencyKey:randomUUID()};
  const post=body=>fetch(base+'/api/flow/changes',{method:'POST',headers:{'X-Toolkit-Flow':'1','Content-Type':'application/json'},body:JSON.stringify(body)});
  const response=await post(payload);
  assert.equal(response.status,200);assert.equal((await response.json()).change.status,'completed');
  const saved=(await service.domain.workRead({workspaceId:'workspace',workId:made.work.id})).work.artifacts[0];
  assert.equal(saved.id,first.id);assert.equal(saved.kind,'content');
  const rejected=await post({...payload,idempotencyKey:randomUUID(),baseRevision:saved.revision});
  assert.equal(rejected.status,422);
 }finally{await new Promise(resolve=>server.close(resolve))}
}));
test('document and diagram proposals become content screens and can be undone without stale fields',async()=>fixture(async({domain})=>{
 for(const original of [documentArtifact('검사 기록'),diagramArtifact('검사 순서')]){
  const made=await domain.workCreate({workspaceId:'workspace',name:original.title,artifact:original,idempotencyKey:randomUUID()});
  const before=made.work.artifacts[0],proposal=contentFromArtifact(before,()=>randomUUID());
  const submitted=await domain.changeSubmit({workspaceId:'workspace',workId:made.work.id,mode:'proposal',
   artifactId:before.id,baseRevision:before.revision,artifact:proposal,idempotencyKey:randomUUID()});
  assert.equal(submitted.change.status,'review');
  const applied=await domain.changeAction({workspaceId:'workspace',changeId:submitted.change.id,action:'apply'});
  assert.equal(applied.change.status,'completed');
  const converted=(await domain.workRead({workspaceId:'workspace',workId:made.work.id})).work.artifacts[0];
  assert.equal(converted.id,before.id);assert.equal(converted.revision,before.revision+1);
  assert.equal(converted.kind,'content');assert.equal(converted.composition.blocks[0].kind,before.kind==='document'?'text':'diagram');
  assert.equal(converted.blocks,undefined);assert.equal(converted.nodes,undefined);assert.equal(converted.edges,undefined);
  await domain.changeAction({workspaceId:'workspace',changeId:submitted.change.id,action:'undo'});
  const restored=(await domain.workRead({workspaceId:'workspace',workId:made.work.id})).work.artifacts[0];
  assert.equal(restored.kind,before.kind);assert.equal(restored.id,before.id);
  assert.equal(restored.composition,undefined);
  if(before.kind==='document')assert.deepEqual(restored.blocks,before.blocks);
  else assert.deepEqual(restored.nodes,before.nodes);
 }
}));
test('presentation artifacts do not silently become a different screen',async()=>fixture(async({domain})=>{
 const made=await domain.workCreate({workspaceId:'workspace',name:'발표 자료',artifact:{...documentArtifact('발표 자료'),format:'slides'},idempotencyKey:randomUUID()});
 const before=made.work.artifacts[0],asContent={id:before.id,kind:'content',title:before.title,revision:0,composition:stackComposition([{id:'text',kind:'text',content:{paragraphs:['본문']}}])};
 const listed=(await domain.workList({workspaceId:'workspace'})).works.find(item=>item.id===made.work.id);
 assert.equal(listed.artifact.format,'slides');
 await assert.rejects(domain.changeSubmit({workspaceId:'workspace',workId:made.work.id,mode:'proposal',artifactId:before.id,baseRevision:0,artifact:asContent,idempotencyKey:randomUUID()}),{status:422});
 await assert.rejects(domain.changeSubmit({workspaceId:'workspace',workId:made.work.id,mode:'proposal',artifactId:before.id,baseRevision:0,artifact:diagramArtifact('다른 도식'),idempotencyKey:randomUUID()}),{status:422});
}));

test('image conversion retains the approved original asset and can be undone',async()=>fixture(async({domain},directory)=>{
 const bytes=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2AXcAAAAASUVORK5CYII=','base64');
 const file=createHash('sha256').update(bytes).digest('hex')+'.png';
 await mkdir(join(directory,'assets'));await writeFile(join(directory,'assets',file),bytes);
 const source={id:randomUUID(),kind:'image',revision:0,title:'부품 사진',src:'/api/flow/assets/'+file,
  alt:'부품 상단',width:1200,height:800,crop:{x:.2,y:.1,width:.5,height:.6}};
 const made=await domain.workCreate({workspaceId:'workspace',name:'이미지 검토',artifact:source,idempotencyKey:randomUUID()});
 const before=made.work.artifacts[0];
 const proposal=contentFromArtifact(before,()=>randomUUID());
 const lost={...proposal,composition:stackComposition([{id:randomUUID(),kind:'text',content:{paragraphs:['원본 누락']}}])};
 await assert.rejects(domain.changeSubmit({workspaceId:'workspace',workId:made.work.id,mode:'proposal',
  artifactId:before.id,baseRevision:0,artifact:lost,idempotencyKey:randomUUID()}),{status:422});
 const submitted=await domain.changeSubmit({workspaceId:'workspace',workId:made.work.id,mode:'proposal',
  artifactId:before.id,baseRevision:0,artifact:proposal,idempotencyKey:randomUUID()});
 assert.equal(submitted.change.status,'review');
 await domain.changeAction({workspaceId:'workspace',changeId:submitted.change.id,action:'apply'});
 const saved=(await domain.workRead({workspaceId:'workspace',workId:made.work.id})).work.artifacts[0];
 assert.equal(saved.id,before.id);assert.equal(saved.kind,'content');
 assert.equal(saved.src,undefined);assert.equal(saved.width,undefined);
 assert.equal(saved.composition.blocks[0].content.src,before.src);
 assert.deepEqual(saved.composition.blocks[0].content.crop,before.crop);
 await domain.changeAction({workspaceId:'workspace',changeId:submitted.change.id,action:'undo'});
 const restored=(await domain.workRead({workspaceId:'workspace',workId:made.work.id})).work.artifacts[0];
 assert.equal(restored.kind,'image');assert.equal(restored.composition,undefined);
 assert.equal(restored.src,before.src);assert.deepEqual(restored.crop,before.crop);
}));

test('a content artifact starts with an editable text block and preserves its identity',()=>{
 const artifact=contentArtifact('검사 작업 화면');
 assert.equal(validArtifact(artifact),true);
 assert.equal(artifact.composition.blocks[0].kind,'text');
 const state=initialState(),work=state.works[0];
 const next=reviseArtifact(state,work.id,work.activeArtifactId,{kind:'content',composition:artifact.composition});
 assert.equal(next.works[0].artifacts[0].id,work.activeArtifactId);
 assert.equal(next.works[0].artifacts[0].kind,'content');
});
test('linked image, PDF and audio files join existing content without replacing its blocks',()=>{
 const original=contentArtifact('검사 화면');
 original.composition.blocks[0].content.paragraphs=['기존 메모'];
 for(const file of [
  {name:'photo.png',path:'images/photo.png'},
  {name:'report.pdf',path:'reports/report.pdf'},
  {name:'recording.wav',path:'audio/recording.wav'},
 ]){
  const media=workspaceFileArtifact(file,'workspace');
  const composition=appendContentBlock(original.composition,media.composition.blocks[0]);
  assert.equal(validArtifact({...original,composition}),true);
  assert.deepEqual(composition.blocks[0],original.composition.blocks[0]);
  assert.deepEqual(composition.rows[0],original.composition.rows[0]);
  assert.equal(composition.blocks[1].kind,'file');
 }
});
test('a diagram stays with the other content when its nodes are revised',async()=>fixture(async({domain})=>{
 const base=contentArtifact('검사 화면');
 base.composition.blocks[0].content.paragraphs=['기존 관찰'];
 const diagram=newContentBlock('diagram',randomUUID(),randomUUID);
 diagram.content.nodes[0].label='원본 확인';
 diagram.content.nodes.push({id:randomUUID(),label:'조건 비교',detail:'조명과 각도',x:750,y:300});
 diagram.content.edges.push({id:randomUUID(),from:diagram.content.nodes[0].id,to:diagram.content.nodes[1].id,label:''});
 base.composition=appendContentBlock(base.composition,diagram);
 const made=await domain.workCreate({workspaceId:'workspace',name:'검사 작업',artifact:base,idempotencyKey:randomUUID()});
 const current=made.work.artifacts[0];
 assert.equal(current.composition.blocks[1].kind,'diagram');
 const revised={...current,composition:{...current.composition,blocks:current.composition.blocks.map(block=>block.id===diagram.id?{...block,content:{...block.content,nodes:block.content.nodes.map((node,index)=>index===1?{...node,label:'촬영 조건 비교'}:node)}}:block)}};
 const submitted=await domain.changeSubmit({workspaceId:'workspace',workId:made.work.id,mode:'proposal',artifactId:current.id,baseRevision:current.revision,artifact:revised,idempotencyKey:randomUUID()});
 assert.equal(submitted.change.status,'review');
 await domain.changeAction({workspaceId:'workspace',changeId:submitted.change.id,action:'apply'});
 const saved=(await domain.workRead({workspaceId:'workspace',workId:made.work.id})).work.artifacts[0];
 assert.deepEqual(saved.composition.rows,current.composition.rows);
 assert.deepEqual(saved.composition.blocks[0],current.composition.blocks[0]);
 assert.equal(saved.composition.blocks[1].content.nodes[1].label,'촬영 조건 비교');
 assert.equal(saved.composition.blocks[1].content.edges[0].to,diagram.content.nodes[1].id);
}));
test('v2 reads remain unchanged while the first write uses SQLite',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'flow-legacy-')),service=createWorkspaceService(directory);
 try{
  const before=initialState(),legacy={...before,version:2,works:before.works.map(({revision,...rest})=>rest)};
  await writeFile(join(directory,'workspace.json'),JSON.stringify(legacy));
  const data=await service.read();assert.equal(data.state.version,4);assert.equal(data.state.works[0].id,legacy.works[0].id);
  assert.equal(service.store.isMigrated(),false);await service.put(data.state,data.revision);
  assert.equal(service.store.isMigrated(),true);
  assert.equal(JSON.parse(await readFile(join(directory,'workspace.json'),'utf8')).version,2);
 }finally{service.close();await rm(directory,{recursive:true,force:true})}
});
test('work create, read, scoped context update and idempotent retry',async()=>fixture(async({domain})=>{
 const first=documentArtifact('첫 문서');const key=randomUUID();
 const made=await domain.workCreate({workspaceId:'workspace',name:'새 작업',artifact:first,idempotencyKey:key});
 const repeated=await domain.workCreate({workspaceId:'workspace',name:'새 작업',artifact:first,idempotencyKey:key});
 await assert.rejects(domain.workCreate({workspaceId:'workspace',name:'다른 작업',artifact:first,idempotencyKey:key}),{status:409});
 assert.equal(made.work.id,repeated.work.id);assert.match(made.link,/work=/);
 const found=await domain.workList({workspaceId:'workspace',query:'새 작업'});assert.equal(found.works.length,1);
 const read=await domain.workRead({workspaceId:'workspace',workId:made.work.id});assert.equal(read.work.revision,0);
 const changed=await domain.workUpdate({workspaceId:'workspace',workId:made.work.id,expectedRevision:0,idempotencyKey:randomUUID(),purpose:'사용자가 지정한 목적'});
 assert.equal(changed.work.revision,1);assert.equal(changed.work.purpose,'사용자가 지정한 목적');
 await assert.rejects(domain.workUpdate({workspaceId:'workspace',workId:made.work.id,expectedRevision:0,idempotencyKey:randomUUID(),name:'낡은 수정'}),{status:409});
}));
test('a work keeps artifact order and widths without changing artifact content',async()=>fixture(async({domain})=>{
 const original=(await domain.workList({workspaceId:'workspace'})).works[0];
 const before=(await domain.workRead({workspaceId:'workspace',workId:original.id})).work;
 const second=documentArtifact('둘째 문서');
 await domain.changeSubmit({workspaceId:'workspace',workId:before.id,mode:'add',artifact:second,idempotencyKey:randomUUID()});
 const added=(await domain.workRead({workspaceId:'workspace',workId:before.id})).work;
 const [first,next]=added.artifacts;
 const layout={order:[next.id,first.id],spans:{[next.id]:8,[first.id]:4}};
 const saved=await domain.workUpdate({workspaceId:'workspace',workId:added.id,expectedRevision:added.revision,idempotencyKey:randomUUID(),surfaceLayout:layout});
 assert.equal(saved.work.revision,added.revision+1);
 assert.deepEqual(saved.work.surfaceLayout,layout);
 assert.deepEqual(saved.work.artifacts,added.artifacts);
 assert.deepEqual((await domain.workRead({workspaceId:'workspace',workId:added.id})).work.surfaceLayout,layout);
 await assert.rejects(domain.workUpdate({workspaceId:'workspace',workId:added.id,expectedRevision:added.revision,idempotencyKey:randomUUID(),surfaceLayout:layout}),{status:409});
 for(const bad of [
  {order:[first.id],spans:{[first.id]:5}},
  {order:[first.id,first.id],spans:{}},
  {order:['missing'],spans:{}},
  {order:[first.id],spans:{missing:6}},
  {...layout,color:'red'},
 ]) await assert.rejects(domain.workUpdate({workspaceId:'workspace',workId:added.id,expectedRevision:saved.work.revision,idempotencyKey:randomUUID(),surfaceLayout:bad}),{status:422});
}));
test('a work links Toolkit records by identity without copying their content',async()=>fixture(async({domain,read})=>{
 const readRef='read1.'+Buffer.from(JSON.stringify({version:1,spaceId:'research',connectionId:'main',corpusId:'source-corpus',unitId:'unit-1'})).toString('base64url');
 const work=(await domain.workList({workspaceId:'workspace'})).works[0];
 const linkedResources=[
  {kind:'journal-item',id:'123e4567-e89b-42d3-a456-426614174000'},
  {kind:'library-issue',id:'research:2026-09-24:09'},
  {kind:'design-recipe',id:'document-minimal'},
  {kind:'host-file',root:'research-note/main',path:'notes/한글.md'},
  {kind:'context',locator:{product:'sense',sectionId:'conversation-and-writing'}},
  {kind:'context',locator:{product:'corpus',spaceId:'research',documentId:'methods:notes'}},
  {kind:'context',locator:{product:'context-item',spaceId:'research',itemId:'item_1'}},
  {kind:'context',locator:{product:'context-skill',spaceId:'research'}},
  {kind:'context',locator:{product:'source',spaceId:'research',readRef}},
 ];
 const updated=await domain.workUpdate({workspaceId:'workspace',workId:work.id,expectedRevision:work.revision,idempotencyKey:randomUUID(),linkedResources});
 assert.deepEqual(updated.work.linkedResources,linkedResources);
 const current=(await domain.workRead({workspaceId:'workspace',workId:work.id})).work;
 assert.deepEqual(current.linkedResources,linkedResources);
 assert.equal((await read()).state.works.find(item=>item.id===work.id).linkedResources[0].title,undefined);
 await assert.rejects(domain.workUpdate({workspaceId:'workspace',workId:work.id,expectedRevision:work.revision,idempotencyKey:randomUUID(),linkedResources:[]}),{status:409});
 for(const bad of [
  [{kind:'journal-item',id:'not-an-id'}],
  [{kind:'library-issue',id:'daily:2026-09-24',title:'copied title'}],
  [{kind:'design-recipe',id:'../other'}],
  [{kind:'design-recipe',id:'document-minimal',title:'copied title'}],
  [{kind:'host-file',root:'workspace',path:'../secret'}],
  [{kind:'host-file',root:'workspace',path:'notes//file.md'}],
  [{kind:'host-file',root:'workspace',path:'notes/file.md',body:'copied content'}],
  [linkedResources[0],linkedResources[0]],
  [{kind:'unknown',id:'x'}],
  [{kind:'context',locator:{product:'corpus',spaceId:'research',documentId:'../secret'}}],
  [{kind:'context',locator:{product:'sense',sectionId:'conversation-and-writing',skill:false}}],
  [{kind:'context',locator:{product:'corpus',spaceId:'research',documentId:'notes'},title:'copied title'}],
  [linkedResources[2],linkedResources[2]],
  [linkedResources[3],linkedResources[3]],
  [linkedResources[5],{kind:'context',locator:{documentId:'methods:notes',product:'corpus',spaceId:'research'}}],
  [{kind:'context',locator:{product:'source',spaceId:'research',readRef:'opaque-ref'}}],
  [{kind:'context',locator:{product:'source',spaceId:'other',readRef}}],
  [{kind:'context',locator:{product:'source',spaceId:'research',readRef},title:'copied title'}],
  [linkedResources[8],linkedResources[8]],
  [{kind:'context',locator:{product:'corpus',spaceId:'research',documentId:123}}],
  [{kind:'context',locator:{product:'corpus',spaceId:'research',documentId:'x'.repeat(201)}}],
 ]) await assert.rejects(domain.workUpdate({workspaceId:'workspace',workId:work.id,expectedRevision:current.revision,idempotencyKey:randomUUID(),linkedResources:bad}),{status:422});
}));
test('a Toolkit resource stays in the saved work surface and rejects unsafe identities',async()=>fixture(async({domain})=>{
 const work=(await domain.workList({workspaceId:'workspace'})).works[0];
 const reference={kind:'host-file',root:'workspace',path:'reports/검사.pdf'};
 const block={id:randomUUID(),kind:'resource',content:{reference,title:'검사 결과',detail:'Spark · 파일'}};
 const artifact={id:randomUUID(),kind:'content',title:'검사 자료',revision:0,composition:stackComposition([block])};
 const result=await domain.changeSubmit({workspaceId:'workspace',workId:work.id,mode:'add',artifact,idempotencyKey:randomUUID()});
 assert.equal(result.change.status,'completed');
 const saved=(await domain.workRead({workspaceId:'workspace',workId:work.id})).work.artifacts.at(-1);
 assert.deepEqual(saved.composition.blocks[0].content.reference,reference);
 assert.equal(saved.composition.blocks[0].content.body,undefined);
 await assert.rejects(domain.changeSubmit({
  workspaceId:'workspace',workId:work.id,mode:'add',idempotencyKey:randomUUID(),
  artifact:{...artifact,id:randomUUID(),composition:stackComposition([{...block,content:{...block.content,reference:{...reference,path:'../secret'}}}])},
 }),{status:422});
}));
test('adding a linked resource to existing content keeps the original blocks until review',async()=>fixture(async({domain})=>{
 const original=contentArtifact('검사 화면');
 original.composition.blocks[0].content.paragraphs=['기존 관찰 내용'];
 const made=await domain.workCreate({workspaceId:'workspace',name:'검사 작업',artifact:original,idempotencyKey:randomUUID()});
 const current=made.work.artifacts[0];
 const resource={id:randomUUID(),kind:'resource',content:{
  reference:{kind:'library-issue',id:'research:2026-09-24:09'},title:'연구 발간물',detail:'Research · 2026-09-24',
 }};
 const proposal={...current,composition:appendContentBlock(current.composition,resource)};
 const submitted=await domain.changeSubmit({workspaceId:'workspace',workId:made.work.id,mode:'proposal',
  artifactId:current.id,baseRevision:0,artifact:proposal,idempotencyKey:randomUUID()});
 assert.equal(submitted.change.status,'review');
 assert.equal((await domain.workRead({workspaceId:'workspace',workId:made.work.id})).work.artifacts[0].composition.blocks.length,1);
 await domain.changeAction({workspaceId:'workspace',changeId:submitted.change.id,action:'apply'});
 const saved=(await domain.workRead({workspaceId:'workspace',workId:made.work.id})).work.artifacts[0].composition;
 assert.equal(saved.blocks.length,2);
 assert.equal(saved.blocks[0].content.paragraphs[0],'기존 관찰 내용');
 assert.deepEqual(saved.blocks[1].content.reference,resource.content.reference);
}));
test('the Flow HTTP contract accepts only exact linked product identifiers',async()=>fixture(async(service)=>{
 const server=createServer((request,response)=>service.handler(request,response,()=>{response.writeHead(404);response.end()}));
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const url='http://127.0.0.1:'+server.address().port;
 try {
  const work=(await service.domain.workList({workspaceId:'workspace'})).works[0];
  const body={workspaceId:'workspace',expectedRevision:work.revision,idempotencyKey:randomUUID(),linkedResources:[{kind:'library-issue',id:'daily:2026-09-24'},{kind:'design-recipe',id:'document-minimal'},{kind:'host-file',root:'workspace',path:'notes/한글.md'},{kind:'context',locator:{product:'corpus',spaceId:'research',documentId:'notes'}}]};
  const response=await fetch(url+'/api/flow/works/'+encodeURIComponent(work.id),{method:'PATCH',headers:{'X-Toolkit-Flow':'1','Content-Type':'application/json'},body:JSON.stringify(body)});
  assert.equal(response.status,200);
  assert.deepEqual((await response.json()).work.linkedResources,body.linkedResources);
  const layout={order:[work.artifact.id],spans:{[work.artifact.id]:8}};
  const arranged=await fetch(url+'/api/flow/works/'+encodeURIComponent(work.id),{method:'PATCH',headers:{'X-Toolkit-Flow':'1','Content-Type':'application/json'},body:JSON.stringify({workspaceId:'workspace',expectedRevision:1,idempotencyKey:randomUUID(),surfaceLayout:layout})});
  assert.equal(arranged.status,200);
  assert.deepEqual((await arranged.json()).work.surfaceLayout,layout);
  const rejected=await fetch(url+'/api/flow/works/'+encodeURIComponent(work.id),{method:'PATCH',headers:{'X-Toolkit-Flow':'1','Content-Type':'application/json'},body:JSON.stringify({...body,idempotencyKey:randomUUID(),expectedRevision:2,linkedResources:[{kind:'library-issue',id:'https://other.example'}]})});
  assert.equal(rejected.status,422);
 }finally{await new Promise(resolve=>server.close(resolve));}
}));
test('selection edits only one document block, undo is guarded, proposals wait for review',async()=>fixture(async({domain})=>{
 const w=(await domain.workList({workspaceId:'workspace'})).works[0];
 const full=(await domain.workRead({workspaceId:'workspace',workId:w.id})).work;
 const a=full.artifacts[0],block=a.blocks[0],text=block.text.slice(0,8);
 const edit=await domain.changeSubmit({workspaceId:'workspace',workId:w.id,artifactId:a.id,baseRevision:a.revision,mode:'selection',idempotencyKey:randomUUID(),selection:{artifactId:a.id,blockId:block.id,text},replacement:'바뀐 부분'});
 assert.equal(edit.change.status,'completed');
 const newWork=(await domain.workRead({workspaceId:'workspace',workId:w.id})).work;
 assert.ok(newWork.artifacts[0].blocks[0].text.includes('바뀐 부분'));
 assert.deepEqual(newWork.artifacts[0].blocks.slice(1),a.blocks.slice(1));
 const undone=await domain.changeAction({workspaceId:'workspace',changeId:edit.change.id,action:'undo'});assert.equal(undone.change.status,'undone');
 const proposal={...a,title:'비교할 제목'};
 const review=await domain.changeSubmit({workspaceId:'workspace',workId:w.id,artifactId:a.id,baseRevision:2,mode:'proposal',idempotencyKey:randomUUID(),artifact:proposal});
 assert.equal(review.change.status,'review');
 const unchanged=(await domain.workRead({workspaceId:'workspace',workId:w.id})).work.artifacts[0];assert.equal(unchanged.title,a.title);
 const applied=await domain.changeAction({workspaceId:'workspace',changeId:review.change.id,action:'apply'});assert.equal(applied.change.status,'completed');
 const next=(await domain.workRead({workspaceId:'workspace',workId:w.id})).work.artifacts[0];assert.equal(next.title,'비교할 제목');
 assert.equal((await domain.changeAction({workspaceId:'workspace',changeId:edit.change.id,action:'undo'})).change.status,'undone');
 assert.equal((await domain.workRead({workspaceId:'workspace',workId:w.id})).work.artifacts[0].revision,next.revision);
}));
test('stale proposal remains comparable and duplicate result is not appended',async()=>fixture(async({domain})=>{
 const w=(await domain.workList({workspaceId:'workspace'})).works[0],a=(await domain.workRead({workspaceId:'workspace',workId:w.id})).work.artifacts[0];
 const key=randomUUID(),input={workspaceId:'workspace',workId:w.id,artifactId:a.id,baseRevision:0,mode:'proposal',idempotencyKey:key,artifact:{...a,title:'첫 안'}};
 const one=await domain.changeSubmit(input),two=await domain.changeSubmit(input);assert.equal(one.change.id,two.change.id);
 const edit=await domain.changeSubmit({...input,idempotencyKey:randomUUID(),artifact:{...a,title:'둘째 안'}});
 await domain.changeAction({workspaceId:'workspace',changeId:edit.change.id,action:'apply'});
 const stale=await domain.changeAction({workspaceId:'workspace',changeId:one.change.id,action:'apply'});
 assert.equal(stale.change.status,'conflict');assert.equal(stale.change.proposal.artifact.title,'첫 안');
}));
test('diagram and image artifacts can be submitted without external paths',async()=>fixture(async({domain},directory)=>{
 const bytes=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2AXcAAAAASUVORK5CYII=','base64'),file=createHash('sha256').update(bytes).digest('hex')+'.png';await mkdir(join(directory,'assets'));await writeFile(join(directory,'assets',file),bytes);
 const w=(await domain.workList({workspaceId:'workspace'})).works[0],diagram=diagramArtifact('검토 도식'),image={id:'temporary',kind:'image',revision:0,title:'이미지',src:'/api/flow/assets/'+file,width:40,height:30,crop:null,alt:''};
 const added=[];
 for(const artifact of [diagram,image]){
  const c=await domain.changeSubmit({workspaceId:'workspace',workId:w.id,mode:'add',idempotencyKey:randomUUID(),artifact});
  assert.equal(c.change.status,'completed');added.push(c.change);
 }
 const work=(await domain.workRead({workspaceId:'workspace',workId:w.id})).work;
 assert.equal(work.artifacts.length,3);
 const undone=await domain.changeAction({workspaceId:'workspace',changeId:added[1].id,action:'undo'});assert.equal(undone.change.status,'undone');
 assert.equal((await domain.workRead({workspaceId:'workspace',workId:w.id})).work.artifacts.length,2);
 await assert.rejects(domain.changeSubmit({workspaceId:'workspace',workId:w.id,mode:'add',idempotencyKey:randomUUID(),artifact:{...image,src:'file:///tmp/image.png'}}),{status:422});
}));
test('independent work edits merge, overlapping artifact edits conflict',()=>{
 const base=initialState(),desired=structuredClone(base),current=structuredClone(base),extra=documentArtifact('다른 작업물');
 desired.works[0].artifacts[0].title='브라우저 변경';desired.works[0].artifacts[0].revision++;
 current.works.push({id:'other',revision:0,name:'다른 작업',purpose:'',sourceIds:[],activeArtifactId:extra.id,artifacts:[extra]});
 const merged=mergeStates(base,desired,current);assert.equal(merged.works.length,2);assert.equal(merged.works[0].artifacts[0].title,'브라우저 변경');
 current.works[0].artifacts[0].title='에이전트 변경';current.works[0].artifacts[0].revision++;
 assert.throws(()=>mergeStates(base,desired,current),{status:409});
});

test('concurrent browser edits preserve linked product records and reject competing link changes',()=>{
 const base=initialState(),reference={kind:'journal-item',id:'123e4567-e89b-42d3-a456-426614174000'};
 const desired=structuredClone(base),current=structuredClone(base);
 desired.works[0].artifacts[0].title='수정한 제목';desired.works[0].artifacts[0].revision++;
 current.works[0].linkedResources=[reference];current.works[0].revision++;
 const merged=mergeStates(base,desired,current);
 assert.deepEqual(merged.works[0].linkedResources,[reference]);
 const competing=structuredClone(base);competing.works[0].linkedResources=[{kind:'library-issue',id:'daily:2026-09-24'}];
 assert.throws(()=>mergeStates(base,competing,current),{status:409});
});
test('stale scoped edit is retained as a conflict instead of overwriting newer text',async()=>fixture(async({domain})=>{
 const w=(await domain.workList({workspaceId:'workspace'})).works[0],a=(await domain.workRead({workspaceId:'workspace',workId:w.id})).work.artifacts[0];
 const original=a.blocks[0].text.slice(0,10),blockId=a.blocks[0].id;
 const first=await domain.changeSubmit({workspaceId:'workspace',workId:w.id,artifactId:a.id,baseRevision:0,mode:'selection',idempotencyKey:randomUUID(),selection:{artifactId:a.id,blockId,text:original},replacement:'첫 수정'});
 assert.equal(first.change.status,'completed');
 const stale=await domain.changeSubmit({workspaceId:'workspace',workId:w.id,artifactId:a.id,baseRevision:0,mode:'selection',idempotencyKey:randomUUID(),selection:{artifactId:a.id,blockId,text:original},replacement:'낡은 수정'});
 assert.equal(stale.change.status,'conflict');assert.equal(stale.change.proposal.replacement,'낡은 수정');
 const now=(await domain.workRead({workspaceId:'workspace',workId:w.id})).work.artifacts[0];
 assert.ok(now.blocks[0].text.includes('첫 수정'));assert.ok(!now.blocks[0].text.includes('낡은 수정'));
}));

test('image region and diagram object edits stay within their selected targets',async()=>fixture(async({domain},directory)=>{
 const w=(await domain.workList({workspaceId:'workspace'})).works[0],diagram=diagramArtifact('도식');
 const bytes=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2AXcAAAAASUVORK5CYII=','base64'),file=createHash('sha256').update(bytes).digest('hex')+'.png';
 await mkdir(join(directory,'assets'));await writeFile(join(directory,'assets',file),bytes);
 const image={id:'temporary',kind:'image',revision:0,title:'이미지',src:'/api/flow/assets/'+file,width:40,height:30,crop:null,alt:''};
 for(const artifact of [diagram,image])await domain.changeSubmit({workspaceId:'workspace',workId:w.id,mode:'add',idempotencyKey:randomUUID(),artifact});
 const before=(await domain.workRead({workspaceId:'workspace',workId:w.id})).work;
 const d=before.artifacts.find(a=>a.kind==='diagram'),i=before.artifacts.find(a=>a.kind==='image'),node=d.nodes[0];
 const changedNode=await domain.changeSubmit({workspaceId:'workspace',workId:w.id,artifactId:d.id,baseRevision:0,mode:'selection',idempotencyKey:randomUUID(),selection:{artifactId:d.id,objectKind:'node',objectId:node.id},changes:{label:'선택한 노드'}});
 const changedImage=await domain.changeSubmit({workspaceId:'workspace',workId:w.id,artifactId:i.id,baseRevision:0,mode:'selection',idempotencyKey:randomUUID(),selection:{artifactId:i.id,rect:{x:0.1,y:0.1,width:0.2,height:0.2}},changes:{alt:'선택한 영역'}});
 assert.equal(changedNode.change.status,'completed');assert.equal(changedImage.change.status,'completed');
 const after=(await domain.workRead({workspaceId:'workspace',workId:w.id})).work;
 const newDiagram=after.artifacts.find(a=>a.id===d.id),newImage=after.artifacts.find(a=>a.id===i.id);
 assert.equal(newDiagram.nodes[0].label,'선택한 노드');assert.deepEqual(newDiagram.nodes.slice(1),d.nodes.slice(1));
 assert.equal(newImage.alt,'선택한 영역');assert.equal(newImage.src,i.src);assert.equal(newImage.width,i.width);
}));


test('content artifacts keep their blocks while an agent proposes a new layout',async()=>fixture(async({domain})=>{
 const work=(await domain.workList({workspaceId:'workspace'})).works[0];
 const original={id:'temporary',kind:'content',revision:0,title:'검사 자료',composition:structuredClone(catalogSections[0].composition)};
 const added=await domain.changeSubmit({workspaceId:'workspace',workId:work.id,mode:'add',idempotencyKey:randomUUID(),artifact:original});
 assert.equal(added.change.status,'completed');
 const before=(await domain.workRead({workspaceId:'workspace',workId:work.id})).work.artifacts.at(-1);
 assert.match(artifactText(before),/표면 비교/);
 const rearranged={...before,composition:stackComposition(before.composition.blocks)};
 const change=await domain.changeSubmit({workspaceId:'workspace',workId:work.id,artifactId:before.id,baseRevision:before.revision,mode:'proposal',idempotencyKey:randomUUID(),artifact:rearranged});
 assert.equal(change.change.status,'review');
 const pending=(await domain.workRead({workspaceId:'workspace',workId:work.id})).work.artifacts.at(-1);
 assert.deepEqual(pending.composition.rows,before.composition.rows);
 await domain.changeAction({workspaceId:'workspace',changeId:change.change.id,action:'apply'});
 const after=(await domain.workRead({workspaceId:'workspace',workId:work.id})).work.artifacts.at(-1);
 assert.deepEqual(after.composition.blocks,before.composition.blocks);
 assert.notDeepEqual(after.composition.rows,before.composition.rows);
 await assert.rejects(domain.changeSubmit({workspaceId:'workspace',workId:work.id,artifactId:after.id,baseRevision:after.revision,mode:'selection',idempotencyKey:randomUUID(),selection:{artifactId:after.id},replacement:'바꾸기'}),{status:422});
}));

test('reference material starts a blank work without copying its body',()=>{
 const source={id:'reference-1',title:'검사 자료',body:'참조할 원문',kind:'자료'};
 const before=structuredClone(source),work=newWork('검사 검토',source);
 assert.deepEqual(work.sourceIds,[source.id]);
 assert.equal(work.artifacts[0].kind,'blank');
 assert.equal(work.artifacts[0].title,'');
 assert.equal(artifactText(work.artifacts[0]),'');
 assert.deepEqual(source,before);
});
test('explicitly starting from a saved artifact makes an independent copy',()=>{
 const artifact=contentArtifact('검토 결과'),source={id:'snapshot-1',artifact};
 const work=newWork('추가 검토',source);
 assert.deepEqual(work.sourceIds,[source.id]);
 assert.notEqual(work.artifacts[0].id,artifact.id);
 assert.deepEqual(work.artifacts[0].composition,artifact.composition);
 assert.notStrictEqual(work.artifacts[0].composition,artifact.composition);
});
test('workspace file references keep paths instead of copying file contents',()=>{
 for(const name of ['report.html','photo.png','notes.md','data.csv','paper.pdf']){
  const source=workspaceFileSource({name,path:'references/'+name});
  assert.equal(source.filePath,'references/'+name);
  assert.equal(source.body,'');
  assert.equal(source.live,true);
  const state=initialState();state.linkedFiles=[source];
  assert.equal(validState(state),true);
 }
});

test('an agent-authored HTML artifact keeps a single file reference and separate source links',async()=>fixture(async({domain,read})=>{
 const state=await read(),work=state.state.works[0],before=structuredClone(work);
 const artifact=workspaceFileArtifact({name:'inspection.html',path:'outputs/inspection.html'},'workspace');
 const result=await domain.changeSubmit({workspaceId:'workspace',workId:work.id,mode:'add',artifact,idempotencyKey:randomUUID()});
 const updated=(await domain.workRead({workspaceId:'workspace',workId:work.id})).work;
 const saved=updated.artifacts.find(item=>item.id===result.change.artifactId);
 assert.equal(saved.composition.blocks.length,1);
 assert.equal(saved.composition.blocks[0].kind,'file');
 assert.equal(new URL(saved.composition.blocks[0].content.href,'http://flow.local').searchParams.get('path'),'outputs/inspection.html');
 assert.deepEqual(updated.sourceIds,before.sourceIds);
 assert.deepEqual(updated.artifacts.filter(item=>item.id!==saved.id),before.artifacts);
}));

test('v3 data is only migrated on a guarded write, and v3 clients cannot overwrite it',async()=>fixture(async(service,directory)=>{
 const original=initialState(),legacy={...original,version:3};delete legacy.libraryEntries;
 const path=join(directory,'workspace.json'),bytes=JSON.stringify(legacy);
 await writeFile(path,bytes);
 const migrated=await service.read();
 assert.equal(migrated.state.version,4);
 assert.deepEqual(migrated.state.works,legacy.works);
 assert.deepEqual(migrated.state.librarySnapshots,legacy.librarySnapshots);
 assert.deepEqual(migrated.state.libraryEntries,[]);
 assert.equal(await readFile(path,'utf8'),bytes);
 await assert.rejects(service.put(legacy,migrated.revision),{status:422});
 await service.put(migrated.state,migrated.revision);
 const saved=await service.read();
 await assert.rejects(service.put({...legacy,works:[]},saved.revision),{status:422});
 assert.deepEqual((await service.read()).state,saved.state);
}));

test('agent HTML replaces the same primary artifact immediately, with conflict checks and undo',async()=>fixture(async({domain,read})=>{
 const made=await domain.workCreate({workspaceId:'workspace',name:'수요 분석',idempotencyKey:randomUUID()});
 const work=made.work,blank=work.artifacts[0];
 assert.equal(blank.kind,'blank');
 const html={kind:'html',title:'수요 분석',html:'<!doctype html><h1>수요 분석</h1><button onclick="this.textContent=2">1</button>',assets:[]};
 const input={workspaceId:'workspace',workId:work.id,artifactId:blank.id,baseRevision:0,mode:'replace',artifact:html,idempotencyKey:randomUUID()};
 const change=(await domain.changeSubmit(input)).change;
 assert.equal(change.status,'completed');
 const current=(await domain.workRead({workspaceId:'workspace',workId:work.id})).work;
 assert.equal(current.artifacts.length,1);
 assert.equal(current.activeArtifactId,blank.id);
 assert.equal(current.artifacts[0].html,html.html);
 assert.equal(current.artifacts[0].revision,1);
 assert.equal('draftText' in current.artifacts[0],false);
 const revision=(await read()).revision;
 assert.equal((await domain.changeSubmit(input)).change.id,change.id);
 assert.equal((await read()).revision,revision);
 await assert.rejects(domain.changeSubmit({...input,idempotencyKey:randomUUID(),artifact:{...html,html:'<h1>낡은 변경</h1>'}}),{status:409});
 assert.equal((await read()).revision,revision);
 await domain.changeAction({workspaceId:'workspace',changeId:change.id,action:'undo'});
 const restored=(await domain.workRead({workspaceId:'workspace',workId:work.id})).work.artifacts[0];
 assert.equal(restored.kind,'blank');assert.equal(restored.id,blank.id);assert.equal(restored.revision,2);
 assert.equal('html' in restored,false);assert.equal('assets' in restored,false);
}));

test('HTML manifests use immutable imported assets and preserve older revisions',async()=>fixture(async(service,directory)=>{
 const bytes=Buffer.from([137,80,78,71,13,10,26,10]),assetId=createHash('sha256').update(bytes).digest('hex')+'.png';
 await mkdir(join(directory,'assets'),{recursive:true});await writeFile(join(directory,'assets',assetId),bytes);
 const w=(await service.domain.workList({workspaceId:'workspace'})).works[0];
 const input={workspaceId:'workspace',workId:w.id,artifactId:w.artifact.id,baseRevision:w.artifact.revision,mode:'replace',idempotencyKey:randomUUID(),
  artifact:{kind:'html',title:'이미지 비교',html:'<h1>이미지 비교</h1><img src="images/part.png">',assets:[{name:'images/part.png',src:'/api/flow/assets/'+assetId}]}};
 const first=await service.domain.changeSubmit(input);
 const a=(await service.domain.workRead({workspaceId:'workspace',workId:w.id})).work.artifacts[0];
 assert.deepEqual(a.assets,input.artifact.assets);
 await assert.rejects(service.domain.changeSubmit({...input,baseRevision:a.revision,idempotencyKey:randomUUID(),artifact:{...input.artifact,assets:[{name:'part.png',src:'https://outside.invalid/a.png'}]}}),{status:422});
 await assert.rejects(service.domain.changeSubmit({...input,baseRevision:a.revision,idempotencyKey:randomUUID(),artifact:{...input.artifact,assets:{bad:true}}}),{status:422});
 const revised=await service.domain.changeSubmit({...input,baseRevision:a.revision,idempotencyKey:randomUUID(),artifact:{...input.artifact,html:'<h1>비교 결과</h1>'}});
 assert.deepEqual(revised.change.before.assets,a.assets);
 await assert.rejects(service.domain.changeAction({workspaceId:'workspace',changeId:first.change.id,action:'undo'}),{status:409});
 assert.deepEqual(await readFile(join(directory,'assets',assetId)),bytes);
}));

test('library curation is scoped, reference-based, idempotent and revision guarded',async()=>fixture(async({domain,read},directory)=>{
 const work=(await domain.workList({workspaceId:'workspace'})).works[0];
 const path=join(directory,'original.txt');await writeFile(path,'원문');
 const entry={id:randomUUID(),title:'검토 자료',body:'별도 조사 항목 73214',scope:{kind:'work',workId:work.id},reference:{kind:'host-file',root:'workspace',path:'자료/original.txt'},sourceVersion:'sha256:original'};
 const input={workspaceId:'workspace',entry,expectedRevision:0,idempotencyKey:randomUUID()};
 const first=(await domain.libraryUpsert(input)).entry;
 assert.equal(first.revision,1);
 const revision=(await read()).revision;
 assert.deepEqual((await domain.libraryUpsert(input)).entry,first);
 assert.equal((await read()).revision,revision);
 const list=await domain.libraryList({workspaceId:'workspace',workId:work.id,query:'별도 조사 항목 73214'});
 assert.equal(list.entries.length,1);assert.equal('body' in list.entries[0],false);assert.deepEqual(list.entries[0].scope,entry.scope);
 assert.equal((await domain.libraryRead({workspaceId:'workspace',entryId:entry.id})).entry.body,entry.body);
 const other=(await domain.workCreate({workspaceId:'workspace',name:'다른 작업',idempotencyKey:randomUUID()})).work;
 assert.equal((await domain.libraryList({workspaceId:'workspace',workId:other.id,query:'검토 자료'})).entries.length,0);
 await assert.rejects(domain.libraryUpsert({...input,entry:{...entry,id:randomUUID()},idempotencyKey:randomUUID()}),{status:409});
 await assert.rejects(domain.libraryUpsert({...input,entry:{...entry,title:'낡은 수정'},idempotencyKey:randomUUID()}),{status:409});
 const sourceBefore=(await domain.workRead({workspaceId:'workspace',workId:other.id})).work.artifacts;
 await domain.workUpdate({workspaceId:'workspace',workId:other.id,expectedRevision:other.revision,sourceIds:[entry.id],idempotencyKey:randomUUID()});
 const linked=await domain.workRead({workspaceId:'workspace',workId:other.id});
 assert.deepEqual(linked.work.artifacts,sourceBefore);assert.deepEqual(linked.sources[0].reference,entry.reference);
 assert.equal(await readFile(path,'utf8'),'원문');
}));

test('library merges independent edits but never guesses overlapping changes',()=>{
 const base=initialState(),a={id:'material-a',title:'자료 A',body:'원문 A',revision:1,scope:{kind:'workspace'}},b={...a,id:'material-b',title:'자료 B'};
 base.libraryEntries=[a,b];
 const desired=structuredClone(base),current=structuredClone(base);
 desired.libraryEntries[0]={...a,body:'수정 A',revision:2};
 current.libraryEntries[1]={...b,body:'수정 B',revision:2};
 const merged=mergeStates(base,desired,current);
 assert.equal(merged.libraryEntries[0].body,'수정 A');assert.equal(merged.libraryEntries[1].body,'수정 B');
 current.libraryEntries[0]={...a,body:'동시 수정 A',revision:2};
 assert.throws(()=>mergeStates(base,desired,current),{status:409});
});

test('saving an unchanged artifact again reuses its existing library snapshot',async()=>fixture(async({domain,read})=>{
 const work=(await domain.workList({workspaceId:'workspace'})).works[0];
 const input={workspaceId:'workspace',workId:work.id,artifactId:work.artifact.id,expectedRevision:work.artifact.revision};
 const one=await domain.snapshotCreate({...input,idempotencyKey:randomUUID()});
 const two=await domain.snapshotCreate({...input,idempotencyKey:randomUUID()});
 assert.equal(one.source.id,two.source.id);assert.equal((await read()).state.librarySnapshots.filter(s=>s.artifactId===work.artifact.id).length,1);
}));

test('agent source catalogs retain scope and provenance without widening another work scope',async()=>fixture(async({domain,read})=>{
 const {state}=await read(),first=state.works[0];
 const {work:other}=await domain.workCreate({workspaceId:'workspace',name:'다른 작업',idempotencyKey:randomUUID()});
 const reference={kind:'context',locator:{product:'corpus',spaceId:'project',documentId:'methods'}};
 for(const entry of [
  {id:'first-material',title:'현재 작업 자료',scope:{kind:'work',workId:first.id},body:'정리한 내용',reference,sourceVersion:'v1'},
  {id:'other-material',title:'다른 작업 자료',scope:{kind:'work',workId:other.id},body:'다른 작업의 메모'},
  {id:'shared-material',title:'공통 자료',scope:{kind:'workspace'},body:'다시 참고할 내용'}
 ])await domain.libraryUpsert({workspaceId:'workspace',entry,expectedRevision:0,idempotencyKey:randomUUID()});
 const before=await domain.workRead({workspaceId:'workspace',workId:first.id});
 const material=before.sourceCatalog.find(item=>item.id==='first-material');
 assert.deepEqual(material.scope,{kind:'work',workId:first.id});assert.deepEqual(material.reference,reference);assert.equal(material.sourceVersion,'v1');
 assert.equal('body' in material,false);assert.equal(before.sourceCatalog.some(item=>item.id==='other-material'),false);
 assert.ok(before.sourceCatalog.some(item=>item.id==='shared-material'));
 await domain.workUpdate({workspaceId:'workspace',workId:first.id,expectedRevision:before.work.revision,idempotencyKey:randomUUID(),sourceIds:['other-material']});
 const after=await domain.workRead({workspaceId:'workspace',workId:first.id});
 assert.deepEqual(after.sources[0].scope,{kind:'work',workId:other.id});
 assert.deepEqual(after.sourceCatalog.find(item=>item.id==='other-material').scope,{kind:'work',workId:other.id});
 assert.deepEqual(after.work.artifacts,before.work.artifacts);
}));

test('legacy examples remain identifiable and are not promoted to personal criteria',async()=>fixture(async({domain,read})=>{
 const before=await read(),work=before.state.works[0];
 const result=await domain.workRead({workspaceId:'workspace',workId:work.id});
 const example=result.sourceCatalog.find(item=>item.id==='writing');
 assert.equal(example.example,true);assert.equal(example.collection,'예시 자료');assert.equal(example.scope,undefined);
 assert.equal(result.sources.find(item=>item.id==='observation').example,true);
 const catalog=await domain.libraryList({workspaceId:'workspace'});
 assert.equal(catalog.entries.find(item=>item.id==='writing').example,true);
 const after=await read();assert.equal(after.revision,before.revision);assert.deepEqual(after.state.libraryEntries,[]);
}));
