"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { ArrowLeft, Minus, Pencil, Plus, Printer, Save } from "lucide-react";
import { IconButton, UiButton } from "@/app/ui";
import { FlowResourceLink } from "@/components/flow/flow-resource-link";

export function LibraryReader({path,inline=false}:{path:string;inline?:boolean}) {
  const frame = useRef<HTMLIFrameElement>(null);
  const [editing,setEditing] = useState(false), [loaded,setLoaded] = useState(false), [failed,setFailed] = useState(false);
  const [status,setStatus] = useState(""), [hasDraft,setHasDraft] = useState(false);
  const [issueIdentity,setIssueIdentity] = useState<{path:string;id:string}|null>(null);
  const cleanup = useRef<()=>void>(()=>{});
  const editingRef = useRef(false);
  function setMode(value:boolean) {
    if (inline) value=false;
    editingRef.current=value; setEditing(value);
    const doc=frame.current?.contentDocument;
    if (!doc) return;
    doc.body.classList.toggle("toolkit-reader-editing",value);
    const nodes=doc.querySelectorAll<HTMLElement>("main h1,.reader-header .lead,.reader-header .standfirst,main article");
    nodes.forEach(node=>node.setAttribute("contenteditable",value?(node.tagName==="ARTICLE"?"true":"plaintext-only"):"false"));
    if(value)doc.querySelector<HTMLElement>("main article")?.focus();
  }
  function onLoad() {
    cleanup.current();
    const doc=frame.current?.contentDocument;
    if(!doc?.querySelector("main article")){setIssueIdentity(null);setFailed(true);setLoaded(false);return;}
    const issueId=doc.documentElement.dataset.libraryIssueId;
    setIssueIdentity(issueId?{path,id:issueId}:null);
    setLoaded(true); setFailed(false); setMode(editingRef.current);
    const theme=()=>{doc.documentElement.dataset.theme=document.documentElement.dataset.theme||"light";};
    theme();
    const themeObserver=new MutationObserver(theme);
    themeObserver.observe(document.documentElement,{attributes:true,attributeFilter:["data-theme"]});
    const reflect=()=>{
      setStatus(doc.querySelector<HTMLOutputElement>(".library-save-status")?.textContent||"");
      setHasDraft(doc.documentElement.hasAttribute("data-library-unsaved"));
    };
    const stateObserver=new MutationObserver(reflect);
    stateObserver.observe(doc.documentElement,{attributes:true,attributeFilter:["data-library-unsaved"]});
    const output=doc.querySelector(".library-save-status");
    if(output)stateObserver.observe(output,{childList:true,subtree:true,attributes:true});
    reflect();
    doc.querySelectorAll<HTMLAnchorElement>('a[href]:not([href^="#"])').forEach(link=>{
      if(inline){link.target="_blank";link.rel="noopener noreferrer";}
      else if(!link.target)link.target="_top";
    });
    cleanup.current=()=>{themeObserver.disconnect();stateObserver.disconnect();doc.dispatchEvent(new Event("toolkit-reader-dispose"));};
  }
  useEffect(()=>{
    const protectNavigation=(event:MouseEvent)=>{
      const link=(event.target as Element)?.closest?.("a[href]");
      if(!link||!frame.current?.contentDocument?.documentElement.hasAttribute("data-library-unsaved"))return;
      if(!window.confirm("저장하지 않은 수정이 있습니다. 이 화면을 나갈까요?")){
        event.preventDefault();event.stopImmediatePropagation();
      }
    };
    document.addEventListener("click",protectNavigation,true);
    return()=>{cleanup.current();document.removeEventListener("click",protectNavigation,true);};
  },[]);
  function readerButton(selector:string){frame.current?.contentDocument?.querySelector<HTMLButtonElement>(selector)?.click();}
  const Container=inline?"section":"main";
  return <Container className={"toolkit-reader-page"+(inline?" toolkit-reader-inline":"")}>
    <header className="toolkit-reader-toolbar">
      {!inline&&<Link href="/library" className="file-project-link"><ArrowLeft size={18}/>Library</Link>}
      {!inline&&<span className="toolkit-reader-status" role="status">{status}</span>}
      <div className="toolkit-page-tools">
        <FlowResourceLink reference={!inline&&loaded&&issueIdentity?.path===path?{kind:"library-issue",id:issueIdentity.id}:null}/>
        <IconButton label="글자 작게" disabled={!loaded} onClick={()=>readerButton("[data-reader-smaller]")}><Minus size="1em"/></IconButton>
        <IconButton label="글자 크게" disabled={!loaded} onClick={()=>readerButton("[data-reader-larger]")}><Plus size="1em"/></IconButton>
        <IconButton label="인쇄" disabled={!loaded} onClick={()=>frame.current?.contentWindow?.print()}><Printer size="1em"/></IconButton>
        {!inline&&(editing||hasDraft)&&<UiButton type="button" disabled={!loaded} onClick={()=>readerButton("[data-library-save]")}><Save size="1em"/>저장</UiButton>}
        {!inline&&<UiButton type="button" disabled={!loaded} aria-pressed={editing} onClick={()=>setMode(!editing)}><Pencil size="1em"/>{editing?"읽기":"편집"}</UiButton>}
      </div>
    </header>
    {!loaded&&!failed&&<p className="toolkit-reader-loading" role="status">발간물을 불러오는 중…</p>}
    {failed&&<div className="toolkit-reader-loading" role="alert"><p>발간물을 열지 못했습니다.</p><UiButton onClick={()=>{setFailed(false);frame.current?.contentWindow?.location.reload();}}>다시 시도</UiButton><Link href={inline?path:"/library"} target={inline?"_blank":undefined} rel={inline?"noopener noreferrer":undefined}>{inline?"원본 열기":"Library로 돌아가기"}</Link></div>}
    <iframe ref={frame} className="toolkit-reader-frame" title="발간호 본문" src={path+(inline?"?embedded=1&readonly=1":"?embedded=1")} onLoad={onLoad} hidden={failed}/>
  </Container>;
}
