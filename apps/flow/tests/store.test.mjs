import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm,writeFile,readFile,stat} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {DatabaseSync} from 'node:sqlite';
import {randomUUID} from 'node:crypto';
import {createServer} from 'node:http';
import {createWorkspaceService} from '../server/workspace.mjs';
import {emptyState,initialState,newWork,migrateState} from '../src/model.js';

const html=(id,revision,size=512*1024)=>({id,revision,kind:'html',title:'검토 자료',assets:[],html:'<!doctype html><h1>검토 자료</h1><p>'+String(revision).padStart(8,'0')+'a'.repeat(size-70)+'</p>'});
async function fixture(fn){
 const directory=await mkdtemp(join(tmpdir(),'flow-sqlite-')),service=createWorkspaceService(directory,{workspaceId:'workspace',toolkitUrl:'https://toolkit.example'});
 try{return await fn(service,directory)}finally{service.close();await rm(directory,{recursive:true,force:true})}
}
test('200 revisions keep work, change and library reads bounded; 2MiB round trip and undo are pinned',async()=>fixture(async({domain,reads,store},dir)=>{
 let a=html('artifact-large',0);
 const {work}=await domain.workCreate({workspaceId:'workspace',name:'반복 수정',artifact:a,idempotencyKey:randomUUID()});
 a=work.artifacts[0];
 const first=reads.workRead({workspaceId:'workspace',workId:work.id});
 const size=Buffer.byteLength(JSON.stringify(first));let last;
 for(let i=1;i<=200;i++){
  a=html(a.id,i);
  last=await domain.changeSubmit({workspaceId:'workspace',workId:work.id,artifactId:a.id,baseRevision:i-1,artifact:a,mode:'replace',idempotencyKey:randomUUID()});
 }
 const read=reads.workRead({workspaceId:'workspace',workId:work.id});
 assert.ok(Buffer.byteLength(JSON.stringify(read))<size+4000);
 assert.equal('html' in read.work.artifacts[0],false);
 assert.equal(read.changes.length,0);assert.equal(read.undo.appliedRevision,200);
 assert.equal(reads.changeList({workspaceId:'workspace',workId:work.id,limit:100}).changes.length,100);
 assert.throws(()=>reads.changeList({workspaceId:'workspace',workId:work.id,limit:101}),{status:422});
 const db=new DatabaseSync(join(dir,'workspace.sqlite'));
 assert.equal(db.prepare('SELECT count(*) AS n FROM artifact_versions').get().n,201);
 assert.equal(db.prepare('SELECT count(*) AS n FROM payloads').get().n,201);
 const bytes=await stat(join(dir,'workspace.sqlite'));assert.ok(bytes.size<130*1024*1024);db.close();
 a={...a,html:'x'.repeat(2*1024*1024)};
 const request={workspaceId:'workspace',workId:work.id,artifactId:a.id,baseRevision:200,artifact:a,mode:'replace',idempotencyKey:randomUUID()};
 const applied=await domain.changeSubmit(request);
 assert.equal((await domain.changeSubmit(request)).change.id,applied.change.id);
 let offset=0,version,body='';
 do{
  const chunk=reads.artifactRead({workspaceId:'workspace',artifactId:a.id,revision:201,offset,version});
  assert.ok(Buffer.byteLength(chunk.content)<=65536);body+=chunk.content;version=chunk.version;
  if(chunk.nextOffset===null)break;offset=chunk.nextOffset;
 }while(true);
 assert.equal(JSON.parse(body).html,a.html);
 assert.throws(()=>reads.artifactRead({workspaceId:'workspace',artifactId:a.id,revision:200,version}),{status:409});
 assert.throws(()=>reads.artifactRead({workspaceId:'workspace',artifactId:a.id,revision:201,limit:1048577}),{status:422});
 await assert.rejects(domain.changeAction({workspaceId:'workspace',changeId:last.change.id,action:'undo'}),{status:409});
 await domain.changeAction({workspaceId:'workspace',changeId:applied.change.id,action:'undo'});
 assert.equal(store.artifact({artifactId:a.id,revision:202}).html,html(a.id,200).html);
 assert.equal((await domain.changeAction({workspaceId:'workspace',changeId:applied.change.id,action:'undo'})).change.status,'undone');
 assert.equal(store.artifact({artifactId:a.id}).revision,202);
}));
test('200 works and 1000 library entries are paginated without bodies',async()=>fixture(async({put,reads,read})=>{
 const state=emptyState();state.works=Array.from({length:200},(_,i)=>newWork('작업 '+i));state.activeId=state.works[0].id;
 state.libraryEntries=Array.from({length:1000},(_,i)=>({id:'entry-'+i,title:'자료 '+i,body:'가'.repeat(1000),scope:{kind:'workspace'},revision:1}));
 await put(state,'empty');
 assert.equal(reads.workList({workspaceId:'workspace'}).works.length,50);
 assert.equal(reads.workList({workspaceId:'workspace',offset:150,limit:100}).nextOffset,null);
 let offset=0,total=0;
 do{const page=reads.libraryList({workspaceId:'workspace',offset,limit:100});total+=page.entries.length;assert.ok(page.entries.every(e=>!('body' in e)));assert.ok(JSON.stringify(page).length<30000);if(page.nextOffset===null)break;offset=page.nextOffset}while(true);
 assert.equal(total,1000);assert.equal(reads.libraryRead({workspaceId:'workspace',entryId:'entry-999'}).entry.body.length,1000);
 assert.equal((await read()).state.works.length,200);
}));
test('v1 through v4 preserve old JSON and migrate only on a successful mutation',async()=>{
 const v1={version:1,theme:'light',activeId:'work',savedIds:['work'],works:[{id:'work',name:'기존 작업',title:'기존 문서',sourceIds:[],purpose:'',format:'document',blocks:[{id:'block',heading:'본문',text:'원문'}]}]};
 for(const version of [1,2,3,4])await fixture(async(service,dir)=>{
  const base=initialState(),legacy=version===1?v1:{...base,version,...(version===2?{works:base.works.map(({revision,...work})=>work)}:{})};
  const original=JSON.stringify(legacy);await writeFile(join(dir,'workspace.json'),original);
  const read=await service.read();assert.deepEqual(read.state,migrateState(legacy));assert.equal(service.store.isMigrated(),false);
  await assert.rejects(service.domain.workUpdate({workspaceId:'workspace',workId:read.state.works[0].id,expectedRevision:99,name:'바뀌면 안 됨',idempotencyKey:randomUUID()}),{status:409});
  assert.equal(service.store.isMigrated(),false);
  await service.domain.workUpdate({workspaceId:'workspace',workId:read.state.works[0].id,expectedRevision:0,name:'이어서 검토',idempotencyKey:randomUUID()});
  assert.equal(service.store.isMigrated(),true);assert.equal(await readFile(join(dir,'workspace.json'),'utf8'),original);
  assert.deepEqual(service.store.artifact({artifactId:read.state.works[0].artifacts[0].id,revision:0}),read.state.works[0].artifacts[0]);
 });
});
test('failed transactions preserve current data; a database never falls back to old JSON',async()=>fixture(async(service,dir)=>{
 const state=initialState();await writeFile(join(dir,'workspace.json'),JSON.stringify(state));await service.put(state,(await service.read()).revision);
 const before=(await service.read()).revision;
 await assert.rejects(service.mutate(current=>({state:{...current,works:[{...current.works[0],name:'실패해야 하는 수정'}],libraryEntries:[{id:'invalid'}]},result:{}})),{status:422});
 assert.equal((await service.read()).revision,before);assert.equal((await service.read()).state.works[0].name,state.works[0].name);
 service.close();await writeFile(join(dir,'workspace.sqlite'),'broken database');
 assert.throws(()=>service.read());
}));
test('HTTP capability guard and browser draft recovery reject old full-state writes and repeat safely',async()=>fixture(async(service,dir)=>{
 const base=initialState();await writeFile(join(dir,'workspace.json'),JSON.stringify(base));
 const server=createServer((req,res)=>service.handler(req,res,()=>{res.writeHead(404);res.end()}));await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const origin='http://127.0.0.1:'+server.address().port,headers={'X-Toolkit-Flow':'1','Content-Type':'application/json'};
 try{
  const manifest=await(await fetch(origin+'/api/flow/workspace')).json();assert.equal(manifest.apiVersion,5);assert.equal(manifest.state,undefined);
  const state=structuredClone(base);state.works[0].name='브라우저에서 수정한 작업';
  const request={base,state,idempotencyKey:randomUUID()};
  for(let i=0;i<2;i++)assert.equal((await fetch(origin+'/api/flow/workspace/recover',{method:'POST',headers,body:JSON.stringify(request)})).status,200);
  assert.equal(service.read().state.works[0].name,state.works[0].name);
  for(const [path,method,payload] of [['workspace','PUT',base],['workspace/merge','POST',{base,state}]]){
   assert.equal((await fetch(origin+'/api/flow/'+path,{method,headers,body:JSON.stringify(payload)})).status,426);
  }
  assert.equal(JSON.parse(await readFile(join(dir,'workspace.json'),'utf8')).works[0].name,base.works[0].name);
 }finally{await new Promise(resolve=>server.close(resolve))}
}));

test('process interruption before or after migration publication preserves one complete state',async()=>{
 const {spawnSync}=await import('node:child_process');
 const moduleUrl=new URL('../server/workspace.mjs',import.meta.url).href;
 for(const phase of ['before-link','after-link','before-commit'])await fixture(async(service,dir)=>{
  const base=initialState(),original=JSON.stringify(base);
  await writeFile(join(dir,'workspace.json'),original);
  if(phase==='before-commit'){await service.put(base,(await service.read()).revision);service.close()}
  const workId=base.works[0].id,key=randomUUID();
  const script=`
   import fs from 'node:fs';import {syncBuiltinESMExports} from 'node:module';import {DatabaseSync} from 'node:sqlite';
   const [dir,phase,workId,key]=process.argv.slice(1);
   const stop=()=>process.kill(process.pid,'SIGKILL');
   if(phase==='before-commit'){const original=DatabaseSync.prototype.exec;DatabaseSync.prototype.exec=function(sql){if(sql==='COMMIT')stop();return original.call(this,sql)}}
   else{const original=fs.linkSync;fs.linkSync=(...args)=>{if(phase==='before-link')stop();const value=original(...args);stop();return value};syncBuiltinESMExports()}
   const {createWorkspaceService}=await import(${JSON.stringify(moduleUrl)});
   const service=createWorkspaceService(dir,{workspaceId:'workspace'});
   await service.domain.workUpdate({workspaceId:'workspace',workId,expectedRevision:0,name:'중단 후 확인',idempotencyKey:key});
  `;
  const child=spawnSync(process.execPath,['--input-type=module','-e',script,dir,phase,workId,key],{encoding:'utf8',timeout:15000});
  assert.equal(child.signal,'SIGKILL',child.stderr);
  assert.equal(await readFile(join(dir,'workspace.json'),'utf8'),original);
  const restored=await service.read();
  assert.equal(restored.state.works[0].name,phase==='after-link'?'중단 후 확인':base.works[0].name);
  await service.domain.workUpdate({workspaceId:'workspace',workId,expectedRevision:0,name:'중단 후 확인',idempotencyKey:key});
  assert.equal((await service.read()).state.works[0].revision,1);
  const db=new DatabaseSync(join(dir,'workspace.sqlite'));assert.equal(db.prepare('PRAGMA integrity_check').get().integrity_check,'ok');db.close();
 });
});
test('independent artifacts can change concurrently while a stale revision is rejected',async()=>fixture(async({domain,reads})=>{
 const left=await domain.workCreate({workspaceId:'workspace',name:'자료 A',artifact:html('a',0,1024),idempotencyKey:randomUUID()});
 const right=await domain.workCreate({workspaceId:'workspace',name:'자료 B',artifact:html('b',0,1024),idempotencyKey:randomUUID()});
 const requests=[left,right].map(({work})=>({workspaceId:'workspace',workId:work.id,artifactId:work.artifacts[0].id,baseRevision:0,mode:'replace',artifact:html(work.artifacts[0].id,1,2048),idempotencyKey:randomUUID()}));
 const results=await Promise.all(requests.map(value=>domain.changeSubmit(value)));
 assert.ok(results.every(value=>value.change.appliedRevision===1));
 for(const value of requests)assert.equal(reads.workRead(value).work.artifacts[0].revision,1);
 await assert.rejects(domain.changeSubmit({...requests[0],idempotencyKey:randomUUID()}),{status:409});
}));
