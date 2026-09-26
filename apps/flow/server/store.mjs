import {DatabaseSync} from 'node:sqlite';
import {existsSync,readFileSync,mkdirSync,linkSync,unlinkSync,openSync,closeSync,fsyncSync,chmodSync} from 'node:fs';
import {join} from 'node:path';
import {createHash,randomUUID} from 'node:crypto';
import {migrateState,validState,validArtifact} from '../src/model.js';
import {validLibraryEntry} from '../src/library.js';

const hash=value=>createHash('sha256').update(value).digest('hex');
const fail=(status,message)=>Object.assign(new Error(message),{status});
const canonical=value=>Array.isArray(value)?value.map(canonical):value&&typeof value==='object'?Object.fromEntries(Object.keys(value).sort().map(k=>[k,canonical(value[k])])):value;
const equal=(a,b)=>JSON.stringify(canonical(a))===JSON.stringify(canonical(b));
export const artifactSummary=({id,kind,title,revision,format})=>({id,kind,title,revision,...(format?{format}:{})});
export const changeSummary=c=>Object.fromEntries(Object.entries(c).filter(([key])=>!['idempotencyKey','inputHash','before','proposal'].includes(key)));
const collections=['works','libraryEntries','librarySnapshots','changes','linkedFiles','collectedFiles','files'];
const payloadKeys=new WeakMap();

function schema(db){
 db.exec(`PRAGMA foreign_keys=ON; PRAGMA journal_mode=DELETE; PRAGMA synchronous=FULL; PRAGMA busy_timeout=5000;
 CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
 CREATE TABLE payloads(hash TEXT PRIMARY KEY,json TEXT NOT NULL);
 CREATE TABLE entities(kind TEXT NOT NULL,id TEXT NOT NULL,position INTEGER NOT NULL,data TEXT NOT NULL,PRIMARY KEY(kind,id));
 CREATE TABLE artifact_versions(artifact_id TEXT NOT NULL,revision INTEGER NOT NULL,header TEXT NOT NULL,payload_hash TEXT NOT NULL REFERENCES payloads(hash),PRIMARY KEY(artifact_id,revision));
 CREATE INDEX entity_order ON entities(kind,position);
 PRAGMA user_version=5;`);
}
function putPayload(db,value){
 const json=JSON.stringify(canonical(value)),key=hash(json);
 db.prepare('INSERT OR IGNORE INTO payloads VALUES(?,?)').run(key,json);
 return key;
}
function getPayload(db,key){
 const value=db.prepare('SELECT json FROM payloads WHERE hash=?').get(key);
 if(!value)throw fail(500,'저장된 본문을 찾지 못했습니다.');
 return JSON.parse(value.json);
}
function encodeArtifact(db,artifact,committed=false){
 let ref=payloadKeys.get(artifact);
 if(!ref){
  if(!validArtifact(artifact))throw fail(422,'작업물 형식이 올바르지 않습니다.');
  const {id,revision,...body}=artifact;
  ref={...artifactSummary(artifact),payloadHash:putPayload(db,body),keys:Object.keys(body)};
 }
 if(committed){
  const old=db.prepare('SELECT payload_hash FROM artifact_versions WHERE artifact_id=? AND revision=?').get(ref.id,ref.revision);
  if(old&&old.payload_hash!==ref.payloadHash)throw fail(409,'같은 작업물 버전에 다른 내용이 있습니다.');
  db.prepare('INSERT OR IGNORE INTO artifact_versions VALUES(?,?,?,?)').run(ref.id,ref.revision,JSON.stringify(ref),ref.payloadHash);
 }
 return ref;
}
function decodeArtifact(db,ref){
 const value={id:ref.id,revision:ref.revision,kind:ref.kind,title:ref.title,...(ref.format?{format:ref.format}:{})};
 let body;
 for(const key of ref.keys||[])if(!(key in value))Object.defineProperty(value,key,{enumerable:true,get(){body??=getPayload(db,ref.payloadHash);return body[key]}});
 payloadKeys.set(value,ref);return value;
}
function encodeEntity(db,kind,value){
 if(kind==='works')return {...value,artifacts:value.artifacts.map(a=>encodeArtifact(db,a,true))};
 if(kind==='librarySnapshots')return {...value,artifact:encodeArtifact(db,value.artifact)};
 if(kind==='changes'){
  const {before,proposal,...rest}=value;
  return {...rest,...(before?{beforeRef:encodeArtifact(db,before,true)}:before===null?{before:null}:{}),
   ...(proposal?{proposal:{...proposal,...(proposal.artifact?{artifact:encodeArtifact(db,proposal.artifact)}:{})}}:{})};
 }
 if(kind==='libraryEntries'&&typeof value.body==='string'){const {body,...rest}=value;return {...rest,bodyHash:putPayload(db,body||'')}}
 return value;
}
function decodeEntity(db,kind,row){
 if(kind==='works')return {...row,artifacts:row.artifacts.map(a=>decodeArtifact(db,a))};
 if(kind==='librarySnapshots')return {...row,artifact:decodeArtifact(db,row.artifact)};
 if(kind==='changes'){
  const {beforeRef,proposal,...rest}=row;
  if(beforeRef)Object.defineProperty(rest,'before',{enumerable:true,get:()=>decodeArtifact(db,beforeRef)});
  if(proposal)rest.proposal={...proposal,...(proposal.artifact?{artifact:decodeArtifact(db,proposal.artifact)}:{})};
  return rest;
 }
 if(kind==='libraryEntries'&&row.bodyHash){const {bodyHash,...rest}=row;Object.defineProperty(rest,'body',{enumerable:true,get:()=>getPayload(db,bodyHash)});return rest}
 return row;
}
function load(db){
 const top=db.prepare("SELECT value FROM meta WHERE key='state'").get();
 if(!top)return null;
 const state=JSON.parse(top.value);
 for(const kind of collections){
  const present=state._collections.includes(kind);
  if(present)state[kind]=db.prepare('SELECT data FROM entities WHERE kind=? ORDER BY position').all(kind).map(row=>decodeEntity(db,kind,JSON.parse(row.data)));
 }
 delete state._collections;return state;
}
function write(db,state,previous=null){
 const top={...state,_collections:collections.filter(k=>Array.isArray(state[k]))};
 for(const kind of collections){
  delete top[kind];
  const before=new Map((previous?.[kind]||[]).map(item=>[item.id,item]));
  const current=state[kind]||[];
  for(let i=0;i<current.length;i++){
   const value=current[i];if(typeof value.id!=='string')throw fail(422,'저장 항목의 식별자가 없습니다.');
   if(value!==before.get(value.id)){
    if(kind==='works'&&!validState({version:4,workspaceId:state.workspaceId,theme:'light',activeId:value.id,savedIds:[],libraryEntries:[],works:[value]}))throw fail(422,'작업 형식이 올바르지 않습니다.');
    if(kind==='libraryEntries'&&!validLibraryEntry(value))throw fail(422,'자료 형식이 올바르지 않습니다.');
    const data=encodeEntity(db,kind,value);
    db.prepare('INSERT INTO entities VALUES(?,?,?,?) ON CONFLICT(kind,id) DO UPDATE SET position=excluded.position,data=excluded.data').run(kind,value.id,i,JSON.stringify(data));
   }
   if(value===before.get(value.id))db.prepare('UPDATE entities SET position=? WHERE kind=? AND id=? AND position<>?').run(i,kind,value.id,i);
   before.delete(value.id);
  }
  for(const id of before.keys())db.prepare('DELETE FROM entities WHERE kind=? AND id=?').run(kind,id);
 }
 db.prepare("INSERT INTO meta VALUES('state',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value").run(JSON.stringify(top));
 const sequence=Number(db.prepare("SELECT value FROM meta WHERE key='sequence'").get()?.value||0)+1;
 db.prepare("INSERT INTO meta VALUES('sequence',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value").run(String(sequence));
}
function validateLegacy(state){
 if(!validState(state))throw fail(422,'기존 저장 내용을 확인해 주세요.');
 const ids=new Set();
 for(const c of state.changes||[]){
  if(!c||typeof c.id!=='string'||ids.has(c.id)||c.before&&!validArtifact(c.before)||c.proposal?.artifact&&!validArtifact(c.proposal.artifact))throw fail(422,'기존 변경 기록을 확인해 주세요.');
  ids.add(c.id);
 }
}
export function createFlowStore(directory,workspaceId){
 const path=join(directory,'workspace.sqlite'),legacyPath=join(directory,'workspace.json');
 let db,queue=Promise.resolve();
 const serialized=fn=>{const next=queue.then(fn);queue=next.catch(()=>{});return next};
 function active(){
  if(!db&&existsSync(path)){
   db=new DatabaseSync(path);
   if(db.prepare('PRAGMA user_version').get().user_version!==5){db.close();db=null;throw fail(503,'저장 서비스의 버전을 확인해 주세요.')}
   db.exec('PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000; PRAGMA synchronous=FULL;');
  }
  return db;
 }
 function legacy(){
  if(!existsSync(legacyPath))return {revision:'empty',state:null};
  const bytes=readFileSync(legacyPath),state=migrateState(JSON.parse(bytes),workspaceId);
  if(state.workspaceId!==workspaceId)throw fail(500,'작업공간 식별자가 일치하지 않습니다.');
  validateLegacy(state);return {revision:hash(bytes),state};
 }
 function revision(){
  return hash(workspaceId+':v5:'+active().prepare("SELECT value FROM meta WHERE key='sequence'").get().value);
 }
 function read(){return active()?{state:load(db),revision:revision()}:legacy()}
 function install(previous,next,legacyRevision){
  mkdirSync(directory,{recursive:true,mode:0o700});
  const temp=path+'.'+randomUUID()+'.tmp';let candidate;
  try{
   candidate=new DatabaseSync(temp);chmodSync(temp,0o600);schema(candidate);candidate.exec('BEGIN IMMEDIATE');
   if(previous){
    write(candidate,previous);
    if(!equal(load(candidate),previous))throw fail(500,'기존 자료를 새 저장 형식과 대조하지 못했습니다.');
    candidate.prepare("INSERT INTO meta VALUES('legacyRevision',?)").run(legacyRevision);
   }
   if(next!==previous)write(candidate,next,previous);
   candidate.exec('COMMIT');
   if(candidate.prepare('PRAGMA integrity_check').get().integrity_check!=='ok')throw fail(500,'저장 내용을 확인하지 못했습니다.');
   candidate.close();candidate=null;
   if(legacy().revision!==legacyRevision)throw fail(409,'이행 중 기존 저장본이 변경되었습니다.');
   try{linkSync(temp,path)}catch(e){if(e.code==='EEXIST')throw fail(409,'다른 연결에서 저장을 마쳤습니다. 다시 확인해 주세요.');throw e}
   const fd=openSync(directory,'r');try{fsyncSync(fd)}finally{closeSync(fd)}
  }finally{if(candidate)candidate.close();if(existsSync(temp))unlinkSync(temp);if(existsSync(temp+'-journal'))unlinkSync(temp+'-journal')}
  active();
 }
 async function mutate(fn){return serialized(()=>{
  if(!active()){
   const current=legacy(),update=fn(current.state);
   if(update.state!==current.state)install(current.state,update.state,current.revision);
   return update.result;
  }
  db.exec('BEGIN IMMEDIATE');
  try{
   const state=load(db),update=fn(state);
   if(update.state!==state)write(db,update.state,state);
   db.exec('COMMIT');return update.result;
  }catch(e){db.exec('ROLLBACK');throw e}
 })}
 async function put(state,expected){return serialized(()=>{
  if(!validState(state)||state.workspaceId!==workspaceId)throw fail(422,'작업 형식이 올바르지 않습니다.');
  const current=read();if(current.revision!==expected)throw fail(409,'다른 화면에서 작업이 변경되었습니다.');
  if(!active())install(current.state,state,current.revision);
  else{db.exec('BEGIN IMMEDIATE');try{if(revision()!==expected)throw fail(409,'다른 화면에서 작업이 변경되었습니다.');write(db,state,load(db));db.exec('COMMIT')}catch(e){db.exec('ROLLBACK');throw e}}
  return {revision:revision()};
 })}
 function artifact({artifactId,revision:requested,sourceId,changeId,part}){
  if(active()){
   if(sourceId){const row=db.prepare("SELECT data FROM entities WHERE kind='librarySnapshots' AND id=?").get(sourceId);if(row)return decodeArtifact(db,JSON.parse(row.data).artifact)}
   if(changeId){const row=db.prepare("SELECT data FROM entities WHERE kind='changes' AND id=?").get(changeId);const c=row&&JSON.parse(row.data),ref=part==='before'?c?.beforeRef:c?.proposal?.artifact;if(ref)return decodeArtifact(db,ref)}
   if(artifactId&&Number.isSafeInteger(requested)){
    const row=db.prepare('SELECT header FROM artifact_versions WHERE artifact_id=? AND revision=?').get(artifactId,requested);
    if(row)return decodeArtifact(db,JSON.parse(row.header));
   }
  }
  const {state}=read();
  const value=sourceId?(state?.librarySnapshots?.find(s=>s.id===sourceId)?.artifact||(state?.savedIds.includes(sourceId)?state.works.flatMap(w=>w.artifacts).find(a=>a.id===sourceId):undefined)):changeId?(part==='before'?state?.changes?.find(c=>c.id===changeId)?.before:state?.changes?.find(c=>c.id===changeId)?.proposal?.artifact):state?.works.flatMap(w=>w.artifacts).find(a=>a.id===artifactId&&(requested===undefined||a.revision===requested));
  if(!value)throw fail(404,'작업물 버전을 찾지 못했습니다.');return value;
 }
 function close(){db?.close();db=null}
 return {path,read,put,mutate,artifact,close,isMigrated:()=>Boolean(active())};
}

export function chunkArtifact(artifact,{offset=0,limit=65536,version}={}){
 if(!Number.isSafeInteger(offset)||offset<0||!Number.isSafeInteger(limit)||limit<1||limit>1048576)throw fail(422,'본문 조회 범위를 확인해 주세요.');
 const text=JSON.stringify(artifact),bytes=Buffer.from(text),token=hash(bytes);
 if(version&&version!==token)throw fail(409,'작업물 버전이 바뀌었습니다. 처음부터 다시 읽어 주세요.');
 if(offset>bytes.length||offset&&offset<bytes.length&&(bytes[offset]&192)===128)throw fail(422,'본문 조회 위치를 확인해 주세요.');
 let end=Math.min(offset+limit,bytes.length);while(end<bytes.length&&(bytes[end]&192)===128)end--;
 if(end===offset&&end<bytes.length)throw fail(422,'한 글자를 읽을 수 있는 크기를 지정해 주세요.');
 return {artifact:artifactSummary(artifact),encoding:'utf-8',version:token,offset,totalBytes:bytes.length,content:bytes.subarray(offset,end).toString('utf8'),nextOffset:end<bytes.length?end:null};
}
export function compactWork(work){return {...work,artifacts:work.artifacts.map(artifactSummary)}}
export function compactChange(change){
 const {before,proposal,...header}=change;
 return {...header,...(before?{before:artifactSummary(before)}:{}),...(proposal?{proposal:{...proposal,...(proposal.artifact?{artifact:artifactSummary(proposal.artifact)}:{})}}:{})};
}
export function compactResult(result){
 return {...result,...(result.work?{work:compactWork(result.work)}:{}),...(result.change?{change:compactChange(result.change)}:{})};
}
