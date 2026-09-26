import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm,readFile,mkdir,writeFile,symlink} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createServer} from 'node:http';
import {initialState,emptyState,migrateState,validState,newWork,blankArtifact,contentArtifact,sourceContentArtifact,diagramArtifact,documentArtifact,workspaceFileArtifact,reviseArtifact,libraryItems,sourceItems,saveSnapshot} from '../src/model.js';
import {appendContentBlock,workspaceFilePresentation} from '../src/work-surface/composition-editor.js';
import {createWorkspaceService,assertWorkHost} from '../server/workspace.mjs';
import {readCache} from '../src/useWorkspace.js';
import {mergeStates} from '../src/merge.js';
import {layoutWidthPresets,orderedArtifacts,artifactSpan,setArtifactSpan,moveArtifact} from '../src/surface-layout.js';
import config from '../vite.config.mjs';
import {filePreviewKind,fileContentUrl} from '../src/file-preview.js';

test('artifact width choices use the same labels as the content editor',()=>{
 assert.deepEqual(layoutWidthPresets.map(({value,label})=>[value,label]),[[12,'전체 폭'],[8,'넓게'],[6,'절반'],[4,'좁게']]);
 assert.equal(Object.isFrozen(layoutWidthPresets),true);
});

test('migrates v1 without rewriting documents, references, saved IDs or last selection',()=>{
 const legacy={version:1,theme:'dark',activeId:'a',savedIds:['a'],screen:'files',filesLocation:{path:'자료',selected:'file1'},files:[],libraryFileIds:[],works:[{id:'a',name:'작업',title:'문서',sourceIds:['writing'],purpose:'비교',format:'slides',lastBlockId:'b',blocks:[{id:'b',heading:'제목',text:'말하지 않은 내용도 유지한다.'}]}]};
 const state=migrateState(legacy);
 assert.ok(validState(state));assert.equal(state.works[0].artifacts[0].id,'a');
 assert.deepEqual(state.works[0].artifacts[0].blocks,legacy.works[0].blocks);
 assert.deepEqual(state.savedIds,['a']);assert.deepEqual(state.filesLocation,legacy.filesLocation);
 assert.equal(state.works[0].artifacts[0].lastBlockId,'b');assert.equal(state.works[0].artifacts[0].format,'slides');
 assert.equal(libraryItems(state).find(i=>i.id==='a').artifactId,'a');
 assert.equal(legacy.version,1);assert.ok(!('accepted' in state.works[0].artifacts[0]));
});
test('unknown or damaged storage never falls back silently',()=>{
 assert.throws(()=>migrateState({version:8,works:[]}));
 assert.throws(()=>migrateState({version:1,works:[{blocks:[]}]}));
});
test('artifact changes and curation stay scoped within one work',()=>{
 const s=initialState(),work=s.works[0],doc=work.artifacts[0],diagram=diagramArtifact('검토 흐름',doc);
 work.artifacts.push(diagram);s.savedIds.push(diagram.id);
 const changed=reviseArtifact(s,work.id,diagram.id,{title:'새 제목'},0);
 assert.deepEqual(changed.works[0].artifacts[0],doc);
 assert.equal(changed.works[0].artifacts[1].revision,1);
 assert.throws(()=>reviseArtifact(changed,work.id,diagram.id,{title:'낡은 결과'},0));
 const saved=libraryItems(changed).filter(i=>i.kind==='결과물');
 assert.equal(saved.length,1);assert.equal(saved[0].artifactId,diagram.id);assert.equal(saved[0].title,'새 제목');
 assert.equal(newWork('사본',saved[0]).artifacts[0].kind,'diagram');
});
test('a new work keeps references separate from its blank artifact',()=>{
 const empty=newWork('새 작업');
 assert.equal(emptyState().works[0].artifacts[0].kind,'blank');
 assert.ok(validState(emptyState()));
 assert.equal(empty.artifacts[0].kind,'blank');
 assert.equal(empty.artifacts[0].title,'');
 assert.equal(empty.activeArtifactId,empty.artifacts[0].id);
 const sourceWork=newWork('자료에서 시작',{id:'source',title:'현장 메모',body:'참고 내용'});
 assert.equal(sourceWork.artifacts[0].kind,'blank');
 assert.deepEqual(sourceWork.sourceIds,['source']);
 assert.equal(sourceWork.artifacts[0].composition,undefined);
});

test('explicit content conversion preserves text while new work only links references',()=>{
 const existing=contentArtifact('검사 화면');
 existing.composition.blocks[0].content.paragraphs=['기존 기록'];
 const source={id:'memo',title:'현장 메모',body:'첫 줄\n\n둘째 줄'};
 const imported=sourceContentArtifact(existing.title,source);
 const composition=appendContentBlock(existing.composition,imported.composition.blocks[0]);
 assert.equal(imported.composition.blocks[0].content.heading,'현장 메모');
 assert.deepEqual(imported.composition.blocks[0].content.paragraphs,[source.body]);
 assert.deepEqual(composition.blocks[0],existing.composition.blocks[0]);
 assert.deepEqual(composition.rows[0],existing.composition.rows[0]);
 assert.equal(composition.blocks[1].content.paragraphs[0],source.body);
 const long={...source,body:'가'.repeat(20001)};
 assert.equal(sourceContentArtifact('검사 화면',long),null);
 const longWork=newWork('긴 자료',long);
 assert.equal(longWork.artifacts[0].kind,'blank');
 assert.deepEqual(longWork.sourceIds,[long.id]);
 const plain=documentArtifact('현장 메모',{body:'첫 문단\n\n둘째 문단'});
 assert.deepEqual(plain.blocks.map(({heading,text})=>({heading,text})),[{heading:'',text:'첫 문단'},{heading:'',text:'둘째 문단'}]);
 const sections={id:'sections',title:'문서',blocks:[{heading:'첫 부분',text:'원문 1'},{heading:'둘째 부분',text:'원문 2'}]};
 const copied=sourceContentArtifact('새 작업',sections);
 assert.deepEqual(copied.composition.blocks.map(block=>block.content.heading),['첫 부분','둘째 부분']);
 assert.deepEqual(copied.composition.blocks.map(block=>block.content.paragraphs[0]),['원문 1','원문 2']);
});

test('a blank work surface keeps its identity when first used for text or a diagram',()=>{
 const state=initialState(),work=state.works[0],blank=blankArtifact();
 work.artifacts.push(blank);work.activeArtifactId=blank.id;
 assert.ok(validState(state));
 const draft=reviseArtifact(state,work.id,blank.id,{draftText:'첫 내용'},0);
 assert.equal(draft.works[0].artifacts.at(-1).kind,'blank');
 const document=reviseArtifact(draft,work.id,blank.id,{kind:'document',format:'document',lastBlockId:null,blocks:[{id:'first',heading:'',text:'첫 내용'}]},1);
 assert.equal(document.works[0].artifacts.at(-1).id,blank.id);
 assert.equal('draftText' in document.works[0].artifacts.at(-1),false);
 assert.equal(document.works[0].artifacts.at(-1).blocks[0].text,'첫 내용');
 assert.equal(document.works[0].artifacts[0],work.artifacts[0]);
 const diagram=reviseArtifact(state,work.id,blank.id,{kind:'diagram',nodes:[{id:'n',label:'항목',x:500,y:300}],edges:[]},0);
 assert.equal(diagram.works[0].artifacts.at(-1).kind,'diagram');
 assert.equal(diagram.works[0].artifacts.at(-1).revision,1);
});
test('an untouched blank work surface can be removed without losing another artifact',()=>{
 const base=initialState(),work=base.works[0],blank=blankArtifact();
 work.artifacts.push(blank);work.activeArtifactId=blank.id;
 const desired=structuredClone(base);desired.works[0].artifacts.pop();desired.works[0].activeArtifactId=work.artifacts[0].id;
 const merged=mergeStates(base,desired,structuredClone(base));
 assert.equal(merged.works[0].artifacts.length,1);
 assert.equal(merged.works[0].artifacts[0].id,work.artifacts[0].id);
 const changed=structuredClone(base);changed.works[0].artifacts.at(-1).draftText='another edit';
 assert.throws(()=>mergeStates(base,desired,changed));
});
test('diagram structure and image origins are validated',()=>{
 const s=initialState(),d=diagramArtifact('도식');s.works[0].artifacts.push(d);
 assert.ok(validState(s));
 d.edges.push({id:'bad',from:d.nodes[0].id,to:'missing',label:''});assert.equal(validState(s),false);
 d.edges=[];
 s.works[0].artifacts.push({id:'image',title:'이미지',kind:'image',revision:0,width:10,height:10,src:'https://external.example/image.png'});
 assert.equal(validState(s),false);
});
test('workspace files share one insertion path and retain the selected source',()=>{
 for(const [name,viewer,kind,label] of [
  ['현장 영상.MP4','video','video','영상'],['녹음.ogg','audio','audio','오디오'],
  ['회의.m4a','audio','audio','오디오'],['설명.AAC','audio','audio','오디오'],
  ['사진.jpeg','image','image','이미지'],['문서.PDF','pdf','pdf','PDF'],
  ['메모.md','text','text','Markdown'],['보고서.HTML','text','html','HTML'],
  ['화면.htm','text','html','HTML']
 ]){
  assert.equal(filePreviewKind(name),kind);
  const file={name,path:'자료/'+name};
  const artifact=workspaceFileArtifact(file,'workspace'),block=artifact.composition.blocks[0];
  assert.equal(artifact.kind,'content');
  assert.equal(artifact.title,name.replace(/\.[^.]+$/,''));
  assert.equal(block.kind,'file');
  assert.equal(block.content.name,name);assert.equal(block.content.type,label);
  assert.equal(block.content.href,fileContentUrl('workspace',file.path));
  assert.equal(workspaceFilePresentation(file.path).viewer,viewer);
  const state=initialState();state.works[0].artifacts.push(artifact);state.works[0].activeArtifactId=artifact.id;
  assert.ok(validState(state));
 }
 for(const file of [null,{name:'run.exe',path:'자료/run.exe'},{name:'secret.png',path:'../secret.png'}])
  assert.throws(()=>workspaceFileArtifact(file,'workspace'));
});

test('the work menu does not grow with content types',async()=>{
 const source=await readFile(new URL('../src/App.jsx',import.meta.url),'utf8');
 assert.match(source,/<WorkCanvas /);
 assert.doesNotMatch(source,/beginArtifact|ContentEditor|ArtifactLayoutMenu|내용 수정/);
 assert.doesNotMatch(source,/이미지를 현재 작업 화면에 넣기|이미지를 새 작업 화면에 넣기|도식 만들기|작업 화면 만들기/);
 assert.doesNotMatch(source,/chooseImageImport|function addDiagram|function addContent/);
 const files=await readFile(new URL('../src/FileExplorer.jsx',import.meta.url),'utf8');
 assert.doesNotMatch(files,/이미지로 새 작업|onImportImage/);
});

test('Spark writes are atomic and reject stale revisions, including simultaneous saves',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'flow-save-'));
 try{
  const service=createWorkspaceService(directory),s=initialState(),empty=await service.read();
  assert.equal(empty.revision,'empty');
  assert.equal(empty.state,null);
  const first=await service.put(s,empty.revision);assert.deepEqual((await service.read()).state,s);
  await assert.rejects(service.put(s,'empty'),{status:409});
  const a=structuredClone(s),b=structuredClone(s);a.works[0].name='A';b.works[0].name='B';
  const results=await Promise.allSettled([service.put(a,first.revision),service.put(b,first.revision)]);
  assert.equal(results.filter(r=>r.status==='fulfilled').length,1);
  assert.equal(results.find(r=>r.status==='rejected').reason.status,409);
  assert.equal((await service.read()).state.works[0].name,'A');service.close();
 }finally{await rm(directory,{recursive:true,force:true})}
});
test('HTTP endpoints reject cross-origin writes, executable uploads and traversal',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'flow-api-')),service=createWorkspaceService(directory);
 const server=createServer((req,res)=>service.handler(req,res,()=>{res.writeHead(404);res.end()}));
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const url='http://127.0.0.1:'+server.address().port;
 try{
  const headers={'X-Toolkit-Flow':'1','Content-Type':'application/json','If-Match':'empty'};
  let response=await fetch(url+'/api/flow/workspace',{method:'PUT',headers:{...headers,Origin:'https://other.example'},body:JSON.stringify(initialState())});assert.equal(response.status,403);
  response=await fetch(url+'/api/flow/workspace',{method:'PUT',headers:{...headers,Origin:url},body:JSON.stringify(initialState())});assert.equal(response.status,200);
  response=await fetch(url+'/api/flow/assets',{method:'POST',headers:{'X-Toolkit-Flow':'1','Content-Type':'image/svg+xml',Origin:url},body:'<svg><script>alert(1)</script></svg>'});assert.equal(response.status,415);
  response=await fetch(url+'/api/flow/assets/%2e%2e%2fworkspace.json');assert.equal(response.status,404);
  response=await fetch(url+'/api/flow/workspace',{headers:{Origin:'https://other.example'}});assert.equal(response.status,403);
  response=await fetch(url+'/api/flow/assets',{method:'POST',headers:{'X-Toolkit-Flow':'1','X-Toolkit-Flow-Workspace-ID':'other','Content-Type':'image/png',Origin:url},body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2AXcAAAAASUVORK5CYII=','base64')});assert.equal(response.status,400);
  response=await fetch(url+'/api/flow/assets',{method:'POST',headers:{'X-Toolkit-Flow':'1','Content-Type':'image/png',Origin:url},body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2AXcAAAAASUVORK5CYII=','base64')});
  assert.equal(response.status,201);const image=await response.json();
  response=await fetch(url+image.src);assert.equal(response.status,200);assert.equal(response.headers.get('content-type'),'image/png');
  assert.equal(response.headers.get('x-content-type-options'),'nosniff');
 }finally{await new Promise(resolve=>server.close(resolve));await rm(directory,{recursive:true,force:true})}
});


test('a new browser reads the workspace instead of treating the starter view as an unsaved edit',()=>{
 const cache=readCache({getItem:()=>null},{getItem:()=>null});
 assert.equal(cache.dirty,false);assert.equal(cache.revision,null);assert.equal(cache.error,null);
 assert.equal(cache.state.works[0].artifacts[0].kind,'blank');
});
test('a tab keeps its own unsaved draft even when another tab updates the shared cache',()=>{
 const shared=initialState(),draft=structuredClone(shared);draft.works[0].name='이 화면의 변경';
 const cache=readCache({getItem:key=>key==='toolkit-flow-v3'?JSON.stringify({state:shared,revision:'newer',dirty:false}):null},{getItem:()=>JSON.stringify({state:draft,revision:'older',dirty:true})});
 assert.equal(cache.state.works[0].name,'이 화면의 변경');assert.equal(cache.revision,'older');assert.equal(cache.dirty,true);
 const broken=readCache({getItem:()=>'{invalid'}, {getItem:()=>null});
 assert.ok(broken.error);assert.equal(broken.raw,'{invalid');
});
test('private persisted data is neither served as a source file nor watched for page reloads',()=>{
 assert.ok(config.server.fs.deny.includes('**/.data/**'));
 assert.ok(config.server.watch.ignored.includes('**/.data/**'));
 assert.equal(config.server.host,'127.0.0.1');
});

test('the work service checks its registered execution target and workspace',()=>{
 const target={root:'/home/user/Agent-Workspace/task',workspaceRoot:'/home/user/Agent-Workspace',host:'spark-1de5'};
 assert.doesNotThrow(()=>assertWorkHost(target,{platform:'linux',hostname:'spark-1de5'}));
 assert.doesNotThrow(()=>assertWorkHost(target,{platform:'darwin',hostname:'spark-1de5'}));
 assert.throws(()=>assertWorkHost(target,{platform:'linux',hostname:'other-host'}));
 assert.throws(()=>assertWorkHost({...target,root:'/Volumes/Agent-Workspace/task'},{platform:'linux',hostname:'spark-1de5'}));
});

test('Library snapshots keep the known artifact version after further edits',()=>{
 const initial=initialState(),w=initial.works[0],a=w.artifacts[0];
 const archived=saveSnapshot(initial,w.id,a.id);
 const changed=reviseArtifact(archived,w.id,a.id,{title:'다음 제목'},a.revision);
 const saved=libraryItems(changed).find(item=>item.id===archived.librarySnapshots.at(-1).id);
 assert.equal(saved.title,a.title);
 assert.equal(saved.artifact.revision,a.revision);
 assert.deepEqual(sourceItems(changed).find(item=>item.id===saved.id)?.artifact,saved.artifact);
 assert.equal(changed.works[0].artifacts[0].title,'다음 제목');
});

test('real Spark file browsing stays inside its registered workspace and never follows escaping links',async()=>{
 const root=await mkdtemp(join(tmpdir(),'flow-files-')),outside=await mkdtemp(join(tmpdir(),'flow-outside-'));
 await mkdir(join(root,'folder'));await writeFile(join(root,'folder','note.md'),'actual content');await writeFile(join(root,'folder','data.tsv'),'항목\t값\n가\t1');await writeFile(join(root,'folder','report.htm'),'<!doctype html><h1>보고서</h1>');await writeFile(join(root,'folder','tone.wav'),Buffer.from('RIFF0000WAVEexample'));await writeFile(join(root,'folder','meeting.m4a'),Buffer.concat([Buffer.from([0,0,0,16]),Buffer.from('ftypM4A recording')]));await writeFile(join(root,'folder','voice.aac'),Buffer.from([255,241,80,128,0,31,252]));await writeFile(join(root,'folder','false.m4a'),'not an m4a');await writeFile(join(root,'folder','false.aac'),'not an aac');await writeFile(join(root,'folder','false.mp3'),'not an mp3');await writeFile(join(root,'folder','report.pdf'),Buffer.from('%PDF-1.4\nexample'));await writeFile(join(root,'folder','false.pdf'),'not a pdf');await writeFile(join(outside,'secret.md'),'private');
 await symlink(outside,join(root,'escape'));
 const samplePng=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2AXcAAAAASUVORK5CYII=','base64');
 await writeFile(join(root,'folder','photo.png'),samplePng);await writeFile(join(root,'folder','false.png'),'not an image');
 const renderedPages=[];
 const service=createWorkspaceService(join(root,'.data'),{workspaceRoot:root,pdfInspector:async bytes=>{assert.equal(bytes.subarray(0,5).toString(),'%PDF-');return 3},pdfRenderer:async (bytes,page)=>{assert.equal(bytes.subarray(0,5).toString(),'%PDF-');renderedPages.push(page);return samplePng}});
 const server=createServer((req,res)=>service.handler(req,res,()=>{res.writeHead(404);res.end()}));
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const url='http://127.0.0.1:'+server.address().port;
 try{
  let response=await fetch(url+'/api/flow/files?workspaceId=workspace&path=folder');assert.equal(response.status,200);
  const list=await response.json();assert.deepEqual(list.items.map(item=>item.name).sort(),['data.tsv','false.aac','false.m4a','false.mp3','false.pdf','false.png','meeting.m4a','note.md','photo.png','report.htm','report.pdf','tone.wav','voice.aac']);
  response=await fetch(url+'/api/flow/files/content?workspaceId=workspace&path=folder%2Fnote.md');assert.equal(response.status,200);assert.equal(response.headers.get('content-type'),'application/json; charset=utf-8');const textFile=await response.json();assert.equal(textFile.content,'actual content');assert.equal(textFile.path,'folder/note.md');
  response=await fetch(url+'/api/flow/files/content?workspaceId=workspace&path=folder%2Fnote.md',{method:'HEAD'});assert.equal(response.status,200);assert.equal(response.headers.get('content-type'),'application/json; charset=utf-8');
  response=await fetch(url+'/api/flow/files/content?workspaceId=workspace&path=folder%2Fdata.tsv');assert.equal(response.status,200);const tsvFile=await response.json();assert.equal(tsvFile.content,'항목\t값\n가\t1');assert.equal(tsvFile.type,'text/plain');
  response=await fetch(url+'/api/flow/files/content?workspaceId=workspace&path=folder%2Freport.htm');assert.equal(response.status,200);const htmlFile=await response.json();assert.equal(htmlFile.content,'<!doctype html><h1>보고서</h1>');assert.equal(htmlFile.type,'text/plain');
  response=await fetch(url+'/api/flow/files/content?workspaceId=workspace&path=folder%2Fphoto.png',{method:'HEAD'});assert.equal(response.status,200);assert.equal(response.headers.get('content-type'),'image/png');
  response=await fetch(url+'/api/flow/files/content?workspaceId=workspace&path=folder%2Ffalse.png',{method:'HEAD'});assert.equal(response.status,415);
  response=await fetch(url+'/api/flow/files/content?workspaceId=workspace&path=folder%2Ftone.wav',{method:'HEAD'});assert.equal(response.status,200);assert.equal(response.headers.get('content-type'),'audio/wav');
  response=await fetch(url+'/api/flow/files/content?workspaceId=workspace&path=folder%2Ftone.wav',{headers:{Range:'bytes=0-3'}});assert.equal(response.status,206);assert.equal(response.headers.get('content-type'),'audio/wav');assert.equal(response.headers.get('content-range'),'bytes 0-3/19');assert.equal(Buffer.from(await response.arrayBuffer()).toString(),'RIFF');
  response=await fetch(url+'/api/flow/files/content?workspaceId=workspace&path=folder%2Fmeeting.m4a',{method:'HEAD'});assert.equal(response.status,200);assert.equal(response.headers.get('content-type'),'audio/mp4');
  response=await fetch(url+'/api/flow/files/content?workspaceId=workspace&path=folder%2Fvoice.aac',{headers:{Range:'bytes=0-1'}});assert.equal(response.status,206);assert.equal(response.headers.get('content-type'),'audio/aac');assert.deepEqual([...new Uint8Array(await response.arrayBuffer())],[255,241]);
  response=await fetch(url+'/api/flow/files/content?workspaceId=workspace&path=folder%2Freport.pdf',{method:'HEAD'});assert.equal(response.status,200);assert.equal(response.headers.get('content-type'),'application/pdf');assert.equal(response.headers.get('x-content-type-options'),'nosniff');
  response=await fetch(url+'/api/flow/files/content?workspaceId=workspace&path=folder%2Freport.pdf');assert.equal(response.status,200);assert.equal(Buffer.from(await response.arrayBuffer()).subarray(0,5).toString(),'%PDF-');
  response=await fetch(url+'/api/flow/files/preview?workspaceId=workspace&path=folder%2Freport.pdf',{method:'HEAD'});assert.equal(response.status,200);assert.equal(response.headers.get('x-flow-pdf-pages'),'3');assert.deepEqual(renderedPages,[]);
  response=await fetch(url+'/api/flow/files/preview?workspaceId=workspace&path=folder%2Freport.pdf&page=2');assert.equal(response.status,200);assert.equal(response.headers.get('content-type'),'image/png');assert.equal(response.headers.get('x-flow-pdf-pages'),'3');assert.deepEqual(Buffer.from(await response.arrayBuffer()),samplePng);assert.deepEqual(renderedPages,[2]);
  response=await fetch(url+'/api/flow/files/preview?workspaceId=workspace&path=folder%2Freport.pdf&page=4');assert.equal(response.status,416);
  response=await fetch(url+'/api/flow/files/preview?workspaceId=workspace&path=folder%2Freport.pdf&page=0');assert.equal(response.status,400);
  response=await fetch(url+'/api/flow/files/preview?workspaceId=workspace&path=folder%2Fnote.md');assert.equal(response.status,415);
  response=await fetch(url+'/api/flow/files/content?workspaceId=workspace&path=folder%2Ffalse.pdf');assert.equal(response.status,415);
  response=await fetch(url+'/api/flow/files/content?workspaceId=workspace&path=folder%2Ffalse.mp3');assert.equal(response.status,415);
  response=await fetch(url+'/api/flow/files/content?workspaceId=workspace&path=folder%2Ffalse.m4a');assert.equal(response.status,415);
  response=await fetch(url+'/api/flow/files/content?workspaceId=workspace&path=folder%2Ffalse.aac');assert.equal(response.status,415);
  response=await fetch(url+'/api/flow/files?workspaceId=workspace&path=..%2F');assert.ok([400,403].includes(response.status));
  response=await fetch(url+'/api/flow/files/content?workspaceId=workspace&path=escape%2Fsecret.md');assert.equal(response.status,403);
  response=await fetch(url+'/api/flow/files?workspaceId=wrong');assert.equal(response.status,400);
 }finally{await new Promise(resolve=>server.close(resolve));await rm(root,{recursive:true,force:true});await rm(outside,{recursive:true,force:true})}
});

test('one work surface orders and sizes pieces without changing their content',()=>{
 const state=initialState(),work=state.works[0],first=work.artifacts[0],second=diagramArtifact('흐름',first);
 work.artifacts.push(second);
 const sized=setArtifactSpan(work,second.id,6);
 const arranged=moveArtifact(sized,second.id,-1);
 assert.deepEqual(orderedArtifacts(arranged).map(item=>item.id),[second.id,first.id]);
 assert.equal(artifactSpan(arranged,second.id),6);
 assert.equal(artifactSpan(arranged,first.id),12);
 assert.deepEqual(arranged.artifacts,work.artifacts);
 state.works[0]=arranged;
 assert.ok(validState(state));
 assert.equal(validState({...state,works:[{...arranged,surfaceLayout:{order:[first.id,first.id],spans:{}}}]}),false);
 const third=blankArtifact();
 arranged.artifacts.push(third);
 assert.deepEqual(orderedArtifacts(arranged).map(item=>item.id),[second.id,first.id,third.id]);
});

test('layout changes merge with separate content edits',()=>{
 const base=initialState(),work=base.works[0],second=diagramArtifact('흐름',work.artifacts[0]);
 work.artifacts.push(second);
 const desired=structuredClone(base);
 desired.works[0]=setArtifactSpan(desired.works[0],second.id,6);
 const current=reviseArtifact(structuredClone(base),work.id,work.artifacts[0].id,{title:'수정한 제목'},0);
 const merged=mergeStates(base,desired,current);
 assert.equal(merged.works[0].artifacts[0].title,'수정한 제목');
 assert.equal(artifactSpan(merged.works[0],second.id),6);
 assert.ok(validState(merged));
});
