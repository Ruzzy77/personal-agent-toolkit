import {z} from "zod";
import type {McpServer} from "@modelcontextprotocol/server";
import {mcpTextResult,mcpTextError} from "@personal-agent/remote-runtime";
import {executeContextOperation} from "./context-api";
import {requireScope} from "./auth";
import {contentSha256} from "./canonical";
import {ContextError,asContextError} from "./errors";
import {flowLinkedResource} from "./host";
import {disabledProducts} from "./toolkit-products";
import {JournalService} from "personal-agent-journal-service/service";
import {LibraryService} from "personal-agent-library-service/service";
import type {Env,Principal} from "./types";

type Row=Record<string,unknown>;
const row=(v:unknown):Row=>v&&typeof v==="object"&&!Array.isArray(v)?v as Row:{};
const rows=(v:unknown):Row[]=>Array.isArray(v)?v.map(row):[];
const text=(v:unknown)=>typeof v==="string"?v:"";
const referenceKey=(value:unknown)=>JSON.stringify(value);
const searchSchema=z.object({query:z.string().trim().min(1).max(200),space_id:z.string().max(64).optional(),cursor:z.string().max(4096).optional(),limit:z.number().int().min(1).max(100).default(50)}).strict();
const readSchema=z.object({reference:flowLinkedResource,expected_version:z.string().max(256).optional(),start_char:z.number().int().nonnegative().default(0),max_chars:z.number().int().min(1).max(100000).default(32768)}).strict();
const positionSchema=z.object({done:z.boolean(),spaceOffset:z.number().int().nonnegative(),contextOffset:z.number().int().nonnegative(),itemOffset:z.number().int().nonnegative()}).strict();
const cursorSchema=z.object({query:z.string(),space:z.string(),provider:z.number().int().min(0).max(3),pages:z.array(positionSchema).length(4)}).strict();
type Cursor=z.infer<typeof cursorSchema>;
type Resource={reference:unknown;title:string;detail:string;sourceVersion?:string};
const sectionTitles:Record<string,string>={"questions-and-choices":"질문과 선택","scope-and-checking":"업무 범위","evidence-and-judgment":"자료와 해석","explanation-and-output":"설명과 산출물 구성","conversation-and-writing":"한국어 생성","what-to-keep":"기억 체계","visual-production":"시각 설계와 제작","research-exploration":"연구 탐색","research-review":"연구 검토"};
const contextRef=(locator:Row)=>({kind:"context",locator});
const encodeCursor=(c:Cursor)=>btoa(JSON.stringify(c).replace(/[\u007f-\uffff]/g,c=>"\\u"+c.charCodeAt(0).toString(16).padStart(4,"0")));
function decodeCursor(value:string|undefined,query:string,space:string):Cursor{
 if(!value)return {query,space,provider:0,pages:Array.from({length:4},()=>({done:false,spaceOffset:0,contextOffset:0,itemOffset:0}))};
 try{const c=cursorSchema.parse(JSON.parse(atob(value)));if(c.query!==query||c.space!==space)throw new Error();return c}
 catch{throw new ContextError("invalid_request","검색을 처음부터 다시 진행해 주세요.",422)}
}
const nextProvider=(c:z.infer<typeof positionSchema>)=>{c.done=true};
export async function flowResourceSearch(env:Env,principal:Principal,raw:unknown){
 const input=searchSchema.parse(raw),state=decodeCursor(input.cursor,input.query,input.space_id||"");
 const disabled=await disabledProducts(env,principal.ownerId);
 const can=(product:"sense"|"corpus"|"journal"|"library")=>!disabled.has(product)&&principal.scopes.has(product+".read");
 const call=async(name:string,args:Row)=>row(await executeContextOperation(env,principal,name,args));
 const found:Resource[]=[],seen=new Set<string>(),failures:string[]=[];
 // Each request has a bounded number of source reads. Empty pages still return a cursor.
 for(let attempts=0;attempts<8&&found.length<input.limit&&state.pages.some(page=>!page.done);attempts++){
  const index=state.provider,cursor=state.pages[index]!;state.provider=(index+1)%4;if(cursor.done)continue;
  const remaining=Math.min(input.limit-found.length,Math.max(1,Math.ceil(input.limit/4)));let batch:Resource[]=[];
  const provider=["sense","corpus","journal","library"][index] as "sense"|"corpus"|"journal"|"library";
  if(!can(provider)){nextProvider(cursor);continue}
  try{
   if(provider==="sense"){
    const data=await call("sense_read",{view:"full",include_skill:true}),match=(value:unknown)=>String(value||"").toLocaleLowerCase().includes(input.query.toLocaleLowerCase());
    batch=rows(row(data.profile).sections).filter(s=>s.sensitivity==="ordinary").flatMap(s=>{
     const id=text(s.id),title=sectionTitles[id]||text(s.purpose)||id,skill=row(s.skill),items:Resource[]=[];
     if(match([title,s.purpose,s.text].join(" ")))items.push({reference:contextRef({product:"sense",sectionId:id}),title,detail:"기준"});
     if(skill.name&&match([skill.name,skill.description,skill.instructions].join(" ")))items.push({reference:contextRef({product:"sense",sectionId:id,skill:true}),title:text(skill.name),detail:"작성 지침"});
     return items;
    });
    const items=batch.slice(cursor.itemOffset,cursor.itemOffset+remaining);cursor.itemOffset+=items.length;
    if(cursor.itemOffset>=batch.length)nextProvider(cursor);batch=items;
   }else if(provider==="corpus"){
    const list=input.space_id?{spaces:[{space_id:input.space_id}],has_more:false}:await call("corpus_space_list",{offset:cursor.spaceOffset,limit:1});
    const space=rows(list.spaces)[0];
    if(!space){nextProvider(cursor);continue}
    const spaceId=text(space.space_id);
    const data=await call("corpus_space_search",{space_id:spaceId,query:input.query,search_scope:"all",context_offset:cursor.contextOffset,limit:100});
    const context=row(data.context);
    batch=rows(context.items).map(item=>({reference:contextRef(item.result_type==="document"?{product:"corpus",spaceId,documentId:text(item.id)}:{product:"context-item",spaceId,itemId:text(item.id)}),title:text(item.title)||(text(item.snippet).split("\n")[0]||"").replace(/^#+\s*/,"").slice(0,90)||text(item.id),detail:text(space.display_name)||"프로젝트 자료",sourceVersion:String(item.version)}));
    if(cursor.contextOffset===0){
     const originals=new Map<string,Row>();
     for(const item of rows(data.candidates)){const key=JSON.stringify([item.connection_id,item.document_id||item.relative_path||item.read_ref]);if(!originals.has(key))originals.set(key,item)}
     batch.push(...[...originals.values()].map(item=>({reference:contextRef({product:"source",spaceId,readRef:text(item.read_ref)}),title:text(item.relative_path)||text(item.title)||"원자료",detail:text(space.display_name)||"원자료",sourceVersion:text(item.revision_id)})));
    }
    const items=batch.slice(cursor.itemOffset,cursor.itemOffset+remaining);cursor.itemOffset+=items.length;
    if(cursor.itemOffset>=batch.length){
     cursor.itemOffset=0;
     if(context.has_more&&Number(context.next_offset)>cursor.contextOffset)cursor.contextOffset=Number(context.next_offset);
     else{cursor.contextOffset=0;if(input.space_id||!list.has_more)nextProvider(cursor);else cursor.spaceOffset=Number(list.next_offset)}
    }
    batch=items;
   }else if(provider==="journal"){
    const result=await new JournalService(env.JOURNAL_DB).findItems({query:input.query,latestOnly:true,weekId:null,startsOn:null,endsOn:null,projectKey:input.space_id||null,lane:null,resolution:null,limit:remaining,offset:cursor.itemOffset});
    batch=result.items.map(item=>({reference:{kind:"journal-item",id:item.id},title:item.title,detail:"기록",sourceVersion:String(item.version)}));
    cursor.itemOffset+=batch.length;if(cursor.itemOffset>=result.count)nextProvider(cursor);
   }else{
    const result=await new LibraryService({DB:env.LIBRARY_DB,MEDIA:env.LIBRARY_MEDIA}).listIssues(null,remaining+1,"active",cursor.itemOffset,input.query);
    batch=result.slice(0,remaining).map(item=>({reference:{kind:"library-issue",id:item.id},title:item.title,detail:"발간물",sourceVersion:String(item.version)}));
    cursor.itemOffset+=batch.length;if(result.length<=remaining)nextProvider(cursor);
   }
   for(const item of batch){const key=referenceKey(item.reference);if(!seen.has(key)){seen.add(key);found.push(item)}}
  }catch{failures.push(provider);nextProvider(cursor)}
 }
 return {items:found,nextCursor:state.pages.some(page=>!page.done)?encodeCursor(state):null,partial:failures.length>0,unavailable:failures};
}
export async function flowResourceRead(env:Env,principal:Principal,raw:unknown){
 const input=readSchema.parse(raw),ref=input.reference,disabled=await disabledProducts(env,principal.ownerId);
 const product=ref.kind==="context"?(ref.locator.product==="sense"?"sense":"corpus"):ref.kind==="journal-item"?"journal":ref.kind==="library-issue"?"library":ref.kind==="user-context"?"hypes":null;
 if(!product)throw new ContextError("invalid_request","이 자료는 해당 원본 조회 경로에서 열어 주세요.",422);
 if(disabled.has(product))throw new ContextError("scope_denied","이 자료의 연결이 꺼져 있습니다.",403);
 requireScope(principal,product+".read");
 const call=async(name:string,args:Row)=>row(await executeContextOperation(env,principal,name,args));
 let title="",version="",body="",format="markdown",detail="자료",href="";
 if(ref.kind==="context"){
  const loc=ref.locator;
  if(loc.product==="sense"){
   const data=await call("sense_read",{view:"sections",section_ids:[loc.sectionId],include_skill:Boolean(loc.skill)}),section=rows(data.sections)[0];
   if(!section)throw new ContextError("not_found","자료를 찾지 못했습니다.",404);
   const skill=row(section.skill);
   title=loc.skill?text(skill.name):sectionTitles[loc.sectionId]||text(section.purpose)||loc.sectionId;
   body=loc.skill?text(skill.instructions):text(section.text);version=loc.skill?text(skill.version):text(section.section_sha256);detail=loc.skill?"작성 지침":"기준";
  }else if(loc.product==="corpus"){
   const data=await call("corpus_document_read",{space_id:loc.spaceId,document_id:loc.documentId,start_char:input.start_char,max_chars:input.max_chars,...(input.expected_version?{expected_version:Number(input.expected_version)}:{})});
   const doc=row(data.document);title=text(doc.title);body=text(doc.body_markdown);version=String(doc.version);
   return {reference:ref,title,detail:"문서",format,version,body,startChar:input.start_char,nextStartChar:data.has_more?Number(data.next_start_char):null};
  }else if(loc.product==="source"){
   const data=await call("corpus_file_read",{space_id:loc.spaceId,read_ref:loc.readRef,source_view:"text",start_char:input.start_char,max_chars:Math.min(input.max_chars,30000)});
   const source=row(data.source);title=text(source.relative_path)||"원자료";version=text(source.revision_id)||loc.readRef;
   if(input.expected_version&&input.expected_version!==version)throw new ContextError("version_conflict","원자료 버전이 바뀌었습니다.",409);
   return {reference:ref,title,detail:"원자료",format:"text",version,body:text(data.untrusted_content),startChar:input.start_char,nextStartChar:data.has_more?Number(data.next_start_char):null};
  }else{
   let offset=0;
   do{
    const data=await call("corpus_space_get",{space_id:loc.spaceId,context_offset:offset,include_context_skill:loc.product==="context-skill",document_limit:1});
    const context=row(row(data.space).context);
    if(loc.product==="context-skill"){const skill=row(context.skill);title=text(skill.name);body=text(skill.instructions);version=text(skill.version);break}
    const item=rows(context.items).find(item=>item.item_id===loc.itemId);
    if(item){body=text(item.body_text);title=(body.split("\n")[0]||"").replace(/^#+\s*/,"").slice(0,90);version=String(context.version);break}
    if(!context.has_more)throw new ContextError("not_found","자료를 찾지 못했습니다.",404);
    offset=Number(context.next_offset);
   }while(true);
  }
 }else if(ref.kind==="journal-item"){
  const record=await new JournalService(env.JOURNAL_DB).getItemDetail(ref.id);title=record.item.title;body=JSON.stringify(record);version=String(record.item.version)+":"+await contentSha256(record);format="journal";detail="기록";href="/journal";
 }else if(ref.kind==="library-issue"){
  const issue=await new LibraryService({DB:env.LIBRARY_DB,MEDIA:env.LIBRARY_MEDIA}).readIssue(ref.id);
  if(!issue)throw new ContextError("not_found","발간물을 찾지 못했습니다.",404);
  title=issue.title;body=issue.sourceHtml;version=String(issue.version);format="html";detail="발간물";href=issue.canonicalPath;
 }else if(ref.kind==="user-context"){
  const context=await call("hypes_read",{seed_refs:[ref.id],limit:20,max_hops:1});
  const subject=[...rows(context.nodes),...rows(context.predicates)].find(node=>node.node_id===ref.id||node.predicate_id===ref.id);
  if(!subject)throw new ContextError("not_found","사용자 맥락을 찾지 못했습니다.",404);
  title=text(subject.name);body=JSON.stringify(context);version=await contentSha256(context);format="user-context";detail="사용자 맥락";
 }
 if(!title)throw new ContextError("not_found","자료를 찾지 못했습니다.",404);
 if(input.expected_version&&input.expected_version!==version)throw new ContextError("version_conflict","자료 버전이 바뀌었습니다. 처음부터 다시 열어 주세요.",409);
 const points=Array.from(body),end=Math.min(points.length,input.start_char+input.max_chars);
 return {reference:ref,title,detail,href,format,version,body:points.slice(input.start_char,end).join(""),startChar:input.start_char,nextStartChar:end<points.length?end:null};
}
export const FLOW_RESOURCE_TOOLS=[
 {name:"flow_resource_search",description:"Find accessible original guidance, project material, records and publications without copying or adopting them. Continue nextCursor; partial indicates unavailable sources.",schema:searchSchema,run:flowResourceSearch},
 {name:"flow_resource_read",description:"Read one original reference without changing it. Continue nextStartChar with expected_version; unavailable historical versions never silently return current content.",schema:readSchema,run:flowResourceRead},
] as const;
export async function executeFlowResource(env:Env,principal:Principal,name:string,input:unknown){
 const tool=FLOW_RESOURCE_TOOLS.find(t=>t.name===name);
 if(!tool)throw new ContextError("not_found","자료 조회 기능을 찾지 못했습니다.",404);
 return tool.run(env,principal,input);
}
export function registerFlowResourceTools(server:McpServer,env:Env,principal:Principal){
 for(const tool of FLOW_RESOURCE_TOOLS)server.registerTool(tool.name,{description:tool.description,inputSchema:tool.schema,outputSchema:z.looseObject({}),annotations:{readOnlyHint:true,destructiveHint:false,idempotentHint:true,openWorldHint:false}},async (input:unknown)=>{
  try{return mcpTextResult({ok:true,result:await tool.run(env,principal,input)})}
  catch(error){const e=asContextError(error);return mcpTextError({ok:false,error:{code:e.code,message:e.message}})}
 });
}
