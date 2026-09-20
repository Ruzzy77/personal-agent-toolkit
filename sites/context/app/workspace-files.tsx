"use client";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowLeft, Download, File, Folder, FolderPlus, Pencil, RefreshCw, Save, Trash2, Upload, X } from "lucide-react";
import { FieldSelect, IconButton, UiButton, UiInput, UiTextarea } from "./ui";
import { InlineDocument } from "./inline-document";
import { ownerFetch } from "../lib/owner-client";
import { draftKey, fileUrl, hostCall, joined, linkedCorpus, restoredDraft, uploadChunks, type FileDraft, type FileEntry, type HostRoot, type Transfer } from "../lib/host-files";

type Listing={entries:FileEntry[];next_cursor:string|null};
type Modal={kind:"folder"|"file"|"move"|"saveAs";title:string;value:string;entry?:FileEntry};
const TEXT=/\.(md|markdown|txt|json|jsonc|csv|tsv|ya?ml|toml|ini|py|js|jsx|ts|tsx|css|html?|sh|xml|svg|log|sql)$/i;
const MARKDOWN=/\.(md|markdown)$/i;
const RASTER=/^image\/(png|jpeg|gif|webp|avif)$/;
const humanSize=(n:number)=>n<1024?n+" B":n<1048576?(n/1024).toFixed(1)+" KB":n<1073741824?(n/1048576).toFixed(1)+" MB":(n/1073741824).toFixed(1)+" GB";
const failure=(error:unknown)=>error instanceof Error?error.message:"작업을 완료하지 못했습니다.";

export function WorkspaceFiles(){
  const [roots,setRoots]=useState<HostRoot[]>([]),[root,setRoot]=useState("workspace"),[path,setPath]=useState(".");
  const [entries,setEntries]=useState<FileEntry[]>([]),[cursor,setCursor]=useState<string|null>(null),[query,setQuery]=useState("");
  const [selected,setSelected]=useState<FileEntry|null>(null),[draft,setDraft]=useState<FileDraft|null>(null),[incoming,setIncoming]=useState<FileDraft|null>(null);
  const draftRef=useRef<FileDraft|null>(null),sequence=useRef(0);
  const [error,setError]=useState(""),[message,setMessage]=useState(""),[loading,setLoading]=useState(false),[busy,setBusy]=useState(false),[source,setSource]=useState(false);
  const [modal,setModal]=useState<Modal|null>(null),dialog=useRef<HTMLDialogElement>(null);
  const [trash,setTrash]=useState<Array<{id:string;path:string;expires:number}>|null>(null),[trashCursor,setTrashCursor]=useState<string|null>(null);
  const uploadInput=useRef<HTMLInputElement>(null);
  const [upload,setUpload]=useState<{file:File;receipt:Transfer;root:string;path:string;offset:number}|null>(null);
  const uploadAbort=useRef<AbortController|null>(null),[uploading,setUploading]=useState(false);
  const currentRoot=roots.find(item=>item.id===root);
  const location=[...(currentRoot?.locations??[])].filter(item=>path===item.path||path.startsWith(item.path+"/")).sort((a,b)=>b.path.length-a.path.length)[0];
  const permission=location?.permission??currentRoot?.permission??"read_only";
  const writable=permission!=="read_only",mutable=permission==="read_write";
  const linked=linkedCorpus(currentRoot,path);
  const dirty=Boolean(draft&&draft.body!==draft.base);

  const keepDraft=useCallback((value:FileDraft|null)=>{
    draftRef.current=value;setDraft(value);
    if(value){try{if(value.body!==value.base)sessionStorage.setItem(draftKey(value.root,value.path),JSON.stringify(value));else sessionStorage.removeItem(draftKey(value.root,value.path));}
    catch{setError("브라우저에 수정안을 보관하지 못했습니다. 창을 닫기 전에 저장해 주세요.");}}
  },[]);
  useEffect(()=>{let active=true;hostCall<{roots:HostRoot[]}>("host_roots").then(result=>{if(active){setRoots(result.roots);if(!result.roots.some(item=>item.id==="workspace"))setRoot(result.roots[0]?.id??"workspace");}}).catch(error=>{if(active)setError(failure(error));});return()=>{active=false;};},[]);
  const load=useCallback(async(more?:string)=>{
    const id=++sequence.current;setLoading(true);setError("");
    try{const data=await hostCall<Listing>("host_files",{root,operation:"list",path,limit:100,...(more?{cursor:more}:{})});
      if(id===sequence.current){setEntries(old=>more?[...old,...data.entries]:data.entries);setCursor(data.next_cursor);}}
    catch(error){if(id===sequence.current)setError(failure(error));}finally{if(id===sequence.current)setLoading(false);}
  },[root,path]);
  useEffect(()=>{let active=true;queueMicrotask(()=>{if(active)void load();});return()=>{active=false;sequence.current++;};},[load]);
  useEffect(()=>{if(modal)dialog.current?.showModal();else dialog.current?.close();},[modal]);
  useEffect(()=>{const handler=(event:BeforeUnloadEvent)=>{if(draftRef.current&&draftRef.current.body!==draftRef.current.base)event.preventDefault();};window.addEventListener("beforeunload",handler);return()=>window.removeEventListener("beforeunload",handler);},[]);
  function navigate(next:string,nextRoot=root){sequence.current++;setRoot(nextRoot);setPath(next);setQuery("");setSelected(null);keepDraft(null);setIncoming(null);setTrash(null);setMessage("");}
  async function open(entry:FileEntry){
    if(entry.type==="directory"){navigate(entry.path);return;}
    if(entry.type!=="file")return;
    setSelected(entry);keepDraft(null);setIncoming(null);setError("");setSource(false);setMessage("");
    const id=++sequence.current;
    if((TEXT.test(entry.name)||entry.mime.startsWith("text/"))&&entry.bytes<=2*1024*1024){
      setLoading(true);
      try{const data=await hostCall<{files:Array<{content:string;version:string}>}>("host_read",{root,files:[{path:entry.path}],max_bytes:2097152});
        if(id!==sequence.current)return;
        const current={root,path:entry.path,base:data.files[0].content,body:data.files[0].content,version:data.files[0].version};
        let saved=current;try{saved=restoredDraft(sessionStorage.getItem(draftKey(root,entry.path)),current);}catch{}
        keepDraft(saved);if(saved.version!==current.version)setIncoming(current);
      }catch(error){if(id===sequence.current)setError(failure(error));}finally{if(id===sequence.current)setLoading(false);}
    }
  }
  async function save(){
    const value=draftRef.current;if(!value)return;setBusy(true);setError("");
    try{const result=await hostCall<{version:string}>("host_write",{root:value.root,path:value.path,content:value.body,expected_version:value.version});
      keepDraft({...value,base:value.body,version:result.version});setIncoming(null);setMessage("저장됨");void load();}
    catch(error){if((error as {code?:string}).code==="version_conflict"){
      const result=await hostCall<{files:Array<{content:string;version:string}>}>("host_read",{root:value.root,files:[{path:value.path}]}).catch(()=>null);
      if(result){setError("");setIncoming({...value,body:result.files[0].content,base:result.files[0].content,version:result.files[0].version});}
      else setError("다른 곳에서 파일이 변경되었습니다. 현재 수정안은 유지했습니다.");
    }else setError(failure(error));}finally{setBusy(false);}
  }
  async function rename(entry:FileEntry){setModal({kind:"move",title:"이름 변경·이동",value:entry.path,entry});}
  async function remove(entry:FileEntry){
    if(!confirm(entry.name+"을(를) 휴지통으로 옮길까요?"))return;
    setBusy(true);setError("");
    try{const stat=await hostCall<{version:string}>("host_files",{root,operation:"stat",path:entry.path});
      await hostCall("host_files",{root,operation:"trash",path:entry.path,expected_version:stat.version});
      if(selected?.path===entry.path){setSelected(null);keepDraft(null);}setMessage("휴지통으로 옮겼습니다.");void load();}
    catch(error){setError(failure(error));}finally{setBusy(false);}
  }
  async function submitModal(){
    if(!modal||!modal.value.trim())return;setBusy(true);setError("");
    try{
      const value=modal.value.trim();
      if(modal.kind==="move"&&modal.entry){const stat=await hostCall<{version:string}>("host_files",{root,operation:"stat",path:modal.entry.path});await hostCall("host_files",{root,operation:"move",path:modal.entry.path,destination:value,expected_version:stat.version});if(selected?.path===modal.entry.path){setSelected(null);keepDraft(null);}}
      else {if(value.includes("/")||value.includes("\\")||value==="."||value==="..")throw new Error("폴더 안에서 사용할 이름을 입력해 주세요.");
        const target=joined(path,value);
        if(modal.kind==="folder")await hostCall("host_files",{root,operation:"mkdir",path:target});
        else await hostCall("host_write",{root,path:target,content:modal.kind==="saveAs"?draftRef.current?.body??"":"",expected_version:"absent"});
      }
      setModal(null);setMessage("저장됨");void load();
    }catch(error){setError(failure(error));}finally{setBusy(false);}
  }
  async function showTrash(more?:string){
    setBusy(true);setError("");try{const result=await hostCall<{items:Array<{id:string;path:string;expires:number}>;next_cursor:string|null}>("host_files",{root,operation:"trash_list",limit:100,...(more?{cursor:more}:{})});setTrash(old=>more?[...(old??[]),...result.items]:result.items);setTrashCursor(result.next_cursor);}
    catch(error){setError(failure(error));}finally{setBusy(false);}
  }
  async function restore(id:string){setBusy(true);setError("");try{await hostCall("host_files",{root,operation:"restore",trash_id:id});await showTrash();void load();}catch(error){setError(failure(error));}finally{setBusy(false);}}
  async function runUpload(value:NonNullable<typeof upload>){
    const controller=new AbortController();uploadAbort.current=controller;setUploading(true);setError("");
    try{await uploadChunks(value.receipt,value.file,offset=>setUpload(old=>old?{...old,offset}:null),controller.signal);setUpload(null);setMessage("업로드 완료");void load();}
    catch(error){if(!controller.signal.aborted)setError(failure(error));}
    finally{setUploading(false);uploadAbort.current=null;}
  }
  async function startUpload(file:File){
    if(file.size>1073741824){setError("1 GiB 이하의 파일을 선택해 주세요.");return;}
    setBusy(true);setError("");
    try{const target=joined(path,file.name),existing=entries.find(entry=>entry.name===file.name);let version="absent";
      if(existing){if(!confirm(file.name+"을(를) 업로드한 파일로 교체할까요?"))return;version=(await hostCall<{version:string}>("host_files",{root,operation:"stat",path:target})).version;}
      const receipt=await hostCall<Transfer>("host_transfer",{root,path:target,direction:"upload",size:file.size,expected_version:version});
      const value={file,receipt,root,path:target,offset:0};setUpload(value);void runUpload(value);
    }catch(error){setError(failure(error));}finally{setBusy(false);}
  }
  async function cancelUpload(){if(!upload)return;uploadAbort.current?.abort();try{await ownerFetch("/api/transfers/"+upload.receipt.transfer_id+"/status",{method:"DELETE",headers:{"X-Toolkit-Transfer-Token":upload.receipt.token}});setUpload(null);}catch(error){setError(failure(error));}}
  const visible=entries.filter(item=>item.name.toLocaleLowerCase().includes(query.toLocaleLowerCase()));
  const parts=path==="."?[]:path.split("/");
  return <main className="file-workspace su-workspace su-stack" id="main-content">
    <div className="su-toolbar">
      <div className="su-row"><h1>작업공간</h1><FieldSelect aria-label="작업공간" value={root} onChange={option=>navigate(".",option.value)}>{roots.map(item=><option key={item.id} value={item.id}>{item.id==="workspace"?"Spark":item.id}</option>)}</FieldSelect></div>
      <div className="su-row"><Link href="/context">프로젝트 문서</Link><IconButton label="휴지통" onClick={()=>void showTrash()}><Trash2 size="1em"/></IconButton></div>
    </div>
    <nav className="file-breadcrumbs su-row" aria-label="폴더 경로"><button onClick={()=>navigate(".")}>Spark</button>{parts.map((part,i)=><span className="su-row" key={i}><span aria-hidden="true">/</span><button aria-current={i===parts.length-1?"location":undefined} onClick={()=>navigate(parts.slice(0,i+1).join("/"))}>{part}</button></span>)}</nav>
    {linked&&<div className="su-row file-linked"><Link href={"/context?space="+encodeURIComponent(linked.space_id)}>연결된 프로젝트 문서 열기</Link></div>}
    <div className="file-status" role="status">{error||message||(permission==="read_only"?"읽기 전용":permission==="create_only"?"새 파일만 만들 수 있습니다.":"")}</div>
    {upload&&<section className="su-panel su-stack" aria-label="파일 업로드"><div className="su-toolbar"><span>{upload.file.name} · {humanSize(upload.offset)} / {humanSize(upload.file.size)}</span><div className="su-row">{uploading?<UiButton onClick={()=>uploadAbort.current?.abort()}>일시정지</UiButton>:<UiButton onClick={()=>void runUpload(upload)}>이어 올리기</UiButton>}<UiButton onClick={()=>void cancelUpload()}>취소</UiButton></div></div><progress value={upload.offset} max={upload.file.size||1}/></section>}
    {trash!==null?<section className="su-stack"><div className="su-toolbar"><h2>휴지통</h2><UiButton onClick={()=>setTrash(null)}>파일로 돌아가기</UiButton></div><p className="file-muted">30일 동안 복원할 수 있습니다. Finder에서 직접 삭제한 파일은 포함되지 않습니다.</p>{trash.length===0?<p>휴지통이 비어 있습니다.</p>:trash.map(item=><div key={item.id} className="su-toolbar file-trash-row"><span>{item.path}<small className="file-muted"> · {new Date(item.expires*1000).toLocaleDateString("ko-KR")}까지</small></span><UiButton disabled={busy||!mutable} onClick={()=>void restore(item.id)}>복원</UiButton></div>)}{trashCursor&&<UiButton disabled={busy} onClick={()=>void showTrash(trashCursor)}>더 보기</UiButton>}</section>:
    <div className={"file-columns"+(selected?" has-selection":"")}>
      <section className="file-list-pane su-stack" aria-label="파일 목록">
        <div className="su-toolbar"><UiInput type="search" placeholder="이 폴더에서 찾기" aria-label="파일 이름 검색" value={query} onChange={event=>setQuery(event.target.value)}/><IconButton label="새로고침" disabled={loading} onClick={()=>void load()}><RefreshCw size="1em"/></IconButton></div>
        <div className="su-row"><UiButton disabled={!writable||busy||Boolean(upload)} onClick={()=>uploadInput.current?.click()}><Upload size="1em"/>업로드</UiButton><UiButton disabled={!writable||busy} onClick={()=>setModal({kind:"folder",title:"새 폴더",value:""})}><FolderPlus size="1em"/>새 폴더</UiButton><UiButton disabled={!writable||busy} onClick={()=>setModal({kind:"file",title:"새 파일",value:"새 문서.md"})}>새 파일</UiButton><input ref={uploadInput} type="file" hidden onChange={event=>{const file=event.target.files?.[0];if(file)void startUpload(file);event.target.value="";}}/></div>
        <ul className="file-list" aria-busy={loading}>{visible.map(entry=><li key={entry.path} className={selected?.path===entry.path?"selected":""}><button className="file-row" disabled={entry.type==="symlink"} onClick={()=>void open(entry)} aria-current={selected?.path===entry.path?"true":undefined}>{entry.type==="directory"?<Folder size="1.2em" aria-hidden/>:<File size="1.2em" aria-hidden/>}<span>{entry.name}<small>{entry.type==="directory"?"폴더":entry.type==="symlink"?"바로가기":humanSize(entry.bytes)}</small></span></button><div className="su-row file-row-actions"><IconButton label={entry.name+" 이름 변경·이동"} disabled={!mutable||busy||entry.type==="symlink"} onClick={()=>void rename(entry)}><Pencil size="1em"/></IconButton><IconButton label={entry.name+" 휴지통으로 이동"} disabled={!mutable||busy||entry.type==="symlink"} onClick={()=>void remove(entry)}><Trash2 size="1em"/></IconButton></div></li>)}</ul>
        {!loading&&visible.length===0&&<p className="file-muted">{query?"검색 결과가 없습니다.":"폴더가 비어 있습니다."}</p>}{cursor&&<UiButton disabled={loading} onClick={()=>void load(cursor)}>더 보기</UiButton>}
      </section>
      <section className="file-detail su-stack" aria-label="파일 내용">
        {selected?<><div className="su-toolbar"><div className="su-row"><IconButton label="파일 목록으로" onClick={()=>{setSelected(null);keepDraft(null);}}><ArrowLeft size="1em"/></IconButton><h2>{selected.name}</h2></div><div className="su-row"><a className="su-btn" href={fileUrl(root,selected.path)} download><Download size="1em" aria-hidden/>다운로드</a>{draft&&<UiButton disabled={!dirty||busy||!mutable||Boolean(incoming)} className="primary" onClick={()=>void save()}><Save size="1em"/>저장</UiButton>}</div></div>
        {incoming&&<section className="su-panel su-stack" role="alert"><p>다른 곳에서 파일이 바뀌었습니다. 수정안은 그대로 보관했습니다.</p><div className="su-row"><UiButton onClick={()=>setModal({kind:"saveAs",title:"수정안을 다른 이름으로 저장",value:selected.name.replace(/(\.[^.]+)?$/, "-수정안$1")})}>다른 이름으로 저장</UiButton><UiButton onClick={()=>{if(confirm("현재 수정안을 닫고 최신 파일을 열까요?")){keepDraft(incoming);setIncoming(null);}}}>최신 파일로 열기</UiButton></div><details><summary>서버의 최신 내용</summary><pre className="file-source">{incoming.body}</pre></details></section>}
        {loading&&!draft?<p>파일을 여는 중입니다.</p>:draft?<><div className="su-toolbar"><span className="file-muted">{dirty?"수정 중":""}</span>{(MARKDOWN.test(selected.name)||/\.html?$/i.test(selected.name))&&<UiButton disabled={/\.html?$/i.test(selected.name)&&dirty&&source} aria-pressed={source} onClick={()=>setSource(!source)}>{source?"문서 보기":"원문 편집"}</UiButton>}</div>{/\.html?$/i.test(selected.name)&&!source?<iframe className="file-preview" title={selected.name+" 미리보기"} src={fileUrl(root,selected.path,true)} sandbox=""/>:MARKDOWN.test(selected.name)&&!source?<article className="workspace-prose file-markdown"><InlineDocument body={draft.body} editable={mutable&&!busy} onChange={(body,expected)=>{const current=draftRef.current;if(!current||current.body!==expected)return false;keepDraft({...current,body});return true;}}/></article>:<UiTextarea className="file-source-editor" aria-label={selected.name+" 내용"} value={draft.body} disabled={!mutable||busy} onChange={event=>keepDraft({...draft,body:event.target.value})}/>}</>:
        RASTER.test(selected.mime)?<img className="file-image" src={fileUrl(root,selected.path,true)} alt={selected.name}/>:
        selected.mime==="application/pdf"||/\.html?$/i.test(selected.name)?<iframe className="file-preview" title={selected.name+" 미리보기"} src={fileUrl(root,selected.path,true)} sandbox=""/>:
        <div className="su-stack file-empty"><p>원본을 다운로드해 열 수 있습니다.</p>{/\.(docx?|xlsx?|pptx?|hwpx?)$/i.test(selected.name)&&<p className="file-muted">PDF 미리보기가 필요하면 에이전트에게 변환을 요청해 주세요.</p>}</div>}</>:<div className="file-empty file-muted"><File size={32} aria-hidden/><p>파일을 선택해 열어 보세요.</p></div>}
      </section>
    </div>}
    <dialog ref={dialog} className="su-dialog file-dialog" onCancel={()=>setModal(null)}><form className="su-stack" onSubmit={event=>{event.preventDefault();void submitModal();}}><div className="su-toolbar"><h2>{modal?.title}</h2><IconButton label="닫기" type="button" onClick={()=>setModal(null)}><X size="1em"/></IconButton></div><UiInput autoFocus aria-label={modal?.kind==="move"?"작업공간 기준 새 경로":"이름"} value={modal?.value??""} onChange={event=>setModal(old=>old?{...old,value:event.target.value}:null)}/>{modal?.kind==="move"&&<p className="file-muted">현재 작업공간 안의 경로를 입력해 주세요.</p>}<div className="su-row"><UiButton type="button" onClick={()=>setModal(null)}>취소</UiButton><UiButton type="submit" className="primary" disabled={busy||!modal?.value.trim()}>저장</UiButton></div></form></dialog>
  </main>;
}
