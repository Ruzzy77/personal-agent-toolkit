"use client";
import {ListItemAction} from "@personal-agent/flow-surface";

import { useEffect, useRef, useState } from "react";
import { Button } from "@openai/apps-sdk-ui/components/Button";
import { Input } from "@openai/apps-sdk-ui/components/Input";
import { Menu, IconButton } from "../../app/ui";
import { ArrowUp, ChevronRight, Ellipsis, File, Folder, HardDrive, Link2, Search } from "lucide-react";
import { workspaceFilePresentation, workspaceMediaType } from "@personal-agent/flow-surface";
import { displayedFiles, fileBrowserRoot, fileKind } from "../../lib/file-list";
import { hostCall, type FileEntry, type HostRoot } from "../../lib/host-files";
import { flowResourceKey } from "../../lib/flow-resources";

type Listing={entries:FileEntry[];next_cursor:string|null};

export function FlowFilePicker({selected,busy,onPick,current,rootId,imagesOnly=false,accept}:{
  selected:Set<string>;busy:boolean;onPick:(root:string,path:string,trigger:HTMLButtonElement)=>void;
  current?:{root:string;path:string};rootId?:string;imagesOnly?:boolean;accept?:"image"|"video"|"audio"|"file";
}) {
  const [location,setLocation]=useState<ReturnType<typeof fileBrowserRoot>>(null);
  const root=location?.id??"";
  const [directory,setDirectory]=useState(".");
  const [entries,setEntries]=useState<FileEntry[]>([]);
  const [cursor,setCursor]=useState<string|null>(null);
  const [query,setQuery]=useState("");
  const [showHidden,setShowHidden]=useState(false);
  const [loading,setLoading]=useState(true);
  const [error,setError]=useState("");
  const request=useRef(0);

  useEffect(()=>{
    let active=true;
    void hostCall<{roots:HostRoot[]}>("host_roots").then(data=>{
      if(!active)return;
      const next=fileBrowserRoot(data.roots,rootId);
      setDirectory(".");setQuery("");setLocation(next);
      if(!next){setLoading(false);setError("파일 위치를 확인하지 못했습니다.");}
    }).catch(()=>{if(active){setLoading(false);setError("파일 목록에 연결하지 못했습니다.");}});
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
  function browse(path:string){setDirectory(path);setQuery("");}
  const parts=directory==="."?[]:directory.split("/");
  const fileType=accept??(imagesOnly?"image":null);
  const imageFile=/\.(?:png|jpe?g|webp|gif)$/i;
  const visible=displayedFiles(entries,query,fileType?false:showHidden,false).filter(entry=>
    !fileType||(entry.type==="directory"&&entry.name!=="node_modules")||
    (entry.type==="file"&&(fileType==="file"?Boolean(workspaceFilePresentation(entry.path)):fileType==="image"?imageFile.test(entry.name):workspaceMediaType(entry.path)?.kind===fileType))
  );
  const rootName=location?.label??"파일";
  return <div className="flow-file-picker su-stack">
    <div className="flow-file-toolbar">
      <div className="flow-file-location">
        <IconButton size="md" label="상위 폴더" disabled={!parts.length||loading} onClick={()=>browse(parts.slice(0,-1).join("/")||".")}><ArrowUp size="1em" aria-hidden="true"/></IconButton>
        <nav className="flow-file-breadcrumbs" aria-label="폴더 경로">
          <Button type="button" color="primary" variant="ghost" size="md" pill={false} className="flow-file-root" disabled={!root} aria-current={!parts.length?"location":undefined} onClick={()=>browse(".")}><HardDrive size="1em" aria-hidden="true"/><span className="flow-file-root-name">{rootName}</span></Button>
          {parts.map((part,index)=><span className="flow-file-crumb" key={index}><ChevronRight size="1em" aria-hidden="true"/>{index===parts.length-1?<span aria-current="location">{part}</span>:<Button type="button" color="primary" variant="ghost" size="md" pill={false} onClick={()=>browse(parts.slice(0,index+1).join("/"))}><span className="flow-file-root-name" title={part}>{part}</span></Button>}</span>)}
        </nav>
      </div>
      <div className="flow-file-tools">
        <div className="flow-file-search"><Input type="search" aria-label="파일 찾기" value={query} onChange={event=>setQuery(event.target.value)} placeholder={cursor?"불러온 파일에서 찾기":"현재 폴더에서 찾기"} startAdornment={<Search size="1em" aria-hidden="true"/>}/></div>
        {!fileType&&<Menu><Menu.Trigger><IconButton size="md" label="파일 보기 옵션" selected={showHidden}><Ellipsis size="1em" aria-hidden="true"/></IconButton></Menu.Trigger><Menu.Content align="end" minWidth={180}><Menu.CheckboxItem checked={showHidden} onCheckedChange={value=>setShowHidden(value===true)} indicatorVariant="ghost">숨김 파일 보기</Menu.CheckboxItem></Menu.Content></Menu>}
      </div>
    </div>
    {error&&<p className="flow-error" role="alert">{error}</p>}
    {loading&&!entries.length?<p className="flow-muted" role="status">파일을 불러오는 중입니다.</p>:
      visible.length===0&&!error?<p className="flow-muted">{query?"찾은 파일이 없습니다.":"폴더가 비어 있습니다."}</p>:
      <ul className="flow-file-list" aria-label="파일 목록" aria-busy={loading}>{visible.map(entry=>{
        const linked=selected.has(flowResourceKey({kind:"host-file",root,path:entry.path}));
        const limit=fileType==="file"?workspaceFilePresentation(entry.path)?.maxBytes??20*1024*1024:20*1024*1024;
        const tooLarge=Boolean(fileType)&&entry.type==="file"&&entry.bytes>limit;
        const EntryIcon=entry.type==="directory"?Folder:entry.type==="symlink"?Link2:File;
        return <li key={entry.path}><ListItemAction label={entry.name} icon={<EntryIcon size="1em" aria-hidden="true"/>} aria-label={fileKind(entry)+": "+entry.name+(linked?", 연결됨":"")+(tooLarge?", 크기 제한 초과":"")} aria-current={current?.root===root&&current.path===entry.path?"true":undefined} disabled={busy||entry.type==="symlink"||linked||tooLarge}
          onClick={(event:React.MouseEvent<HTMLButtonElement>)=>entry.type==="directory"?browse(entry.path):onPick(root,entry.path,event.currentTarget)}>
          {linked&&<small>연결됨</small>}{tooLarge&&<small>{limit===512*1024?"512KB 초과":"20MB 초과"}</small>}
        </ListItemAction></li>;
      })}</ul>}
    {cursor&&<Button color="primary" variant="ghost" pill={false} size="sm" disabled={loading} onClick={()=>void more()}>더 보기</Button>}
  </div>;
}
