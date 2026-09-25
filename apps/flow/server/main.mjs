import {createServer} from 'node:http';
import {readFile,stat} from 'node:fs/promises';
import {join,resolve,relative,extname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {hostname} from 'node:os';
import {createWorkspaceService,assertWorkHost} from './workspace.mjs';

const root=resolve(fileURLToPath(new URL('..',import.meta.url)));
const workspaceRoot=process.env.TOOLKIT_FLOW_WORKSPACE_ROOT;
const executionHost=process.env.TOOLKIT_FLOW_EXEC_HOST;
assertWorkHost({root,host:executionHost,workspaceRoot},{platform:process.platform,hostname:hostname()});
const service=createWorkspaceService(process.env.TOOLKIT_FLOW_DATA_DIR||join(root,'.data'),{workspaceRoot,workspaceId:process.env.TOOLKIT_FLOW_WORKSPACE_ID||'workspace',displayName:process.env.TOOLKIT_FLOW_DISPLAY_NAME||'작업공간',publicUrl:process.env.TOOLKIT_FLOW_PUBLIC_URL||'',toolkitUrl:process.env.TOOLKIT_FLOW_TOOLKIT_URL||''});
const client=join(root,'dist/client');
const types={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.svg':'image/svg+xml','.png':'image/png','.jpg':'image/jpeg','.webp':'image/webp','.gif':'image/gif','.ico':'image/x-icon','.csv':'text/csv; charset=utf-8','.pdf':'application/pdf','.mp4':'video/mp4','.webm':'video/webm','.mp3':'audio/mpeg','.wav':'audio/wav','.ogg':'audio/ogg'};
const server=createServer(async(req,res)=>{
 if(req.url?.startsWith('/api/flow/'))return service.handler(req,res,()=>{res.writeHead(404);res.end()});
 try{
  const url=new URL(req.url,'http://localhost'),decoded=decodeURIComponent(url.pathname);
  if(decoded.includes('\\')||decoded.split('/').includes('..')){res.writeHead(403);return res.end()}
  const requested=resolve(client,'.'+decoded),rel=relative(client,requested);
  if(rel==='..'||rel.startsWith('../')||rel.startsWith('/')){res.writeHead(403);return res.end()}
  let target=requested,info=await stat(target).catch(()=>null);
  if(!info?.isFile()){target=join(client,'index.html');info=await stat(target)}
  const bytes=await readFile(target);
  res.writeHead(200,{'Content-Type':types[extname(target)]||'application/octet-stream','Content-Length':bytes.length,'Cache-Control':target.endsWith('/index.html')?'no-cache':'private, max-age=3600','X-Content-Type-Options':'nosniff'});
  res.end(req.method==='HEAD'?undefined:bytes);
 }catch{res.writeHead(500);res.end('Toolkit 화면을 열지 못했습니다.')}
});
const port=Number(process.env.TOOLKIT_FLOW_PORT||4176);
server.listen(port,'127.0.0.1',()=>console.log('Toolkit Flow listening on 127.0.0.1:'+port));
