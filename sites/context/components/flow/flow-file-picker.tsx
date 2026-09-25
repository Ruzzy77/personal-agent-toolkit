"use client";

import { useEffect, useRef, useState } from "react";
import { Button } from "@openai/apps-sdk-ui/components/Button";
import { Input } from "@openai/apps-sdk-ui/components/Input";
import { Switch } from "@openai/apps-sdk-ui/components/Switch";
import { FieldSelect } from "../../app/ui";
import { workspaceFilePresentation, workspaceMediaType } from "@personal-agent/flow-surface";
import { displayedFiles, fileKind } from "../../lib/file-list";
import { hostCall, type FileEntry, type HostRoot } from "../../lib/host-files";
import { flowResourceKey } from "../../lib/flow-resources";

type Listing={entries:FileEntry[];next_cursor:string|null};

export function FlowFilePicker({selected,busy,onPick,rootId,imagesOnly=false,accept}:{
  selected:Set<string>;busy:boolean;onPick:(root:string,path:string)=>void;
  rootId?:string;imagesOnly?:boolean;accept?:"image"|"video"|"audio"|"file";
}) {
  const [roots,setRoots]=useState<HostRoot[]>([]);
  const [root,setRoot]=useState("");
  const [directory,setDirectory]=useState(".");
  const [entries,setEntries]=useState<FileEntry[]>([]);
  const [cursor,setCursor]=useState<string|null>(null);
  const [query,setQuery]=useState("");
  const [showHidden,setShowHidden]=useState(false);
  const [loading,setLoading]=useState(false);
  const [error,setError]=useState("");
  const request=useRef(0);

  useEffect(()=>{
    let active=true;
    void hostCall<{roots:HostRoot[]}>("host_roots").then(data=>{
      if(!active)return;
      const available=rootId?data.roots.filter(item=>item.id===rootId):data.roots;
      setRoots(available);
      setRoot(available[0]?.id??"");
      if(!available.length)setError("등록된 파일 작업공간을 찾지 못했습니다.");
    }).catch(()=>{if(active)setError("작업공간을 불러오지 못했습니다.")});
    return()=>{active=false};
  },[rootId]);
  useEffect(()=>{
    if(!root)return;
    const number=++request.current;
    queueMicrotask(()=>{if(number===request.current){setLoading(true);setError("");setEntries([]);setCursor(null)}});
    void hostCall<Listing>("host_files",{root,operation:"list",path:directory,limit:100}).then(data=>{
      if(number===request.current){setEntries(data.entries);setCursor(data.next_cursor);}
    }).catch(()=>{if(number===request.current)setError("폴더를 열지 못했습니다.")})
      .finally(()=>{if(number===request.current)setLoading(false)});
    return()=>{request.current++};
  },[root,directory]);

  async function more(){
    if(!cursor||loading)return;
    const number=request.current;
    setLoading(true);setError("");
    try{
      const data=await hostCall<Listing>("host_files",{root,operation:"list",path:directory,limit:100,cursor});
      if(number===request.current){setEntries(current=>[...current,...data.entries]);setCursor(data.next_cursor);}
    }catch{if(number===request.current)setError("파일을 더 불러오지 못했습니다.")}
    finally{if(number===request.current)setLoading(false)}
  }
  function chooseRoot(value:string){setRoot(value);setDirectory(".");setQuery("");}
  function browse(path:string){setDirectory(path);setQuery("");}
  const parts=directory==="."?[]:directory.split("/");
  const fileType=accept??(imagesOnly?"image":null);
  const imageFile=/\.(?:png|jpe?g|webp|gif)$/i;
  const visible=displayedFiles(entries,query,fileType?false:showHidden,false).filter(entry=>
    !fileType||(entry.type==="directory"&&entry.name!=="node_modules")||
    (entry.type==="file"&&(fileType==="file"?Boolean(workspaceFilePresentation(entry.path)):fileType==="image"?imageFile.test(entry.name):workspaceMediaType(entry.path)?.kind===fileType))
  );
  return <div className="flow-file-picker">
    {roots.length>1&&<FieldSelect aria-label="작업공간" value={root} onChange={option=>chooseRoot(option.value)}>{roots.map(item=><option key={item.id} value={item.id}>{item.id==="workspace"?"Spark":item.id}</option>)}</FieldSelect>}
    <nav className="flow-file-breadcrumbs" aria-label="폴더 경로">
      <button type="button" onClick={()=>browse(".")}>{root==="workspace"?"Spark":root||"작업공간"}</button>
      {parts.map((part,index)=><span key={index}><span aria-hidden="true">/</span><button type="button" aria-current={index===parts.length-1?"location":undefined} onClick={()=>browse(parts.slice(0,index+1).join("/"))}>{part}</button></span>)}
    </nav>
    <label>파일 찾기<Input type="search" value={query} onChange={event=>setQuery(event.target.value)} placeholder={cursor?"불러온 파일에서 찾기":"현재 폴더에서 찾기"}/></label>
    {!fileType&&<Switch checked={showHidden} onCheckedChange={setShowHidden} label="숨김 파일 보기"/>}
    {error&&<p className="flow-error" role="alert">{error}</p>}
    {loading&&!entries.length?<p className="flow-muted" role="status">파일을 불러오는 중입니다.</p>:
      visible.length===0&&!error?<p className="flow-muted">{query?"찾은 파일이 없습니다.":"폴더가 비어 있습니다."}</p>:
      <ul className="flow-resource-options">{visible.map(entry=>{
        const linked=selected.has(flowResourceKey({kind:"host-file",root,path:entry.path}));
        const limit=fileType==="file"?workspaceFilePresentation(entry.path)?.maxBytes??20*1024*1024:20*1024*1024;
        const tooLarge=Boolean(fileType)&&entry.type==="file"&&entry.bytes>limit;
        return <li key={entry.path}><button type="button" disabled={busy||entry.type==="symlink"||linked||tooLarge}
          onClick={()=>entry.type==="directory"?browse(entry.path):onPick(root,entry.path)}>
          <span>{fileKind(entry)}</span><strong>{entry.name}</strong>{linked&&<small>연결됨</small>}{tooLarge&&<small>{limit===512*1024?"512KB 초과":"20MB 초과"}</small>}
        </button></li>;
      })}</ul>}
    {cursor&&<Button color="primary" variant="ghost" pill={false} size="sm" disabled={loading} onClick={()=>void more()}>더 보기</Button>}
  </div>;
}
