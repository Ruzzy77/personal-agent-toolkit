"use client";

import { useEffect, useRef, useState } from "react";
import { Button } from "@openai/apps-sdk-ui/components/Button";
import { Input } from "@openai/apps-sdk-ui/components/Input";
import { hostCall } from "../../lib/host-files";
import type { FlowSource, FlowSourceCatalogItem } from "../../lib/flow-content";

type SnapshotPage = { sources:FlowSourceCatalogItem[];nextOffset:number|null };

export function SavedFlowLibrary({workspaceId,busy,refreshKey,onOpen}: {
  workspaceId:string;busy:boolean;refreshKey:number;onOpen:(source:FlowSource)=>void;
}) {
  const [expanded,setExpanded]=useState(false);
  const [query,setQuery]=useState("");
  const [sources,setSources]=useState<FlowSourceCatalogItem[]>([]);
  const [nextOffset,setNextOffset]=useState<number|null>(null);
  const [loading,setLoading]=useState(false);
  const [loadingMore,setLoadingMore]=useState(false);
  const [opening,setOpening]=useState("");
  const [error,setError]=useState("");
  const readers=useRef(new Set<AbortController>());
  const pages=useRef(new Set<AbortController>());

  useEffect(()=>{
    const activeReaders=readers.current;
    const activePages=pages.current;
    return()=>{
      for(const controller of activeReaders)controller.abort();
      for(const controller of activePages)controller.abort();
    };
  },[]);
  useEffect(()=>{
    if(expanded)return;
    for(const controller of readers.current)controller.abort();
  },[expanded]);
  useEffect(()=>{
    if(!expanded)return;
    const controller=new AbortController();
    const activePages=pages.current;
    queueMicrotask(()=>{if(!controller.signal.aborted){setLoading(true);setSources([]);setNextOffset(null);setError("")}});
    const timer=setTimeout(()=>{
      void hostCall<SnapshotPage>("flow_snapshot_list",{workspace_id:workspaceId,query:query.trim(),limit:50},controller.signal)
        .then(result=>{if(!controller.signal.aborted){setSources(result.sources);setNextOffset(result.nextOffset)}})
        .catch(()=>{if(!controller.signal.aborted)setError("보관한 작업물을 불러오지 못했습니다.")})
        .finally(()=>{if(!controller.signal.aborted)setLoading(false)});
    },query.trim()?250:0);
    return()=>{clearTimeout(timer);controller.abort();for(const page of activePages)page.abort()};
  },[expanded,workspaceId,query,refreshKey]);

  async function more(){
    if(busy||loading||loadingMore||nextOffset===null)return;
    const controller=new AbortController();
    pages.current.add(controller);
    setLoadingMore(true);setError("");
    try{
      const result=await hostCall<SnapshotPage>("flow_snapshot_list",{workspace_id:workspaceId,query:query.trim(),offset:nextOffset,limit:50},controller.signal);
      if(!controller.signal.aborted){setSources(current=>[...current,...result.sources]);setNextOffset(result.nextOffset)}
    }catch{if(!controller.signal.aborted)setError("보관한 작업물을 더 불러오지 못했습니다.")}
    finally{pages.current.delete(controller);setLoadingMore(false)}
  }
  async function open(id:string){
    if(busy||opening)return;
    const controller=new AbortController();
    readers.current.add(controller);
    setOpening(id);setError("");
    try{
      const result=await hostCall<{source:FlowSource}>("flow_snapshot_read",{workspace_id:workspaceId,source_id:id},controller.signal);
      if(!controller.signal.aborted&&result.source.id===id)onOpen(result.source);
    }catch{if(!controller.signal.aborted)setError("보관한 작업물을 열지 못했습니다.")}
    finally{readers.current.delete(controller);setOpening("")}
  }
  return <section className="flow-saved-list" aria-label="보관한 작업물">
    <div className="flow-saved-head"><h2>보관한 작업물</h2><Button type="button" color="primary" variant="ghost" pill={false} size="sm" disabled={busy} aria-expanded={expanded} onClick={()=>setExpanded(value=>!value)}>{expanded?"닫기":"보기"}</Button></div>
    {expanded&&<>
      <label>보관함 검색<Input type="search" value={query} onChange={event=>setQuery(event.target.value)} disabled={loadingMore||Boolean(opening)} placeholder="작업물 찾기"/></label>
      {error&&<p className="flow-error" role="alert">{error}</p>}
      {loading?<p className="flow-muted" role="status">보관한 작업물을 불러오는 중입니다.</p>
        :!sources.length&&!error?<p className="flow-muted">{query?"찾은 작업물이 없습니다.":"보관한 작업물이 없습니다."}</p>
        :<ul className="flow-resource-options">{sources.map(source=><li key={source.id}><button type="button" disabled={busy||Boolean(opening)} onClick={()=>void open(source.id)}><strong>{source.title}</strong>{source.artifactRevision!==undefined&&<span>버전 {source.artifactRevision+1}</span>}</button></li>)}</ul>}
      {nextOffset!==null&&!loading&&<Button type="button" color="primary" variant="ghost" pill={false} size="sm" disabled={busy||loadingMore||Boolean(opening)} onClick={()=>void more()}>{loadingMore?"불러오는 중":"더 보기"}</Button>}
    </>}
  </section>;
}
