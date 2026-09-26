import {createHash} from 'node:crypto';
import {spawn} from 'node:child_process';
import {mkdir,readFile,writeFile,rename,rm,readdir,realpath,stat} from 'node:fs/promises';
import {join,relative,resolve} from 'node:path';
import {createFlowDomain} from './domain.mjs';
import {createFlowStore,compactResult} from './store.mjs';
import {createFlowReads} from './reads.mjs';
import {hostname} from 'node:os';
import {validState,migrateState} from '../src/model.js';
import {mergeStates} from '../src/merge.js';
import {workspaceMediaType} from '../src/work-surface/composition-editor.js';

const MAX_STATE=10*1024*1024,MAX_IMAGE=64*1024*1024;
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
const fault=(status,message)=>Object.assign(new Error(message),{status});
async function parseJson(req,limit){try{return JSON.parse(await body(req,limit))}catch(e){if(e.status)throw e;throw fault(400,'요청 내용을 읽지 못했습니다.')}}
async function body(req,limit){
 const parts=[];let size=0;
 for await(const part of req){size+=part.length;if(size>limit)throw fault(413,'파일이 너무 큽니다.');parts.push(part)}
 return Buffer.concat(parts);
}
function runPdfTool(command,args,bytes,maxOutput,timeoutMs){
 return new Promise((resolve,reject)=>{
  const child=spawn(command,args,{stdio:['pipe','pipe','pipe']});
  const parts=[];let size=0,limitError=null;
  const timer=setTimeout(()=>{limitError=fault(503,'PDF를 여는 시간이 초과되었습니다.');child.kill('SIGKILL')},timeoutMs);
  child.on('error',()=>{clearTimeout(timer);reject(fault(503,'PDF 미리보기를 사용할 수 없습니다.'))});
  child.stdout.on('data',part=>{size+=part.length;if(size>maxOutput){limitError=fault(413,'PDF 미리보기 이미지가 너무 큽니다.');child.kill('SIGKILL')}else parts.push(part)});
  child.stderr.resume();
  child.stdin.on('error',()=>{});
  child.on('close',code=>{clearTimeout(timer);if(limitError)return reject(limitError);if(code!==0||!parts.length)return reject(fault(415,'PDF 내용을 읽지 못했습니다.'));resolve(Buffer.concat(parts))});
  child.stdin.end(bytes);
 });
}
async function pdfPageCount(bytes){
 const output=await runPdfTool('pdfinfo',['-'],bytes,64*1024,3000);
 const count=Number(/^Pages:\s+(\d+)\s*$/m.exec(output.toString())?.[1]);
 if(!Number.isInteger(count)||count<1||count>10000)throw fault(415,'PDF 쪽 수를 확인하지 못했습니다.');
 return count;
}
function renderPdfPage(bytes,page){
 return runPdfTool('pdftoppm',['-f',String(page),'-l',String(page),'-singlefile','-png','-scale-to','1200','-'],bytes,8*1024*1024,7000);
}
function imageType(bytes,mime){
 if(bytes.subarray(0,8).equals(Buffer.from([137,80,78,71,13,10,26,10])))return ['image/png','png'];
 if(bytes[0]===255&&bytes[1]===216&&bytes[2]===255)return ['image/jpeg','jpg'];
 if(['GIF87a','GIF89a'].includes(bytes.subarray(0,6).toString()))return ['image/gif','gif'];
 if(bytes.subarray(0,4).toString()==='RIFF'&&bytes.subarray(8,12).toString()==='WEBP')return ['image/webp','webp'];
 const first=bytes.subarray(0,12),kind={
  'audio/mpeg':['mp3',first.subarray(0,3).toString()==='ID3'||bytes[0]===255&&(bytes[1]&224)===224],
  'audio/wav':['wav',first.subarray(0,4).toString()==='RIFF'&&first.subarray(8,12).toString()==='WAVE'],
  'audio/ogg':['ogg',first.subarray(0,4).toString()==='OggS'],
  'video/mp4':['mp4',first.subarray(4,8).toString()==='ftyp'],
  'audio/mp4':['m4a',first.subarray(4,8).toString()==='ftyp'],
  'audio/aac':['aac',bytes[0]===255&&(bytes[1]&246)===240],
  'video/webm':['webm',first.subarray(0,4).equals(Buffer.from([26,69,223,163]))],
  'application/pdf':['pdf',first.subarray(0,5).toString()==='%PDF-']
 }[mime];
 if(kind?.[1])return [mime,kind[0]];
 throw fault(415,'지원하지 않거나 파일 내용과 형식이 일치하지 않습니다.');
}
export function createWorkspaceService(directory,{workspaceRoot=null,workspaceId='workspace',displayName='작업공간',publicUrl='',toolkitUrl='',pdfInspector=pdfPageCount,pdfRenderer=renderPdfPage}={}){
 const assets=join(directory,'assets');
 const store=createFlowStore(directory,workspaceId);
 const {read,put,mutate}=store;
 const reads=createFlowReads(store,{workspaceId,displayName,publicUrl,toolkitUrl});
 const forbidden=new Set(['.git','.data','.ssh','.codex','node_modules']);
 function checkedParts(path){
  if(typeof path!=='string'||path.startsWith('/')||path.includes('\\')||/[\u0000-\u001f]/.test(path))throw fault(400,'경로 형식이 올바르지 않습니다.');
  const parts=path?path.split('/'):[];
  if(parts.some(part=>!part||part.startsWith('.')||part==='..'||forbidden.has(part)||/^\.env(?:\.|$)/.test(part)||/\.(?:pem|key|p12|pfx|crt)$/.test(part)))throw fault(403,'이 경로는 열 수 없습니다.');
  return parts;
 }
 async function safeFile(path){
  if(!workspaceRoot)throw fault(503,'작업공간이 연결되지 않았습니다.');
  const root=await realpath(workspaceRoot),target=await realpath(resolve(root,...checkedParts(path))).catch(e=>{if(e.code==='ENOENT')throw fault(404,'파일을 찾지 못했습니다.');throw e});
  const rel=relative(root,target);
  if(rel==='..'||rel.startsWith('../')||rel.startsWith('/'))throw fault(403,'작업공간 밖의 경로는 열 수 없습니다.');
  if(rel.split('/').some(part=>forbidden.has(part)||/^\.env(?:\.|$)/.test(part)||/\.(?:pem|key|p12|pfx|crt)$/.test(part)))throw fault(403,'이 경로는 열 수 없습니다.');
  return {root,target,rel};
 }
 async function listFiles(path){
  const {target,rel}=await safeFile(path),info=await stat(target);
  if(!info.isDirectory())throw fault(400,'폴더가 아닙니다.');
  const entries=await readdir(target,{withFileTypes:true});
  const items=entries.filter(item=>!item.isSymbolicLink()&&!item.name.startsWith('.')&&!forbidden.has(item.name)&&!/^\.env(?:\.|$)/.test(item.name)&&!/\.(?:pem|key|p12|pfx|crt)$/.test(item.name)).slice(0,2000).map(item=>({name:item.name,path:[rel,item.name].filter(Boolean).join('/'),type:item.isDirectory()?'directory':'file'}));
  return {workspaceId,path:rel,items,truncated:entries.length>2000};
 }
 async function fileContent(path){
  const {target,rel}=await safeFile(path),info=await stat(target);
  if(!info.isFile())throw fault(400,'파일이 아닙니다.');
  if(info.size>20*1024*1024)throw fault(413,'미리볼 수 있는 크기를 넘었습니다.');
  const ext=rel.split('.').at(-1).toLowerCase();
  const type={png:'image/png',jpg:'image/jpeg',jpeg:'image/jpeg',webp:'image/webp',gif:'image/gif'}[ext];
  if(type){const bytes=await readFile(target),[actual]=imageType(bytes);if(actual!==type)throw fault(415,'파일 형식과 내용이 다릅니다.');return {type,bytes,path:rel}}
  const media=workspaceMediaType(rel);
  if(media){
   const bytes=await readFile(target);
   const valid=ext==='mp3'?(bytes.subarray(0,3).toString()==='ID3'||bytes[0]===255&&(bytes[1]&224)===224):
    ext==='wav'?(bytes.subarray(0,4).toString()==='RIFF'&&bytes.subarray(8,12).toString()==='WAVE'):
    ext==='ogg'?bytes.subarray(0,4).toString()==='OggS':
    ext==='mp4'||ext==='m4a'?bytes.subarray(4,8).toString()==='ftyp':
    ext==='aac'?bytes.length>=2&&bytes[0]===255&&(bytes[1]&246)===240:
    bytes.subarray(0,4).equals(Buffer.from([26,69,223,163]));
   if(!valid)throw fault(415,'파일 형식과 내용이 다릅니다.');
   return {type:media.mime,bytes,path:rel};
  }
  if(ext==='pdf'){
   const bytes=await readFile(target);
   if(bytes.subarray(0,5).toString()!=='%PDF-')throw fault(415,'파일 형식과 내용이 다릅니다.');
   return {type:'application/pdf',bytes,path:rel};
  }
  if(!['txt','md','markdown','csv','tsv','json','yaml','yml','html','htm','css','js','jsx','ts','tsx','mjs','svg','xml'].includes(ext))throw fault(415,'이 형식은 미리보기를 지원하지 않습니다.');
  if(info.size>512*1024)throw fault(413,'텍스트 미리보기는 512KB 이하만 지원합니다.');
  const bytes=await readFile(target);const content=new TextDecoder('utf-8',{fatal:true}).decode(bytes);
  return {type:'text/plain',content,path:rel};
 }
 const assetExists=async src=>{const match=typeof src==='string'&&src.match(/^\/api\/flow\/assets\/([a-f0-9]{64}\.(?:png|jpg|webp|gif|mp3|wav|ogg|mp4|webm|m4a|aac|pdf))$/);return !!match&&(await stat(join(assets,match[1])).catch(()=>null))?.isFile()};
 const domain=createFlowDomain({read,mutate,workspaceId,displayName,publicUrl,webLink:reads.link,assetExists});
 async function handler(req,res,next){
  let url;
  try{url=new URL(req.url,'http://localhost')}catch{return next()}
  if(!url.pathname.startsWith('/api/flow/'))return next();
  const json=(status,value)=>{res.writeHead(status,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'});res.end(JSON.stringify(value))};
  try{
   const host=req.headers.host||'',hostname=host.split(':')[0];
   if(!['127.0.0.1','localhost'].includes(hostname))throw fault(403,'허용되지 않은 호스트입니다.');
   if(req.headers.origin&&req.headers.origin!=='http://'+host)throw fault(403,'다른 출처에서는 접근할 수 없습니다.');
   if(req.headers['sec-fetch-site']==='cross-site')throw fault(403,'다른 출처에서는 접근할 수 없습니다.');
   if(!['GET','HEAD'].includes(req.method)&&req.headers['x-toolkit-flow']!=='1')throw fault(403,'요청을 확인할 수 없습니다.');
   if(url.pathname==='/api/flow/workspace/recover'&&req.method==='POST'){
    const data=await parseJson(req,2*MAX_STATE);
    if(!validState(data.base)||!validState(data.state)||data.base.workspaceId!==workspaceId||data.state.workspaceId!==workspaceId)throw fault(422,'복구할 작업 형식을 확인해 주세요.');
    if(typeof data.idempotencyKey!=='string'||data.idempotencyKey.length<8||data.idempotencyKey.length>160)throw fault(422,'복구 요청을 확인해 주세요.');
    const inputHash=hash(Buffer.from(JSON.stringify({base:data.base,state:data.state})));
    const result=await mutate(current=>{
     if(!current)throw fault(409,'비교할 저장본이 없습니다.');
     const prior=current.changes?.find(change=>change.idempotencyKey===data.idempotencyKey);
     if(prior){if(prior.kind!=='browser_recovery'||prior.inputHash!==inputHash)throw fault(409,'다른 복구 요청과 식별자가 겹칩니다.');return {state:current,result:{recovered:true}}}
     const next=mergeStates(data.base,data.state,current);
     next.changes=[...(current.changes||[]),{id:'recovery:'+data.idempotencyKey,kind:'browser_recovery',idempotencyKey:data.idempotencyKey,inputHash,status:'completed'}];
     return {state:next,result:{recovered:true}};
    });
    return json(200,{...result,revision:(await read()).revision});
   }
   if(url.pathname==='/api/flow/workspace/merge'&&req.method==='POST'){
    if(store.isMigrated())throw fault(426,'새 Toolkit 화면에서 작업을 이어가 주세요. 이 브라우저의 변경은 그대로 남아 있습니다.');
    const data=await parseJson(req,2*MAX_STATE);
    if(!validState(data.base)||!validState(data.state))throw fault(422,'병합할 작업 형식이 올바르지 않습니다.');
    return json(200,await mutate(current=>{
     if(!current)throw fault(409,'저장본이 없습니다.');
     const next=mergeStates(data.base,data.state,current);
     if(!validState(next))throw fault(422,'병합한 작업 형식이 올바르지 않습니다.');
     return {state:next,result:{state:next,revision:hash(Buffer.from(JSON.stringify(next)))}};
    }));
   }
   if(url.pathname==='/api/flow/workspace'){
    if(req.method==='GET')return json(200,{api:'toolkit-flow-v5',apiVersion:5,workspaceId,displayName,toolkitUrl,webUrl:reads.link(),migrated:store.isMigrated()});
    if(req.method==='PUT'){
     if(store.isMigrated())throw fault(426,'새 Toolkit 화면에서 작업을 이어가 주세요.');
     let data;try{data=JSON.parse(await body(req,MAX_STATE))}catch(e){if(e.status)throw e;throw fault(400,'저장 내용을 읽을 수 없습니다.')}
     return json(200,await put(data,req.headers['if-match']));
    }
    throw fault(405,'지원하지 않는 요청입니다.');
   }
   if(url.pathname==='/api/flow/workspaces'&&req.method==='GET')return json(200,reads.workspaceList());
   if(url.pathname==='/api/flow/works'&&req.method==='GET')return json(200,reads.workList({workspaceId:url.searchParams.get('workspaceId'),query:url.searchParams.get('query')||'',offset:Number(url.searchParams.get('offset')||0),limit:Number(url.searchParams.get('limit')||50)}));
   if(url.pathname==='/api/flow/works'&&req.method==='POST')return json(201,compactResult(await domain.workCreate(await parseJson(req,MAX_STATE))));
   const workPath=url.pathname.match(/^\/api\/flow\/works\/([^/]+)$/);
   if(workPath){const input={workspaceId:url.searchParams.get('workspaceId')||undefined,workId:decodeURIComponent(workPath[1])};
    if(req.method==='GET')return json(200,reads.workRead({...input,offset:Number(url.searchParams.get('offset')||0),limit:Number(url.searchParams.get('limit')||100)}));
    if(req.method==='PATCH')return json(200,compactResult(await domain.workUpdate({...await parseJson(req,MAX_STATE),workId:input.workId})));
   }
   if(url.pathname==='/api/flow/library'&&req.method==='GET')return json(200,reads.libraryList({workspaceId:url.searchParams.get('workspaceId'),query:url.searchParams.get('query')||'',workId:url.searchParams.get('workId')||undefined,offset:Number(url.searchParams.get('offset')||0),limit:Number(url.searchParams.get('limit')||50)}));
   if(url.pathname==='/api/flow/library'&&req.method==='POST')return json(200,await domain.libraryUpsert(await parseJson(req,MAX_STATE)));
   const libraryPath=url.pathname.match(/^\/api\/flow\/library\/([^/]+)$/);
   if(libraryPath&&req.method==='GET')return json(200,reads.libraryRead({workspaceId:url.searchParams.get('workspaceId'),entryId:decodeURIComponent(libraryPath[1])}));
   if(url.pathname==='/api/flow/snapshots'&&req.method==='GET')return json(200,await domain.snapshotList({workspaceId:url.searchParams.get('workspaceId'),query:url.searchParams.get('query')||'',offset:Number(url.searchParams.get('offset')||0),limit:Number(url.searchParams.get('limit')||50)}));
   if(url.pathname==='/api/flow/snapshots'&&req.method==='POST')return json(201,await domain.snapshotCreate(await parseJson(req,4096)));
   const snapshotPath=url.pathname.match(/^\/api\/flow\/snapshots\/([^/]+)$/);
   if(snapshotPath&&req.method==='GET')return json(200,{source:reads.libraryRead({workspaceId:url.searchParams.get('workspaceId'),entryId:decodeURIComponent(snapshotPath[1])}).entry});
   if(url.pathname==='/api/flow/artifact'&&req.method==='GET')return json(200,reads.artifactRead({workspaceId:url.searchParams.get('workspaceId'),artifactId:url.searchParams.get('artifactId')||undefined,revision:url.searchParams.has('revision')?Number(url.searchParams.get('revision')):undefined,sourceId:url.searchParams.get('sourceId')||undefined,changeId:url.searchParams.get('changeId')||undefined,part:url.searchParams.get('part')||undefined,version:url.searchParams.get('version')||undefined,offset:Number(url.searchParams.get('offset')||0),limit:Number(url.searchParams.get('limit')||65536)}));
   if(url.pathname==='/api/flow/changes'&&req.method==='GET')return json(200,reads.changeList({workspaceId:url.searchParams.get('workspaceId'),workId:url.searchParams.get('workId'),offset:Number(url.searchParams.get('offset')||0),limit:Number(url.searchParams.get('limit')||50),status:url.searchParams.get('status')||undefined}));
   const changeReadPath=url.pathname.match(/^\/api\/flow\/changes\/([^/]+)$/);
   if(changeReadPath&&req.method==='GET')return json(200,reads.changeRead({workspaceId:url.searchParams.get('workspaceId'),changeId:decodeURIComponent(changeReadPath[1])}));
   if(url.pathname==='/api/flow/changes'&&req.method==='POST')return json(200,compactResult(await domain.changeSubmit(await parseJson(req,MAX_STATE))));
   const changePath=url.pathname.match(/^\/api\/flow\/changes\/([^/]+)\/(apply|undo)$/);
   if(changePath&&req.method==='POST')return json(200,compactResult(await domain.changeAction({...await parseJson(req,1024),changeId:decodeURIComponent(changePath[1]),action:changePath[2]})));
   if(url.pathname==='/api/flow/files'&&req.method==='GET'){
    if(url.searchParams.get('workspaceId')!==workspaceId)throw fault(400,'등록된 작업공간을 선택해 주세요.');
    return json(200,await listFiles(url.searchParams.get('path')||''));
   }
   if(url.pathname==='/api/flow/files/preview'&&['GET','HEAD'].includes(req.method)){
    if(url.searchParams.get('workspaceId')!==workspaceId)throw fault(400,'등록된 작업공간을 선택해 주세요.');
    const rawPage=url.searchParams.get('page')||'1';
    if(!/^(?:[1-9]\d{0,3}|10000)$/.test(rawPage))throw fault(400,'쪽 번호가 올바르지 않습니다.');
    const page=Number(rawPage);
    const source=await fileContent(url.searchParams.get('path')||'');
    if(source.type!=='application/pdf')throw fault(415,'PDF 파일을 선택해 주세요.');
    const pages=await pdfInspector(source.bytes);
    if(!Number.isInteger(pages)||pages<1||pages>10000)throw fault(415,'PDF 쪽 수를 확인하지 못했습니다.');
    if(page>pages)throw fault(416,'마지막 쪽을 넘었습니다.');
    const headers={'Content-Type':'image/png','X-Flow-Pdf-Pages':String(pages),'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff','Content-Security-Policy':"default-src 'none'"};
    if(req.method==='HEAD'){res.writeHead(200,headers);return res.end()}
    const bytes=await pdfRenderer(source.bytes,page);
    if(!Buffer.isBuffer(bytes)||!bytes.subarray(0,8).equals(Buffer.from([137,80,78,71,13,10,26,10])))throw fault(500,'PDF 미리보기를 만들지 못했습니다.');
    res.writeHead(200,{...headers,'Content-Length':bytes.length});
    return res.end(bytes);
   }
   if(url.pathname==='/api/flow/files/content'&&['GET','HEAD'].includes(req.method)){
    if(url.searchParams.get('workspaceId')!==workspaceId)throw fault(400,'등록된 작업공간을 선택해 주세요.');
    const result=await fileContent(url.searchParams.get('path')||'');
    if(result.type==='text/plain')return json(200,result);
    const headers={'Content-Type':result.type,'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff','Content-Security-Policy':"default-src 'none'"};
    if((result.type.startsWith('audio/')||result.type.startsWith('video/'))&&req.headers.range){
     const match=/^bytes=(\d*)-(\d*)$/.exec(req.headers.range),size=result.bytes.length;
     let start=match?.[1]?Number(match[1]):0,end=match?.[2]?Number(match[2]):size-1;
     if(match&&!match[1]&&match[2]){start=Math.max(0,size-Number(match[2]));end=size-1}
     if(!match||!Number.isSafeInteger(start)||!Number.isSafeInteger(end)||start<0||start>=size||end<start){res.writeHead(416,{...headers,'Content-Range':'bytes */'+size});return res.end()}
     end=Math.min(end,size-1);
     res.writeHead(206,{...headers,'Accept-Ranges':'bytes','Content-Range':`bytes ${start}-${end}/${size}`,'Content-Length':end-start+1});
     return res.end(req.method==='HEAD'?undefined:result.bytes.subarray(start,end+1));
    }
    res.writeHead(200,{...headers,'Content-Length':result.bytes.length,...(result.type.startsWith('audio/')||result.type.startsWith('video/')?{'Accept-Ranges':'bytes'}:{})});
    return res.end(req.method==='HEAD'?undefined:result.bytes);
   }
   if(url.pathname==='/api/flow/assets'&&req.method==='POST'){
    if(req.headers['x-toolkit-flow-workspace-id']&&req.headers['x-toolkit-flow-workspace-id']!==workspaceId)throw fault(400,'등록된 작업공간을 선택해 주세요.');
    const bytes=await body(req,MAX_IMAGE),[mime,ext]=imageType(bytes,req.headers['content-type']);
    if(req.headers['content-type']!==mime)throw fault(415,'파일 형식과 내용이 다릅니다.');
    await mkdir(assets,{recursive:true,mode:0o700});
    const name=hash(bytes)+'.'+ext;
    try{await writeFile(join(assets,name),bytes,{flag:'wx',mode:0o600})}catch(e){if(e.code!=='EEXIST')throw e}
    return json(201,{src:'/api/flow/assets/'+name,mime,size:bytes.length});
   }
   const match=url.pathname.match(/^\/api\/flow\/assets\/([a-f0-9]{64}\.(png|jpg|webp|gif|mp3|wav|ogg|mp4|webm|m4a|aac|pdf))$/);
   if(match&&['GET','HEAD'].includes(req.method)){
    let bytes;try{bytes=await readFile(join(assets,match[1]))}catch(e){if(e.code==='ENOENT')throw fault(404,'이미지 파일을 찾지 못했습니다.');throw e}
    res.writeHead(200,{'Content-Type':{png:'image/png',jpg:'image/jpeg',webp:'image/webp',gif:'image/gif',mp3:'audio/mpeg',wav:'audio/wav',ogg:'audio/ogg',mp4:'video/mp4',webm:'video/webm',m4a:'audio/mp4',aac:'audio/aac',pdf:'application/pdf'}[match[2]],'Content-Length':bytes.length,'Cache-Control':'private, max-age=3600','X-Content-Type-Options':'nosniff','Content-Security-Policy':"default-src 'none'"});
    return res.end(req.method==='HEAD'?undefined:bytes);
   }
   throw fault(404,'찾을 수 없는 작업 경로입니다.');
  }catch(e){if(!res.headersSent)json(e.status||500,{error:e.status?e.message:'작업공간에 저장하지 못했습니다.'});else res.end()}
 }
 return {handler,read,put,mutate,domain,reads,store,close:store.close};
}
export function assertWorkHost({root,host,workspaceRoot},runtime={platform:process.platform,hostname:hostname()}){
 const path=workspaceRoot&&root?relative(workspaceRoot,root):'..';
 if(!host||runtime.hostname!==host||!workspaceRoot||path==='..'||path.startsWith('../')||path.startsWith('/'))throw new Error('등록된 작업환경에서만 실행할 수 있습니다.');
}

export function workspacePlugin(target){
 return {name:'toolkit-workspace',apply:'serve',configureServer(server){
  assertWorkHost({...target,root:server.config.root});
  const service=createWorkspaceService(process.env.TOOLKIT_FLOW_DATA_DIR||join(server.config.root,'.data'),{workspaceRoot:target.workspaceRoot,workspaceId:process.env.TOOLKIT_FLOW_WORKSPACE_ID||'workspace',displayName:process.env.TOOLKIT_FLOW_DISPLAY_NAME||'작업공간',publicUrl:process.env.TOOLKIT_FLOW_PUBLIC_URL||'',toolkitUrl:process.env.TOOLKIT_FLOW_TOOLKIT_URL||''});
  server.middlewares.use(service.handler);
 }};
}
