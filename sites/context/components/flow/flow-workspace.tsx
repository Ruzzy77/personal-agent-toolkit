"use client";
import {useCallback,useEffect,useRef,useState} from "react";
import {Archive,Download,FileText,Info,Link2,MoreHorizontal,Moon,Sun,Undo2} from "lucide-react";
import {Input} from "@openai/apps-sdk-ui/components/Input";
import {Textarea} from "@openai/apps-sdk-ui/components/Textarea";
import {Menu} from "@openai/apps-sdk-ui/components/Menu";
import {Tooltip} from "@openai/apps-sdk-ui/components/Tooltip";
import {SegmentedControl} from "@openai/apps-sdk-ui/components/SegmentedControl";
import {Field,FieldSelect} from "@personal-agent/ui-kit/react";
import {B,AppHeader,Overlay,WorkCanvas,LibraryBrowser,ArtifactPreview,ReviewComparison,contentRenderers,exportHtmlArtifact} from "@personal-agent/flow-surface";
import {hostCall} from "../../lib/host-files";
import {flowContentForWeb,flowMediaUrl} from "../../lib/flow-content";
import type {FlowRead,FlowWork,FlowWorkItem,FlowLinkedResource,FlowSource} from "../../lib/flow-content";
import {FlowResourceView} from "./flow-resource-view";
import type {FlowOpenResource} from "./flow-resource-view";
import {FlowFilePicker} from "./flow-file-picker";
import {FlowResourceBlock} from "./flow-resource-block";
import {flowResourceIdentity,flowResourceKey,hostFileResource} from "../../lib/flow-resources";
import {readLinked} from "../../lib/flow-resource-read";
import {FLOW_LAST_WORK_KEY,flowSelectionFromStorage,selectFlowWorkspace,selectFlowWork,resourceHandoffFromSearch} from "../../lib/flow-handoff";
import "./flow-workspace.css";

type Workspace={id:string;permission:string;root_id?:string};
type Material=FlowSource&{revision?:number;scope?:{kind:"work"|"workspace"|"personal";workId?:string};reference?:FlowLinkedResource;sourceVersion?:string};
type Screen="work"|"library"|"files";
function MaterialContent({item,workspaceId,onSave}:{item:Material;workspaceId:string;onSave:(item:Material,patch:{title:string;body:string})=>Promise<void>}){
 const [resource,setResource]=useState<FlowOpenResource|null>(null),[error,setError]=useState(""),[editing,setEditing]=useState(false),[busy,setBusy]=useState(false),[title,setTitle]=useState(item.title),[body,setBody]=useState(item.body||"");
 const referenceKey=JSON.stringify(item.reference||null);
 useEffect(()=>{
  const reference=JSON.parse(referenceKey) as FlowLinkedResource|null;
  if(!reference)return;
  const controller=new AbortController();
  void readLinked(reference,controller.signal).then(value=>{if(!controller.signal.aborted)setResource(value)}).catch(()=>{if(!controller.signal.aborted)setError("원본을 열지 못했습니다.")});
  return()=>controller.abort();
 },[referenceKey]);
 const baseline=useRef(item);
 async function save(){setBusy(true);setError("");try{await onSave(baseline.current,{title:title.trim(),body});setEditing(false)}catch(e){setError(e instanceof Error?e.message:"자료를 저장하지 못했습니다.")}finally{setBusy(false)}}
 if(editing)return <form className="su-stack" onSubmit={e=>{e.preventDefault();void save()}}>
  <Field label="이름"><Input value={title} onChange={e=>setTitle(e.target.value)} maxLength={160}/></Field>
  <Field label="내용"><Textarea value={body} onChange={e=>setBody(e.target.value)} rows={12}/></Field>
  {error&&<p role="alert">{error}</p>}<div className="su-row"><B type="submit" variant="solid" disabled={busy||!title.trim()||!body.trim()&&!item.reference}>저장</B><B variant="ghost" onClick={()=>setEditing(false)} disabled={busy}>취소</B></div>
 </form>;
 return <div className="su-stack" data-gap="section">
  {!item.artifact&&item.body&&<div className="flow-material-text">{item.body}</div>}
  {item.artifact&&<ArtifactPreview artifact={item.artifact} renderers={contentRenderers} prepareContent={c=>flowContentForWeb(workspaceId,c)} resolveMediaUrl={v=>flowMediaUrl(workspaceId,v)} headingLevel={2}/>}
  {resource&&(item.body?<details><summary>원본</summary><FlowResourceView resource={resource} hideTitle/></details>:<FlowResourceView resource={resource} hideTitle/>)}
  {error&&<p role="alert">{error}</p>}
  {item.reference&&!resource&&!error&&<p role="status">원본을 여는 중입니다.</p>}
  {item.scope&&<div><B variant="ghost" onClick={()=>{baseline.current=item;setTitle(item.title);setBody(item.body||"");setEditing(true)}}>자료 수정</B></div>}
 </div>;
}
export function FlowWorkspace(){
 const [spaces,setSpaces]=useState<Workspace[]>([]),[space,setSpace]=useState(""),[works,setWorks]=useState<FlowWorkItem[]>([]),[detail,setDetail]=useState<FlowRead|null>(null);
 const [screen,setScreen]=useState<Screen>("work"),[panel,setPanel]=useState<string|null>(null),[artifactId,setArtifactId]=useState<string>(),[materials,setMaterials]=useState<Material[]>([]);
 const [loading,setLoading]=useState(true),[busy,setBusy]=useState(false),[error,setError]=useState(""),[message,setMessage]=useState(""),[name,setName]=useState(""),[source,setSource]=useState<Material|null>(null);
 const [resource,setResource]=useState<FlowOpenResource|null>(null),[file,setFile]=useState<FlowOpenResource|null>(null),[theme,setTheme]=useState("light");
 const [linkedResults,setLinked]=useState<{key:string;resources:FlowOpenResource[]}>({key:"",resources:[]});
 const [draftName,setDraftName]=useState(""),[draftPurpose,setDraftPurpose]=useState("");
 const returnRef=useRef<HTMLElement|null>(null),sourceTrigger=useRef<HTMLButtonElement>(null),moreTrigger=useRef<HTMLButtonElement>(null),request=useRef(0),selected=useRef(""),revision=useRef(""),busyRef=useRef(false),panelRef=useRef<string|null>(null),scroll=useRef<Record<string,number>>({});
 const createAttempt=useRef<{signature:string;key:string}|null>(null);
 const work=detail?.work,writable=spaces.find(s=>s.id===space)?.permission==="read_write";
 const linkedKey=JSON.stringify(work?.linkedResources||[]);
 const linked=linkedResults.key===linkedKey?linkedResults.resources:[];
 const artifact=work?.artifacts.find(a=>a.id===(artifactId||work.activeArtifactId))||work?.artifacts[0];
 useEffect(()=>{
  const controller=new AbortController();
  const references=JSON.parse(linkedKey) as FlowLinkedResource[];
  void Promise.all(references.map(reference=>readLinked(reference,controller.signal).catch(()=>null))).then(values=>{if(!controller.signal.aborted)setLinked({key:linkedKey,resources:values.filter((value):value is NonNullable<typeof value>=>value!==null)})});
  return()=>controller.abort();
 },[linkedKey]);
 const resolveMedia=useCallback((value:unknown)=>flowMediaUrl(space,value),[space]);
 const prepareContent=useCallback((value:Parameters<typeof flowContentForWeb>[1])=>flowContentForWeb(space,value),[space]);
 const renderers={...contentRenderers,resource:(props:React.ComponentProps<typeof FlowResourceBlock>)=><FlowResourceBlock {...props} artifactTitle={artifact?.title}/>}
 useEffect(()=>{busyRef.current=busy;panelRef.current=panel},[busy,panel]);
 useEffect(()=>{if(!message)return;const timer=setTimeout(()=>setMessage(""),3200);return()=>clearTimeout(timer)},[message]);
 const loadMaterials=useCallback(async(workspaceId:string,query="",signal?:AbortSignal)=>{
  let offset=0;const all:Material[]=[];
  do{const result=await hostCall<{entries:Material[];nextOffset:number|null}>("flow_library_list",{workspace_id:workspaceId,query,offset,limit:100},signal);all.push(...result.entries);if(result.nextOffset===null)break;if(result.nextOffset<=offset)throw new Error("자료 목록을 확인할 수 없습니다.");offset=result.nextOffset;}while(true);
  return all;
 },[]);
 const searchMaterials=useCallback((query:string,signal:AbortSignal)=>loadMaterials(space,query,signal),[space,loadMaterials]);
 const open=useCallback(async(workspaceId:string,id:string)=>{
  const number=++request.current;selected.current=id;setError("");setLoading(true);
  try{
   const result=id?await hostCall<FlowRead>("flow_work_read",{workspace_id:workspaceId,work_id:id}):null;
   if(number!==request.current)return;
   setDetail(result);setArtifactId(undefined);
   if(result){setDraftName(result.work.name);setDraftPurpose(result.work.purpose||"");revision.current=result.stateRevision||"";sessionStorage.setItem(FLOW_LAST_WORK_KEY,JSON.stringify({workspaceId,workId:id}))}
  }catch(e){if(number===request.current)setError(e instanceof Error?e.message:"작업을 열지 못했습니다.")}
  finally{if(number===request.current)setLoading(false)}
 },[]);
 const initialize=useCallback((preferred?:string)=>{
  return hostCall<{workspaces:Workspace[]}>("flow_workspace_list").then(async({workspaces})=>{
   setSpaces(workspaces);
   const viewTarget=new URLSearchParams(location.search).get("screen");if(viewTarget==="library"||viewTarget==="files")setScreen(viewTarget);
   setTheme(document.documentElement.dataset.theme==="dark"?"dark":"light");
   const params=new URLSearchParams(location.search),remembered=flowSelectionFromStorage(sessionStorage.getItem(FLOW_LAST_WORK_KEY));
   const workspaceId=selectFlowWorkspace(workspaces.map(s=>s.id),preferred,params.get("workspace"),remembered);
   if(!workspaceId){setSpace("");setDetail(null);setLoading(false);return}
   setSpace(workspaceId);
   const [listing,entries]=await Promise.all([hostCall<{works:FlowWorkItem[]}>("flow_work_list",{workspace_id:workspaceId}),loadMaterials(workspaceId)]);
   setWorks(listing.works);setMaterials(entries);
   const id=selectFlowWork(listing.works.map(w=>w.id),params.get("work"),remembered,workspaceId)||"";
   await open(workspaceId,id);
   const target=params.get("artifact");if(target)setArtifactId(target);
  }).catch(e=>{setError(e instanceof Error?e.message:"작업을 불러오지 못했습니다.");setLoading(false)});
 },[open,loadMaterials]);
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
    const latest=id?await hostCall<FlowRead>("flow_work_read",{workspace_id:space,work_id:id},controller.signal):null;
    const entries=await loadMaterials(space);
    if(controller.signal.aborted||request.current!==number)return;
    setWorks(listing.works);setMaterials(previous=>entries.map(entry=>{const old=previous.find(item=>item.id===entry.id);return old?.revision===entry.revision?{...old,...entry}:entry}));
    if(latest)setDetail(latest);revision.current=listing.stateRevision;
   }catch{if(!controller.signal.aborted)setError("최신 내용을 불러오지 못했습니다. 다시 연결해 주세요.")}
   finally{checking=false}
  }
  const timer=setInterval(()=>void refresh(),5000);return()=>{controller.abort();clearInterval(timer)};
 },[space,loadMaterials]);
 function close(){setPanel(null);requestAnimationFrame(()=>returnRef.current?.focus())}
 function navigate(next:Screen){scroll.current[screen]=scrollY;setScreen(next);setPanel(null);const url=new URL(location.href);url.searchParams.set("screen",next);history.replaceState(null,"",url);requestAnimationFrame(()=>window.scrollTo(0,scroll.current[next]||0))}
 function selectWork(id:string){if(busy)return;navigate("work");const url=new URL(location.href);url.searchParams.set("work",id);url.searchParams.delete("artifact");history.replaceState(null,"",url);void open(space,id)}
 async function refreshWork(){if(work){const result=await hostCall<FlowRead>("flow_work_read",{workspace_id:space,work_id:work.id});setDetail(result);revision.current=result.stateRevision||""}}
 async function updateWork(patch:Record<string,unknown>){
  if(!work||!writable||busy)return;setBusy(true);setError("");
  try{await hostCall("flow_work_update",{workspace_id:space,work_id:work.id,expected_revision:work.revision,idempotency_key:crypto.randomUUID(),...patch});await refreshWork()}
  catch(e){setError(e instanceof Error?e.message:"작업을 저장하지 못했습니다.");throw e}
  finally{setBusy(false)}
 }
 async function connect(item:Material){
  if(!work)return;
  await updateWork({source_ids:[...new Set([...work.sourceIds,item.id])]});setMessage("참고 자료로 연결했습니다.");
 }
 async function linkResource(value:FlowOpenResource){
  if(!work||value.kind==="flow-source")return;
  const reference=flowResourceIdentity(value),current=work.linkedResources||[];
  if(current.some(r=>flowResourceKey(r)===flowResourceKey(reference)))return;
  await updateWork({linked_resources:[...current,reference]});setMessage("참고 자료로 연결했습니다.");
 }
 async function createWork(){
  if(!space||!name.trim()||busy)return;setBusy(true);setError("");
  const signature=JSON.stringify([space,name.trim(),source?.id]);
  if(createAttempt.current?.signature!==signature)createAttempt.current={signature,key:crypto.randomUUID()};
  try{
   const result=await hostCall<{work:FlowWork}>("flow_work_create",{workspace_id:space,name:name.trim(),idempotency_key:createAttempt.current.key,...(source?.artifact?{source_id:source.id}:source?.reference?{linked_resources:[source.reference]}:{})});
   if(source&&!source.artifact)await hostCall("flow_work_update",{workspace_id:space,work_id:result.work.id,expected_revision:result.work.revision,idempotency_key:createAttempt.current.key+"-source",source_ids:[source.id]});
   setWorks(list=>[...list,{...result.work,workspaceId:space,artifact:result.work.artifacts[0]}]);
   await open(space,result.work.id);navigate("work");setName("");setSource(null);createAttempt.current=null;
  }catch(e){setError(e instanceof Error?e.message:"작업을 만들지 못했습니다.")}
  finally{setBusy(false)}
 }
 async function readMaterial(item:Material){
  const {entry}=await hostCall<{entry:Material}>("flow_library_read",{workspace_id:space,entry_id:item.id});
  setMaterials(current=>current.some(value=>value.id===item.id)?current.map(value=>value.id===item.id?{...value,...entry}:value):[...current,entry]);
 }
 async function saveMaterial(item:Material,patch:{title:string;body:string}){
  const entry={id:item.id,title:patch.title,body:patch.body,scope:item.scope,reference:item.reference,sourceVersion:item.sourceVersion};
  const result=await hostCall<{entry:Material}>("flow_library_upsert",{workspace_id:space,entry,expected_revision:item.revision??0,idempotency_key:crypto.randomUUID()});
  setMaterials(current=>current.map(value=>value.id===item.id?{...value,...result.entry}:value));
 }
 async function saveArtifact(){
  if(!work||!artifact||busy)return;setBusy(true);setError("");
  try{await hostCall("flow_snapshot_create",{workspace_id:space,work_id:work.id,artifact_id:artifact.id,expected_revision:artifact.revision,idempotency_key:crypto.randomUUID()});setMaterials(await loadMaterials(space));setMessage("라이브러리에 추가했습니다.")}
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
 const undo=detail?.changes.findLast(change=>change.artifactId===artifact?.id&&change.status==="completed"&&change.before&&change.appliedRevision===artifact?.revision);
 const proposals=detail?.changes.filter(change=>["review","conflict"].includes(change.status))||[];
 const library=(compact=false)=><LibraryBrowser key={space} items={materials} compact={compact} onOpen={readMaterial} onSearch={searchMaterials} renderItem={(item:Material)=><MaterialContent key={item.id} item={item} workspaceId={space} onSave={saveMaterial}/>}
  actions={(item:Material)=><div className="su-row">{work&&writable&&<B disabled={busy||work.sourceIds.includes(item.id)} onClick={()=>void connect(item).catch(()=>{})}><Link2 size="1em"/>{work.sourceIds.includes(item.id)?"연결됨":"참고 자료로 연결"}</B>}{writable&&<B variant="ghost" onClick={()=>{setSource(item);setName(item.title);setPanel("new")}}>{item.artifact?"복사해서 새 작업":"이 자료로 새 작업"}</B>}</div>}/>;
 const title=({sources:"참고 자료",context:"작업 정보",new:"새 작업",settings:"설정",changes:"변경 확인",resource:"참고 자료"} as Record<string,string>)[panel||""]||"";
 return <div className="flow-product">
  <a className="su-skip" href="#flow-main">본문으로</a>
  <AppHeader screen={screen} onNavigate={navigate} navigationReturnRef={returnRef} onSettings={(trigger:HTMLElement)=>{returnRef.current=trigger;setPanel("settings")}}
   workMenu={{groups:{recent:works.filter(w=>w.id===work?.id),other:works.filter(w=>w.id!==work?.id)},currentId:work?.id,reviewCount:new Map(works.map(w=>[w.id,w.reviewCount||0])),onSelect:selectWork,onCreate:(trigger:HTMLElement)=>{returnRef.current=trigger;setName("");setSource(null);setPanel("new")}}}
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
   {error&&<div className="flow-product-notice su-stack"><p role="alert">{error}</p><B onClick={()=>{setLoading(true);setError("");void initialize(space)}}>다시 연결</B></div>}
   {loading&&!detail?<p className="flow-product-notice" role="status">작업을 여는 중입니다.</p>:<>
    <div hidden={screen!=="work"}>{work?<WorkCanvas work={work} artifactId={artifactId} onSelect={(id:string)=>setArtifactId(id)} renderers={renderers} prepareContent={prepareContent} resolveMediaUrl={resolveMedia}/>:<div className="flow-product-notice">{space?<B onClick={()=>setPanel("new")}>새 작업</B>:<p>연결된 작업공간이 없습니다.</p>}</div>}</div>
    <div hidden={screen!=="library"}>{library()}</div>
    <div hidden={screen!=="files"} className="flow-product-files">
     <FlowFilePicker selected={new Set()} busy={busy} onPick={(root,path)=>setFile(hostFileResource(root,path))}/>
     {file&&<FlowResourceView resource={file} onClose={()=>setFile(null)} action={work&&writable?<B onClick={()=>void linkResource(file).catch(()=>{})}><Link2 size="1em"/>참고 자료로 연결</B>:undefined}/>}
    </div>
   </>}
  </main>
  {message&&<div className="su-toast" role="status">{message}</div>}
  <Overlay open={Boolean(panel)} title={title} onClose={close} returnFocusRef={returnRef} placement={panel==="sources"||panel==="resource"?"right":"center"}>
   {panel==="sources"&&<div className="su-stack">{linked.map(value=><B key={flowResourceKey(flowResourceIdentity(value as Exclude<FlowOpenResource,{kind:"flow-source"}>))} variant="ghost" onClick={()=>{setResource(value);setPanel("resource")}}>{value.title}</B>)}{library(true)}</div>}
   {panel==="resource"&&resource&&<FlowResourceView resource={resource} hideTitle action={work&&writable?<B onClick={()=>void linkResource(resource).catch(()=>{})}>참고 자료로 연결</B>:undefined}/>}
   {panel==="new"&&<form className="su-stack" data-gap="section" onSubmit={e=>{e.preventDefault();void createWork()}}><Field label="작업 이름"><Input value={name} onChange={e=>setName(e.target.value)} autoFocus maxLength={160}/></Field>{source&&<p>{source.title}</p>}<div className="su-row"><B type="submit" variant="solid" disabled={!name.trim()||!writable||busy}>시작하기</B><B variant="ghost" onClick={close}>취소</B></div></form>}
   {panel==="context"&&work&&<form className="su-stack" data-gap="section" onSubmit={e=>{e.preventDefault();void updateWork({name:draftName.trim(),purpose:draftPurpose}).then(close).catch(()=>{})}}><Field label="작업 이름"><Input value={draftName} onChange={e=>setDraftName(e.target.value)} maxLength={160}/></Field><Field label="작업 목적"><Textarea value={draftPurpose} onChange={e=>setDraftPurpose(e.target.value)} rows={3}/></Field><div className="su-row"><B type="submit" variant="solid" disabled={!draftName.trim()||!writable||busy}>저장</B><B variant="ghost" onClick={close}>취소</B></div></form>}
   {panel==="settings"&&<div className="su-stack" data-gap="section">
    {spaces.length>1&&<FieldSelect aria-label="작업공간" visibleLabel value={space} options={spaces.map(s=>({value:s.id,label:s.id}))} onChange={option=>{close();setLoading(true);setError("");void initialize(option.value)}}/>}
    <SegmentedControl value={theme} onChange={value=>{setTheme(value);document.documentElement.dataset.theme=value;localStorage.setItem("toolkit-theme",value)}} aria-label="화면 테마" size="md" pill={false}><SegmentedControl.Option value="light"><Sun size="1em"/>밝게</SegmentedControl.Option><SegmentedControl.Option value="dark"><Moon size="1em"/>어둡게</SegmentedControl.Option></SegmentedControl>
    <a href="/settings">계정과 연결 설정</a>
   </div>}
   {panel==="changes"&&<div className="su-stack" data-gap="section">{proposals.map(change=><section key={change.id} className="su-stack"><ReviewComparison current={<ArtifactPreview artifact={work?.artifacts.find(a=>a.id===change.artifactId)} renderers={renderers} resolveMediaUrl={resolveMedia} prepareContent={prepareContent}/>} proposed={change.proposal?.artifact?<ArtifactPreview artifact={change.proposal.artifact} renderers={renderers} resolveMediaUrl={resolveMedia} prepareContent={prepareContent}/>:<p>변경 범위를 다시 확인해 주세요.</p>}/>{change.status==="review"&&writable&&<B disabled={busy} onClick={()=>void act(change.id,"apply")}>수정안 반영</B>}{change.status==="conflict"&&<p>원본이 변경되어 반영하지 않았습니다.</p>}</section>)}</div>}
  </Overlay>
 </div>;
}
