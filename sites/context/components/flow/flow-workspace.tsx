"use client";
import {useCallback,useEffect,useRef,useState} from "react";
import {Archive,Download,FileText,Info,Link2,MoreHorizontal,Moon,Sun,Undo2} from "lucide-react";
import {Input} from "@openai/apps-sdk-ui/components/Input";
import {Textarea} from "@openai/apps-sdk-ui/components/Textarea";
import {Menu} from "@openai/apps-sdk-ui/components/Menu";
import {Tooltip} from "@openai/apps-sdk-ui/components/Tooltip";
import {SegmentedControl} from "@openai/apps-sdk-ui/components/SegmentedControl";
import {Field,FieldSelect} from "@personal-agent/ui-kit/react";
import {B,AppHeader,Overlay,WorkCanvas,LibraryBrowser,ReferencePanel,ArtifactPreview,ReviewComparison,contentRenderers,exportHtmlArtifact,TextContentView,HtmlContentView} from "@personal-agent/flow-surface";
import {sourceReference,workReferences,disconnectWorkReference} from "@personal-agent/flow-surface/work-references";
import type {WorkReference} from "@personal-agent/flow-surface/work-references";
import {readFlowWork,readFlowMaterial,readFlowArtifact,resourceCall,createFlowMutator} from "../../lib/flow-client";
import type {ResourcePage} from "../../lib/flow-client";
import {hostCall} from "../../lib/host-files";
import {flowContentForWeb,flowMediaUrl} from "../../lib/flow-content";
import type {FlowRead,FlowWork,FlowWorkItem,FlowLinkedResource,FlowSource,FlowChange} from "../../lib/flow-content";
import {FlowResourceView} from "./flow-resource-view";
import type {FlowOpenResource} from "./flow-resource-view";
import {FlowFilePicker} from "./flow-file-picker";
import {FlowResourceBlock} from "./flow-resource-block";
import {flowResourceIdentity,flowResourceKey,hostFileResource} from "../../lib/flow-resources";
import {readLinked} from "../../lib/flow-resource-read";
import {FLOW_LAST_WORK_KEY,flowSelectionFromStorage,selectFlowWorkspace,resourceHandoffFromSearch} from "../../lib/flow-handoff";
import "./flow-workspace.css";

type Workspace={id:string;permission:string;root_id?:string;apiVersion?:number;capabilities?:string[]};
type Material=FlowSource&{external?:boolean;original?:FlowOpenResource;revision?:number;scope?:{kind:"work"|"workspace"|"personal";workId?:string};reference?:FlowLinkedResource;sourceVersion?:string};
type Screen="work"|"library"|"files";
function MaterialContent({item,workspaceId,root,onSave,onRetry}:{item:Material;workspaceId:string;root?:string;onRetry?:()=>void;onSave?:(item:Material,patch:{title:string;body:string})=>Promise<void>}){
 const draftKey="flow-library-draft:"+workspaceId+":"+item.id;
 const [draft]=useState(()=>{try{const raw=sessionStorage.getItem(draftKey);return raw?JSON.parse(raw) as {title:string;body:string;base:Material}:null}catch{return null}});
 const [original,setOriginal]=useState<{key:string;resource?:FlowOpenResource;error?:string}>({key:""}),[error,setError]=useState(""),[editing,setEditing]=useState(Boolean(draft)),[busy,setBusy]=useState(false),[title,setTitle]=useState(draft?.title??item.title),[body,setBody]=useState(draft?.body??item.body??"");
 const [readAttempt,setReadAttempt]=useState(0),[showOriginal,setShowOriginal]=useState(!item.body);
 const referenceKey=JSON.stringify(sourceReference(item,root));
 const resource=item.original||(original.key===referenceKey?original.resource:null);
 const originalError=original.key===referenceKey?original.error:null;
 const baseline=useRef(draft?.base||item);
 useEffect(()=>{
  if(!editing)return;
  try{sessionStorage.setItem(draftKey,JSON.stringify({title,body,base:baseline.current}))}catch{queueMicrotask(()=>setError("브라우저에 수정 내용을 보관하지 못했습니다. 화면을 닫기 전에 저장해 주세요."))}
  const protect=(event:BeforeUnloadEvent)=>{event.preventDefault();event.returnValue=""};
  window.addEventListener("beforeunload",protect);return()=>window.removeEventListener("beforeunload",protect);
 },[editing,title,body,draftKey]);
 useEffect(()=>{
  const reference=JSON.parse(referenceKey) as FlowLinkedResource|null;
  if(!reference||!showOriginal||item.original)return;
  const controller=new AbortController();
  void readLinked(reference,controller.signal).then(value=>{if(!controller.signal.aborted)setOriginal({key:referenceKey,resource:value})}).catch(e=>{if(!controller.signal.aborted)setOriginal({key:referenceKey,error:e instanceof Error?e.message:"원본을 열지 못했습니다."})});
  return()=>controller.abort();
 },[referenceKey,readAttempt,showOriginal,item.original]);
 async function save(){if(!onSave)return;setBusy(true);setError("");try{await onSave(baseline.current,{title:title.trim(),body});sessionStorage.removeItem(draftKey);setEditing(false)}catch(e){setError(e instanceof Error?e.message:"자료를 저장하지 못했습니다.")}finally{setBusy(false)}}
 function cancel(){sessionStorage.removeItem(draftKey);setEditing(false);setError("");setTitle(item.title);setBody(item.body||"")}
 if(editing)return <form className="su-stack" onSubmit={e=>{e.preventDefault();void save()}}>
  <Field label="이름"><Input value={title} onChange={e=>setTitle(e.target.value)} maxLength={160}/></Field>
  <Field label="내용"><Textarea value={body} onChange={e=>setBody(e.target.value)} rows={12}/></Field>
  {error&&<p role="alert">{error}</p>}<div className="su-row"><B type="submit" variant="solid" disabled={busy||!title.trim()||!body.trim()&&!item.reference}>저장</B><B variant="ghost" onClick={cancel} disabled={busy}>취소</B></div>
 </form>;
 return <div className="su-stack" data-gap="section">
  {!item.artifact&&item.body&&(item.filePath&&!sourceReference(item,root)
   ?/\.html?$/i.test(item.filePath)?<HtmlContentView body={item.body} name={item.title}/>:<TextContentView body={item.body} path={item.filePath} title={item.title} headingLevel={2}/>
   :<div className="flow-material-text">{item.body}</div>)}
  {item.artifact&&<ArtifactPreview artifact={item.artifact} renderers={contentRenderers} prepareContent={c=>flowContentForWeb(workspaceId,c)} resolveMediaUrl={v=>flowMediaUrl(workspaceId,v)} headingLevel={2}/>}
  {item.body&&referenceKey!=="null"&&<details open={showOriginal} onToggle={event=>setShowOriginal(event.currentTarget.open)}><summary>원본</summary>{resource&&<FlowResourceView resource={resource}/>}</details>}
  {!item.body&&resource&&<FlowResourceView resource={resource} hideTitle={resource.title===item.title}/>}
  {resource&&item.sourceVersion&&resource.kind!=="flow-source"&&resource.resolved?.version&&item.sourceVersion!==resource.resolved.version&&<p>정리할 때 참고한 원본과 현재 원본의 버전이 다릅니다.</p>}
  {error&&<p role="alert">{error}</p>}
  {originalError&&<div className="su-stack"><p role="alert">{originalError}</p><div><B variant="ghost" onClick={()=>{onRetry?.();setReadAttempt(value=>value+1)}}>다시 열기</B></div></div>}
  {showOriginal&&referenceKey!=="null"&&!resource&&!originalError&&<p role="status">원본을 여는 중입니다.</p>}
  {item.scope&&onSave&&<div><B variant="ghost" onClick={()=>{baseline.current=item;setTitle(item.title);setBody(item.body||"");setEditing(true)}}>자료 수정</B></div>}
 </div>;
}
export function FlowWorkspace(){
 const [spaces,setSpaces]=useState<Workspace[]>([]),[space,setSpace]=useState(""),[works,setWorks]=useState<FlowWorkItem[]>([]),[detail,setDetail]=useState<FlowRead|null>(null);
 const [screen,setScreen]=useState<Screen>("work"),[panel,setPanel]=useState<string|null>(null),[artifactId,setArtifactId]=useState<string>(),[materials,setMaterials]=useState<Material[]>([]),[materialOffset,setMaterialOffset]=useState<number|null>(null),[libraryError,setLibraryError]=useState("");
 const [loading,setLoading]=useState(true),[busy,setBusy]=useState(false),[error,setError]=useState(""),[message,setMessage]=useState(""),[name,setName]=useState(""),[source,setSource]=useState<Material|null>(null);
 const [resource,setResource]=useState<FlowOpenResource|null>(null),[file,setFile]=useState<FlowOpenResource|null>(null),[theme,setTheme]=useState("light");
 const [draftName,setDraftName]=useState(""),[draftPurpose,setDraftPurpose]=useState("");
 const returnRef=useRef<HTMLElement|null>(null),sourceTrigger=useRef<HTMLButtonElement>(null),moreTrigger=useRef<HTMLButtonElement>(null),request=useRef(0),selected=useRef(""),revision=useRef(""),busyRef=useRef(false),panelRef=useRef<string|null>(null),scroll=useRef<Record<string,number>>({});
 const mutate=useRef(createFlowMutator(hostCall)).current,selectedArtifact=useRef<string|undefined>(undefined);
 const createAttempt=useRef<{signature:string;key:string}|null>(null);
 const work=detail?.work,connection=spaces.find(s=>s.id===space),writable=connection?.permission==="read_write"&&connection.apiVersion===5;
 const root=spaces.find(item=>item.id===space)?.root_id;
 const artifact=work?.artifacts.find(a=>a.id===(artifactId||work.activeArtifactId))||work?.artifacts[0];
 const resolveMedia=useCallback((value:unknown)=>flowMediaUrl(space,value),[space]);
 const prepareContent=useCallback((value:Parameters<typeof flowContentForWeb>[1])=>flowContentForWeb(space,value),[space]);
 const renderers={...contentRenderers,resource:(props:React.ComponentProps<typeof FlowResourceBlock>)=><FlowResourceBlock {...props} artifactTitle={artifact?.title}/>}
 useEffect(()=>{busyRef.current=busy;panelRef.current=panel},[busy,panel]);
 useEffect(()=>{if(!message)return;const timer=setTimeout(()=>setMessage(""),3200);return()=>clearTimeout(timer)},[message]);
 const loadMaterials=useCallback(async(workspaceId:string,query="",signal?:AbortSignal,offset=0)=>{
  return hostCall<{entries:Material[];nextOffset:number|null}>("flow_library_list",{workspace_id:workspaceId,query,offset,limit:50,...(selected.current?{work_id:selected.current}:{})},signal);
 },[]);
 const refreshMaterials=useCallback(async(workspaceId:string)=>{
  try{const workId=selected.current;const page=await loadMaterials(workspaceId);if(selected.current!==workId)return;setMaterials(page.entries);setMaterialOffset(page.nextOffset);setLibraryError("")}
  catch(e){setLibraryError(e instanceof Error?e.message:"자료 목록을 열지 못했습니다.")}
 },[loadMaterials]);
 const searchMaterials=useCallback(async(query:string,signal:AbortSignal,cursor?:string|null)=>{
  const position=cursor?JSON.parse(cursor) as {library:number|null;original:string|null;originalDone?:boolean}: {library:0,original:null,originalDone:false};
  const [curated,original]=await Promise.allSettled([
   position.library===null?Promise.resolve({entries:[] as Material[],nextOffset:null}):hostCall<{entries:Material[];nextOffset:number|null}>("flow_library_list",{workspace_id:space,query,offset:position.library,limit:25,...(selected.current?{work_id:selected.current}:{})},signal),
   position.originalDone?Promise.resolve({items:[],nextCursor:null} as ResourcePage):resourceCall<ResourcePage>("flow_resource_search",{query,...(position.original?{cursor:position.original}:{}),limit:25},signal)
  ]);
  signal.throwIfAborted();
  if(curated.status==="rejected"&&original.status==="rejected")throw new Error("자료에 연결하지 못했습니다.");
  const library=curated.status==="fulfilled"?curated.value:{entries:[],nextOffset:null},sources=original.status==="fulfilled"?original.value:{items:[],nextCursor:null};
  const next={library:library.nextOffset,original:sources.nextCursor,originalDone:sources.nextCursor===null};
  return {items:[...library.entries,...sources.items.map(value=>({id:"original:"+flowResourceKey(value.reference),title:value.title,collection:value.detail,reference:value.reference,sourceVersion:value.sourceVersion,external:true}))],nextCursor:next.library===null&&next.originalDone?null:JSON.stringify(next),partial:curated.status==="rejected"||original.status==="rejected"||sources.partial};
 },[space]);
 const searchWorks=useCallback((query:string,signal:AbortSignal,offset=0)=>hostCall<{works:FlowWorkItem[];nextOffset:number|null}>("flow_work_list",{workspace_id:space,query,offset,limit:50},signal),[space]);
 const open=useCallback(async(workspaceId:string,id:string,target?:string)=>{
  const number=++request.current;const changed=selected.current!==id;selected.current=id;if(changed)void refreshMaterials(workspaceId);selectedArtifact.current=target;setError("");setLoading(true);
  try{
   const result=id?await readFlowWork(workspaceId,id,target):null;
   if(number!==request.current)return;
   setDetail(result);setArtifactId(target);
   if(result){setDraftName(result.work.name);setDraftPurpose(result.work.purpose||"");revision.current=result.stateRevision||"";sessionStorage.setItem(FLOW_LAST_WORK_KEY,JSON.stringify({workspaceId,workId:id}))}
  }catch(e){if(number===request.current)setError(e instanceof Error?e.message:"작업을 열지 못했습니다.")}
  finally{if(number===request.current)setLoading(false)}
 },[refreshMaterials]);
 const initialize=useCallback((preferred?:string)=>{
  return hostCall<{workspaces:Workspace[]}>("flow_workspace_list").then(async({workspaces})=>{
   setSpaces(workspaces);
   const viewTarget=new URLSearchParams(location.search).get("screen");if(viewTarget==="library"||viewTarget==="files")setScreen(viewTarget);
   setTheme(document.documentElement.dataset.theme==="dark"?"dark":"light");
   const params=new URLSearchParams(location.search),remembered=flowSelectionFromStorage(sessionStorage.getItem(FLOW_LAST_WORK_KEY));
   const workspaceId=selectFlowWorkspace(workspaces.map(s=>s.id),preferred,params.get("workspace"),remembered);
   if(!workspaceId){setSpace("");setDetail(null);setLoading(false);return}
   setSpace(workspaceId);
   if(workspaces.find(item=>item.id===workspaceId)?.apiVersion!==5)throw new Error("작업 서비스의 연결을 갱신해 주세요. 저장 기능은 잠시 사용할 수 없습니다.");
   const listing=await hostCall<{works:FlowWorkItem[]}>("flow_work_list",{workspace_id:workspaceId});
   setWorks(listing.works);
   const id=params.get("work")||(remembered?.workspaceId===workspaceId?remembered.workId:null)||listing.works[0]?.id||"";
   const target=params.get("artifact")||undefined;
   const opened=open(workspaceId,id,target);
   await opened;
  }).catch(e=>{setError(e instanceof Error?e.message:"작업을 불러오지 못했습니다.");setLoading(false)});
 },[open]);
 useEffect(()=>{
  void initialize();
  const reference=resourceHandoffFromSearch(location.search),controller=new AbortController();
  if(reference)void readLinked(reference,controller.signal).then(value=>{if(!controller.signal.aborted){setResource(value);setPanel("resource")}}).catch(()=>{if(!controller.signal.aborted)setError("자료를 열지 못했습니다.")});
  return()=>{controller.abort();request.current++};
 },[initialize]);
 useEffect(()=>{
  if(!space)return;let checking=false;const controller=new AbortController();
  async function refresh(){
   if(checking||busyRef.current||panelRef.current==="context"||panelRef.current==="new"||document.visibilityState==="hidden")return;
   checking=true;const number=request.current,id=selected.current;
   try{
    const listing=await hostCall<{stateRevision:string;works:FlowWorkItem[]}>("flow_work_list",{workspace_id:space},controller.signal);
    if(controller.signal.aborted||request.current!==number||listing.stateRevision===revision.current)return;
    const latest=id?await readFlowWork(space,id,selectedArtifact.current,controller.signal):null;
    if(controller.signal.aborted||request.current!==number)return;
    setWorks(listing.works);
    if(latest)setDetail(latest);revision.current=listing.stateRevision;
    void refreshMaterials(space);
   }catch{if(!controller.signal.aborted)setError("최신 내용을 불러오지 못했습니다. 다시 연결해 주세요.")}
   finally{checking=false}
  }
  const timer=setInterval(()=>void refresh(),5000);return()=>{controller.abort();clearInterval(timer)};
 },[space,refreshMaterials]);
 function close(){setPanel(null);requestAnimationFrame(()=>returnRef.current?.focus())}
 function navigate(next:Screen){scroll.current[screen]=scrollY;setScreen(next);setPanel(null);const url=new URL(location.href);url.searchParams.set("screen",next);history.replaceState(null,"",url);requestAnimationFrame(()=>window.scrollTo(0,scroll.current[next]||0))}
 function selectWork(id:string){if(busy)return;navigate("work");const url=new URL(location.href);url.searchParams.set("work",id);url.searchParams.delete("artifact");history.replaceState(null,"",url);void open(space,id)}
 async function refreshWork(){if(work){const result=await readFlowWork(space,work.id,selectedArtifact.current);setDetail(result);revision.current=result.stateRevision||""}}
 async function updateWork(patch:Record<string,unknown>){
  if(!work||!writable||busy)return;setBusy(true);setError("");
  try{await mutate("flow_work_update",{workspace_id:space,work_id:work.id,expected_revision:work.revision,...patch});await refreshWork()}
  catch(e){setError(e instanceof Error?e.message:"작업을 저장하지 못했습니다.");throw e}
  finally{setBusy(false)}
 }
 function isConnected(item:Material){return Boolean(work&&(item.external&&item.reference?(work.linkedResources||[]).some(reference=>flowResourceKey(reference)===flowResourceKey(item.reference!)):work.sourceIds.includes(item.id)))}
 async function connect(item:Material){
  if(!work||isConnected(item))return;
  if(item.external&&item.reference)await updateWork({linked_resources:[...(work.linkedResources||[]),item.reference]});
  else await updateWork({source_ids:[...new Set([...work.sourceIds,item.id])]});setMessage("참고 자료로 연결했습니다.");
 }
 async function linkResource(value:FlowOpenResource){
  if(!work||value.kind==="flow-source")return;
  const reference=flowResourceIdentity(value),current=work.linkedResources||[];
  if(workReferences(work,detail?.sources,root).some(item=>item.reference&&flowResourceKey(item.reference)===flowResourceKey(reference)))return;
  await updateWork({linked_resources:[...current,reference]});setMessage("참고 자료로 연결했습니다.");
 }
 async function createWork(){
  if(!space||!name.trim()||busy)return;setBusy(true);setError("");
  const signature=JSON.stringify([space,name.trim(),source?.id]);
  if(createAttempt.current?.signature!==signature)createAttempt.current={signature,key:crypto.randomUUID()};
  try{
   const result=await hostCall<{work:FlowWork}>("flow_work_create",{workspace_id:space,name:name.trim(),idempotency_key:createAttempt.current.key,...(source?.artifact?{source_id:source.id}:source?.external&&source.reference?{linked_resources:[source.reference]}:{})});
   if(source&&!source.artifact&&!source.external)await hostCall("flow_work_update",{workspace_id:space,work_id:result.work.id,expected_revision:result.work.revision,idempotency_key:createAttempt.current.key+"-source",source_ids:[source.id]});
   setWorks(list=>[...list,{...result.work,workspaceId:space,artifact:result.work.artifacts[0]}]);
   await open(space,result.work.id);navigate("work");setName("");setSource(null);createAttempt.current=null;
  }catch(e){setError(e instanceof Error?e.message:"작업을 만들지 못했습니다.")}
  finally{setBusy(false)}
 }
 async function readMaterial(item:Material,signal:AbortSignal=new AbortController().signal):Promise<Material>{
  if(item.external&&item.reference){const original=await readLinked(item.reference,signal);return {...item,title:original.title,original,sourceVersion:original.resolved?.version}}
  return readFlowMaterial(space,item,signal);
 }
 async function saveMaterial(item:Material,patch:{title:string;body:string}){
  const entry={id:item.id,title:patch.title,body:patch.body,scope:item.scope,reference:item.reference,sourceVersion:item.sourceVersion};
  const result=await mutate<{entry:Material}>("flow_library_upsert",{workspace_id:space,entry,expected_revision:item.revision??0});
  setMaterials(current=>current.some(value=>value.id===item.id)?current.map(value=>value.id===item.id?{...value,...result.entry}:value):[result.entry,...current]);
 }
 async function saveArtifact(){
  if(!work||!artifact||busy)return;setBusy(true);setError("");
  try{await mutate("flow_snapshot_create",{workspace_id:space,work_id:work.id,artifact_id:artifact.id,expected_revision:artifact.revision});await refreshMaterials(space);setMessage("라이브러리에 추가했습니다.")}
  catch(e){setError(e instanceof Error?e.message:"작업물을 보관하지 못했습니다.")}
  finally{setBusy(false)}
 }
 async function downloadArtifact(){
  if(!artifact)return;
  try{
   const html=artifact.kind==="html"?await exportHtmlArtifact(artifact,resolveMedia):null;
   const text=html??JSON.stringify(artifact,null,2),type=html?"text/html;charset=utf-8":"application/json";
   const url=URL.createObjectURL(new Blob([text],{type})),anchor=document.createElement("a");
   anchor.href=url;anchor.download=(artifact.title||work?.name||"작업물").replace(/[\\/:*?"<>|]/g,"-")+(html?".html":".json");anchor.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }catch(e){setError(e instanceof Error?e.message:"작업물을 내려받지 못했습니다.")}
 }
 async function act(id:string,action:"apply"|"undo"){
  setBusy(true);setError("");try{await hostCall("flow_change_action",{workspace_id:space,change_id:id,action});await refreshWork()}
  catch(e){setError(e instanceof Error?e.message:"변경을 처리하지 못했습니다.")}
  finally{setBusy(false)}
 }
 const availableUndo=detail?.undo;const undo=availableUndo&&availableUndo.artifactId===artifact?.id&&availableUndo.appliedRevision===artifact?.revision?availableUndo:null;
 const proposals=detail?.changes.filter(change=>["review","conflict"].includes(change.status))||[];
 const library=(compact=false)=><LibraryBrowser key={space} items={materials} compact={compact} onOpen={readMaterial} onSearch={searchMaterials} hasMore={materialOffset!==null} onLoadMore={async(signal:AbortSignal)=>{if(materialOffset===null)return;const page=await loadMaterials(space,"",signal,materialOffset);setMaterials(items=>[...items,...page.entries]);setMaterialOffset(page.nextOffset)}} renderItem={(item:Material,{focusHeading}:{focusHeading:()=>void})=><MaterialContent key={item.id} item={item} workspaceId={space} root={root} onRetry={focusHeading} onSave={writable?saveMaterial:undefined}/>}
  actions={(item:Material)=><div className="su-row">{work&&writable&&<B disabled={busy||isConnected(item)} onClick={()=>void connect(item).catch(()=>{})}><Link2 size="1em"/>{isConnected(item)?"연결됨":"참고 자료로 연결"}</B>}{writable&&<B variant="ghost" onClick={()=>{setSource(item);setName(item.title);setPanel("new")}}>{item.artifact?"복사해서 새 작업":"이 자료로 새 작업"}</B>}</div>}/>;
 const title=({sources:"참고 자료",context:"작업 정보",new:"새 작업",settings:"설정",changes:"변경 확인",resource:resource?.kind==="library-issue"?"참고 자료":resource?.title||"참고 자료"} as Record<string,string>)[panel||""]||"";
 return <div className="flow-product">
  <a className="su-skip" href="#flow-main">본문으로</a>
  <AppHeader screen={screen} onNavigate={navigate} navigationReturnRef={returnRef} onSettings={(trigger:HTMLElement)=>{returnRef.current=trigger;setPanel("settings")}}
   workMenu={{onSearch:searchWorks,groups:{recent:works.filter(w=>w.id===work?.id),other:works.filter(w=>w.id!==work?.id)},currentId:work?.id,reviewCount:new Map(works.map(w=>[w.id,w.reviewCount||0])),onSelect:selectWork,onCreate:(trigger:HTMLElement)=>{returnRef.current=trigger;setName("");setSource(null);setPanel("new")}}}
   actions={screen==="work"&&work&&<>
    {undo&&writable&&<Tooltip content="이전 상태로" compact><B uniform variant="ghost" aria-label="이전 상태로" disabled={busy} onClick={()=>void act(undo.id,"undo")}><Undo2 size="1em"/></B></Tooltip>}
    <Tooltip content="참고 자료" compact><B ref={sourceTrigger} uniform variant="ghost" aria-label="참고 자료" aria-expanded={panel==="sources"} onClick={()=>{returnRef.current=sourceTrigger.current;setPanel(panel==="sources"?null:"sources")}}><FileText size="1em"/></B></Tooltip>
    <Menu><Menu.Trigger><B ref={moreTrigger} uniform variant="ghost" aria-label="더 보기"><MoreHorizontal size="1em"/></B></Menu.Trigger><Menu.Content align="end">
     {writable&&artifact?.kind!=="blank"&&<Menu.Item onSelect={()=>void saveArtifact()}><Archive size="1em"/>라이브러리에 추가</Menu.Item>}
     {artifact?.kind!=="blank"&&<Menu.Item onSelect={()=>void downloadArtifact()}><Download size="1em"/>작업물 내려받기</Menu.Item>}
     <Menu.Item onSelect={()=>{returnRef.current=moreTrigger.current;setDraftName(work.name);setDraftPurpose(work.purpose||"");setPanel("context")}}><Info size="1em"/>작업 정보</Menu.Item>
     {proposals.length>0&&<Menu.Item onSelect={()=>{returnRef.current=moreTrigger.current;setPanel("changes")}}>변경 확인</Menu.Item>}
    </Menu.Content></Menu>
   </>}/>
  <main className="flow-product-main" id="flow-main">
   {error&&<div className="flow-product-notice su-stack"><p role="alert">{error}</p><div><B onClick={()=>{setLoading(true);setError("");void initialize(space)}}>다시 연결</B></div></div>}
   {loading&&!detail?<p className="flow-product-notice" role="status">작업을 여는 중입니다.</p>:<>
    <div hidden={screen!=="work"}>{work?<WorkCanvas work={work} artifactId={artifactId} onSelect={(id:string)=>{void open(space,work.id,id)}} renderers={renderers} prepareContent={prepareContent} resolveMediaUrl={resolveMedia}/>:<div className="flow-product-notice">{space?<B onClick={()=>setPanel("new")}>새 작업</B>:<p>연결된 작업공간이 없습니다.</p>}</div>}</div>
    <div hidden={screen!=="library"}>{libraryError&&<div><p role="alert">{libraryError}</p><B variant="ghost" onClick={()=>void refreshMaterials(space)}>다시 열기</B></div>}{library()}</div>
    <div hidden={screen!=="files"} className="flow-product-files">
     <FlowFilePicker selected={new Set()} busy={busy} onPick={(root,path)=>setFile(hostFileResource(root,path))}/>
     {file&&<FlowResourceView resource={file} onClose={()=>setFile(null)} action={work&&writable?<B onClick={()=>void linkResource(file).catch(()=>{})}><Link2 size="1em"/>참고 자료로 연결</B>:undefined}/>}
    </div>
   </>}
  </main>
  {message&&<div className="su-toast" role="status">{message}</div>}
  <Overlay open={Boolean(panel)} title={title} onClose={close} returnFocusRef={returnRef} placement={panel==="sources"||panel==="resource"?"right":"center"}>
   {panel==="sources"&&work&&<ReferencePanel key={space+":"+work.id} work={work} sources={detail?.sources||[]} root={root} readReference={readLinked} readSource={readMaterial} busy={busy}
    renderSource={(item:Material,{focusHeading}:{focusHeading:()=>void})=><MaterialContent key={item.id} item={item} workspaceId={space} root={root} onRetry={focusHeading}/>}
    renderReference={(_reference:FlowLinkedResource,value:FlowOpenResource)=><FlowResourceView resource={value} hideTitle/>}
    onDisconnect={writable?(item:WorkReference)=>{const patch=disconnectWorkReference(work,item);return updateWork({source_ids:patch.sourceIds,linked_resources:patch.linkedResources})}:undefined}/>}
   {panel==="resource"&&resource&&<FlowResourceView resource={resource} hideTitle action={work&&writable?<B onClick={()=>void linkResource(resource).catch(()=>{})}>참고 자료로 연결</B>:undefined}/>}
   {panel==="new"&&<form className="su-stack" data-gap="section" onSubmit={e=>{e.preventDefault();void createWork()}}><Field label="작업 이름"><Input value={name} onChange={e=>setName(e.target.value)} autoFocus maxLength={160}/></Field>{source&&<p>{source.title}</p>}<div className="su-row"><B type="submit" variant="solid" disabled={!name.trim()||!writable||busy}>시작하기</B><B variant="ghost" onClick={close}>취소</B></div></form>}
   {panel==="context"&&work&&<form className="su-stack" data-gap="section" onSubmit={e=>{e.preventDefault();void updateWork({name:draftName.trim(),purpose:draftPurpose}).then(close).catch(()=>{})}}><Field label="작업 이름"><Input value={draftName} onChange={e=>setDraftName(e.target.value)} maxLength={160}/></Field><Field label="작업 목적"><Textarea value={draftPurpose} onChange={e=>setDraftPurpose(e.target.value)} rows={3}/></Field><div className="su-row"><B type="submit" variant="solid" disabled={!draftName.trim()||!writable||busy}>저장</B><B variant="ghost" onClick={close}>취소</B></div></form>}
   {panel==="settings"&&<div className="su-stack" data-gap="section">
    {spaces.length>1&&<FieldSelect aria-label="작업공간" visibleLabel value={space} options={spaces.map(s=>({value:s.id,label:s.id}))} onChange={option=>{close();setLoading(true);setError("");void initialize(option.value)}}/>}
    <SegmentedControl value={theme} onChange={value=>{setTheme(value);document.documentElement.dataset.theme=value;localStorage.setItem("toolkit-theme",value)}} aria-label="화면 테마" size="md" pill={false}><SegmentedControl.Option value="light"><Sun size="1em"/>밝게</SegmentedControl.Option><SegmentedControl.Option value="dark"><Moon size="1em"/>어둡게</SegmentedControl.Option></SegmentedControl>
    <a href="/settings">계정과 연결 설정</a>
   </div>}
   {panel==="changes"&&<div className="su-stack" data-gap="section">{proposals.map(change=><ChangeDetail key={change.id} change={change} workspaceId={space} current={work?.artifacts.find(a=>a.id===change.artifactId)} onApply={writable?()=>act(change.id,"apply"):undefined} busy={busy}/>)}</div>}

  </Overlay>
 </div>;
}


function ChangeDetail({change,workspaceId,current,onApply,busy}:{change:FlowChange;workspaceId:string;current?:FlowWork["artifacts"][number];onApply?:()=>Promise<void>;busy:boolean}){
 const currentId=current?.id,currentRevision=current?.revision;
 const [open,setOpen]=useState(false),[data,setData]=useState<{before:FlowWork["artifacts"][number];after:FlowWork["artifacts"][number]}|null>(null),[error,setError]=useState(""),[attempt,setAttempt]=useState(0);
 useEffect(()=>{
  if(!open||!currentId||currentRevision===undefined)return;
  const controller=new AbortController();
  void Promise.resolve().then(()=>{if(controller.signal.aborted)return null;setError("");setData(null);return Promise.all([readFlowArtifact(workspaceId,{artifact_id:currentId,revision:currentRevision},controller.signal),readFlowArtifact(workspaceId,{change_id:change.id,part:"proposal"},controller.signal)])})
   .then(values=>{if(values&&!controller.signal.aborted)setData({before:values[0],after:values[1]})}).catch(e=>{if(!controller.signal.aborted)setError(e instanceof Error?e.message:"수정안을 열지 못했습니다.")});
  return()=>controller.abort();
 },[workspaceId,change.id,currentId,currentRevision,open,attempt]);
 return <details open={open} onToggle={event=>setOpen(event.currentTarget.open)}><summary>{change.proposal?.artifact?.title||"수정안"}</summary>{open&&<div className="su-stack" data-gap="section">
  {error?<div><p role="alert">{error}</p><B onClick={()=>setAttempt(value=>value+1)}>다시 열기</B></div>:data?<ReviewComparison current={<ArtifactPreview artifact={data.before} renderers={contentRenderers} resolveMediaUrl={v=>flowMediaUrl(workspaceId,v)} prepareContent={c=>flowContentForWeb(workspaceId,c)}/>} proposed={<ArtifactPreview artifact={data.after} renderers={contentRenderers} resolveMediaUrl={v=>flowMediaUrl(workspaceId,v)} prepareContent={c=>flowContentForWeb(workspaceId,c)}/>}/>:<p role="status">수정안을 여는 중입니다.</p>}
  {change.status==="review"&&onApply&&<B disabled={busy||!data} onClick={()=>void onApply()}>수정안 반영</B>}{change.status==="conflict"&&<p>원본이 변경되어 반영하지 않았습니다.</p>}
 </div>}</details>;
}
