"use client";

import { validContextLocator } from "@personal-agent/flow-surface/resource-reference";

import { useEffect, useMemo, useState } from "react";
import { Button } from "@openai/apps-sdk-ui/components/Button";
import { Input } from "@openai/apps-sdk-ui/components/Input";
import { ownerFetch } from "../../lib/owner-client";
import { FlowFilePicker } from "./flow-file-picker";
import { contextResource, flowResourceIdentity, flowResourceKey, hostFileResource, journalResource, libraryResource } from "../../lib/flow-resources";
import { readLinked } from "../../lib/flow-resource-read";
import {readUIKitAssets} from "../../lib/flow-uikit-client";
import type {UIKitAsset} from "../../lib/flow-uikit";
import {uikitResource} from "../../lib/flow-resources";
import { readDesignRecipes } from "../../lib/flow-design-client";
import { candidates, groups } from "../../lib/context";
import type { Group } from "../../lib/context";
import { FieldSelect } from "../../app/ui";
import type { FlowResourceSummary } from "../../lib/flow-resources";
import type { FlowLinkedResource, FlowSource, FlowSourceCatalogItem } from "../../lib/flow-content";
import type { JournalItem } from "../../lib/journal";
import type { LibraryIssueSummary } from "../../lib/library";

type Kind = FlowLinkedResource["kind"] | "flow-library";

type LinkedResourcesProps = {
  workId:string;
  references:FlowLinkedResource[];
  sources:FlowSource[];
  sourceCatalog:FlowSourceCatalogItem[];
  sourceIds:string[];
  writable:boolean;
  busy:boolean;
  activeKey:string|null;
  activeSourceId:string|null;
  onOpen:(resource:FlowResourceSummary)=>void;
} & ({
  browseOnly:true;
  onOpenSource?:never;
  onChange?:never;
  onSourceChange?:never;
} | {
  browseOnly?:false;
  onOpenSource:(source:FlowSource)=>void;
  onChange:(next:FlowLinkedResource[])=>Promise<boolean>;
  onSourceChange:(next:string[])=>Promise<boolean>;
});

export function LinkedResources(props:LinkedResourcesProps) {
  const {workId,references,sources,sourceCatalog,sourceIds,writable,busy,activeKey,activeSourceId,onOpen}=props;
  const browseOnly=props.browseOnly===true;
  const [resolved,setResolved]=useState<Record<string,FlowResourceSummary|null>>({});
  const [picker,setPicker]=useState(false);
  const [kind,setKind]=useState<Kind>(sourceCatalog.length?"flow-library":"journal-item");
  const [query,setQuery]=useState("");
  const [journalItems,setJournalItems]=useState<JournalItem[]>([]);
  const [libraryItems,setLibraryItems]=useState<LibraryIssueSummary[]>([]);
  const [designItems,setDesignItems]=useState<UIKitAsset[]>([]);
  const [libraryMore,setLibraryMore]=useState(false);
  const [contextGroups,setContextGroups]=useState<Group[]>([]);
  const [contextGroupId,setContextGroupId]=useState("sense");
  const [contextItems,setContextItems]=useState<Awaited<ReturnType<typeof candidates>>>([]);
  const [loading,setLoading]=useState(false);
  const [error,setError]=useState("");
  const [choosing,setChoosing]=useState("");
  const referenceKeys=references.map(flowResourceKey).join("\n");
  useEffect(()=>{
    const controller=new AbortController();
    queueMicrotask(()=>{if(!controller.signal.aborted)setResolved({})});
    const designRecipes=references.some(reference=>reference.kind==="design-recipe")?readDesignRecipes(controller.signal):undefined;
    if(references.length) void Promise.all(references.map(async reference=>{
      try{return [flowResourceKey(reference),await readLinked(reference,controller.signal,designRecipes)] as const;}
      catch{return [flowResourceKey(reference),null] as const;}
    })).then(items=>{if(!controller.signal.aborted)setResolved(Object.fromEntries(items));});
    return()=>controller.abort();
  // A new work or a changed reference list requires fresh source reads.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  },[workId,referenceKeys]);

  useEffect(()=>{
    if(!picker||kind!=="journal-item")return;
    const controller=new AbortController();
    const timer=setTimeout(()=>{
      setLoading(true);setError("");setJournalItems([]);
      const params=new URLSearchParams({limit:"50"});
      if(query.trim())params.set("query",query.trim());
      void ownerFetch(`/api/journal/items?${params}`,{signal:controller.signal})
        .then(async response=>{
          if(!response.ok)throw new Error();
          const data=await response.json() as {ok:boolean;result?:{items:JournalItem[]}};
          if(!data.ok||!Array.isArray(data.result?.items))throw new Error();
          if(!controller.signal.aborted)setJournalItems(data.result.items);
        }).catch(()=>{if(!controller.signal.aborted){setJournalItems([]);setError("Journal 기록을 불러오지 못했습니다.");}})
        .finally(()=>{if(!controller.signal.aborted)setLoading(false)});
    },query.trim()?250:0);
    return()=>{clearTimeout(timer);controller.abort()};
  },[picker,kind,query]);

  useEffect(()=>{
    if(!picker||kind!=="library-issue")return;
    const controller=new AbortController();
    queueMicrotask(()=>{if(!controller.signal.aborted){setLoading(true);setError("");setLibraryItems([]);}});
    void ownerFetch("/api/library/issues?limit=200&offset=0",{signal:controller.signal})
      .then(async response=>{
        if(!response.ok)throw new Error();
        const data=await response.json() as {issues?:LibraryIssueSummary[]};
        if(!Array.isArray(data.issues))throw new Error();
        if(!controller.signal.aborted){setLibraryItems(data.issues);setLibraryMore(data.issues.length===200);}
      }).catch(()=>{if(!controller.signal.aborted){setLibraryItems([]);setError("Library 발간물을 불러오지 못했습니다.");}})
      .finally(()=>{if(!controller.signal.aborted)setLoading(false)});
    return()=>controller.abort();
  },[picker,kind]);

  useEffect(()=>{
    if(!picker||kind!=="uikit-asset")return;
    const controller=new AbortController();
    queueMicrotask(()=>{if(!controller.signal.aborted){setLoading(true);setError("");setDesignItems([]);}});
    void readUIKitAssets(controller.signal)
      .then(items=>{if(!controller.signal.aborted)setDesignItems(items);})
      .catch(()=>{if(!controller.signal.aborted){setDesignItems([]);setError("UIKit 자료를 불러오지 못했습니다.");}})
      .finally(()=>{if(!controller.signal.aborted)setLoading(false)});
    return()=>controller.abort();
  },[picker,kind]);

  useEffect(()=>{
    if(!picker||kind!=="context")return;
    let active=true;
    queueMicrotask(()=>{if(active){setLoading(true);setError("");setContextGroups([]);setContextItems([]);}});
    void groups().then(items=>{
      if(active){setContextGroups(items);setContextGroupId(current=>items.some(item=>item.id===current)?current:items[0]?.id??"sense");}
    }).catch(()=>{if(active)setError("Sense·Corpus 자료를 불러오지 못했습니다.");})
      .finally(()=>{if(active)setLoading(false)});
    return()=>{active=false};
  },[picker,kind]);
  useEffect(()=>{
    if(!picker||kind!=="context")return;
    const group=contextGroups.find(item=>item.id===contextGroupId);
    if(!group)return;
    let active=true;
    const timer=setTimeout(()=>{
      setLoading(true);setError("");setContextItems([]);
      void candidates(group,query.trim()).then(items=>{if(active)setContextItems(items.filter(item=>validContextLocator(item.locator)));})
        .catch(()=>{if(active){setContextItems([]);setError("자료를 찾지 못했습니다. 다시 시도해 주세요.");}})
        .finally(()=>{if(active)setLoading(false)});
    },query.trim()?250:0);
    return()=>{active=false;clearTimeout(timer)};
  },[picker,kind,contextGroups,contextGroupId,query]);

  async function moreLibrary() {
    if(loading)return;
    setLoading(true);setError("");
    try {
      const response=await ownerFetch(`/api/library/issues?limit=200&offset=${libraryItems.length}`);
      if(!response.ok)throw new Error();
      const data=await response.json() as {issues?:LibraryIssueSummary[]};
      if(!Array.isArray(data.issues))throw new Error();
      setLibraryItems(current=>[...current,...data.issues!]);
      setLibraryMore(data.issues.length===200);
    } catch {setError("발간물을 더 불러오지 못했습니다.");}
    finally {setLoading(false);}
  }
  async function change(next:FlowLinkedResource[],choice:string) {
    if(busy||choosing||props.browseOnly)return;
    setChoosing(choice);
    try {if(await props.onChange(next))setPicker(false);}
    finally {setChoosing("");}
  }
  async function changeSource(next:string[],choice:string) {
    if(busy||choosing||props.browseOnly)return;
    setChoosing(choice);
    try {if(await props.onSourceChange(next))setPicker(false);}
    finally {setChoosing("");}
  }
  const libraryChoices=useMemo(()=>sourceCatalog.filter(item=>item.title.toLocaleLowerCase("ko").includes(query.toLocaleLowerCase("ko")) || (item.collection??"").toLocaleLowerCase("ko").includes(query.toLocaleLowerCase("ko"))),[sourceCatalog,query]);
  const choices=useMemo(()=>kind==="host-file"||kind==="flow-library"?[]:kind==="journal-item"
    ? journalItems.map(journalResource)
    :kind==="library-issue"
      ? libraryItems.filter(item=>item.title.toLocaleLowerCase("ko").includes(query.toLocaleLowerCase("ko"))).map(libraryResource)
      :kind==="uikit-asset"
        ? designItems.filter(item=>(item.title+" "+item.description).toLocaleLowerCase("ko").includes(query.toLocaleLowerCase("ko"))).map(uikitResource)
      :contextItems.map(item=>contextResource(item.locator,item.title)),
    [kind,journalItems,libraryItems,designItems,contextItems,query]);
  const selected=new Set(references.map(flowResourceKey));
  const selectedSources=new Set(sourceIds);
  const sourceById=new Map(sources.map(source=>[source.id,source]));

  return <section className="flow-linked-resources" aria-label={browseOnly?"자료 찾기":"연결한 자료"}>
    {(sourceIds.length>0||references.length>0)&&<ul>{sourceIds.map(id=>{
      const source=sourceById.get(id);
      return <li key={"source:"+id} className="flow-linked-item">
        {source?<button type="button" className="flow-linked-open" aria-pressed={activeSourceId===id} onClick={()=>{if(!props.browseOnly)props.onOpenSource(source)}}><span>{source.collection||source.kind||"자료"}</span><strong>{source.title}</strong></button>:<div><span>Flow 보관함</span><strong>자료를 찾지 못했습니다.</strong></div>}
        {writable&&<Button color="primary" variant="ghost" pill={false} size="sm" disabled={busy||Boolean(choosing)} onClick={()=>void changeSource(sourceIds.filter(item=>item!==id),id)}>연결 해제</Button>}
      </li>;
    })}{references.map(reference=>{
      const summary=resolved[flowResourceKey(reference)];
      return <li key={flowResourceKey(reference)} className="flow-linked-item">
        {summary?<button type="button" className="flow-linked-open" aria-pressed={activeKey===flowResourceKey(reference)} onClick={()=>onOpen(summary)}><span>{summary.detail}</span><strong>{summary.title}</strong></button>:<div><span>{reference.kind==="journal-item"?"Journal":reference.kind==="library-issue"?"Library":reference.kind==="uikit-asset"?"UIKit":reference.kind==="design-recipe"?"Design":reference.kind==="host-file"?"파일":"Sense·Corpus"}</span><strong>{summary===null?"현재 내용을 열 수 없습니다.":"불러오는 중"}</strong></div>}
        {writable&&<Button color="primary" variant="ghost" pill={false} size="sm" disabled={busy||Boolean(choosing)} onClick={()=>void change(references.filter(item=>flowResourceKey(item)!==flowResourceKey(reference)),flowResourceKey(reference))}>연결 해제</Button>}
      </li>;
    })}</ul>}
    {(writable||browseOnly)&&<Button color="primary" variant="ghost" pill={false} size="sm" disabled={busy||Boolean(choosing)} aria-expanded={picker} onClick={()=>{setPicker(value=>!value);setQuery("");setError("")}}>{picker?"닫기":browseOnly?"자료 찾기":"자료 연결"}</Button>}
    {picker&&<div className="flow-resource-picker">
      <FieldSelect aria-label="자료 종류" value={kind} onChange={option=>{setKind(option.value as Kind);setQuery("");setError("");setLoading(false)}}>
        {!browseOnly&&<option value="flow-library">Flow 보관함</option>}
        <option value="journal-item">Journal</option>
        <option value="library-issue">Library 발간물</option>
        <option value="context">Sense·Corpus</option>
        <option value="uikit-asset">UIKit</option>
        <option value="host-file">파일</option>
      </FieldSelect>
      {kind==="host-file"?<FlowFilePicker selected={selected} busy={busy||Boolean(choosing)} onPick={(root,path)=>{const item=hostFileResource(root,path);if(browseOnly){onOpen(item);setPicker(false)}else void change([...references,flowResourceIdentity(item)],flowResourceKey(item))}}/>:null}
      {kind==="context"&&contextGroups.length>0&&<FieldSelect aria-label="자료 범위" value={contextGroupId} onChange={option=>{setContextGroupId(option.value);setQuery("")}}>{contextGroups.map(group=><option key={group.id} value={group.id}>{group.title}</option>)}</FieldSelect>}
      {kind!=="host-file"&&<><label>{kind==="flow-library"?"보관함 검색":kind==="journal-item"?"기록 찾기":kind==="library-issue"?"발간물 찾기":kind==="uikit-asset"?"디자인 자료 찾기":"자료 찾기"}<Input type="search" value={query} onChange={event=>setQuery(event.target.value)}/></label>
      {kind==="flow-library" ? (!libraryChoices.length?<p className="flow-muted">{query?"검색 결과가 없습니다.":"보관한 자료가 없습니다."}</p>:<ul className="flow-resource-options">{libraryChoices.map(item=><li key={item.id}><button type="button" disabled={busy||Boolean(choosing)||selectedSources.has(item.id)} onClick={()=>void changeSource([...sourceIds,item.id],item.id)}><span>{item.collection||item.kind||"자료"}</span><strong>{item.title}</strong>{selectedSources.has(item.id)&&<small>연결됨</small>}</button></li>)}</ul>) : <>
        {error&&<p className="flow-error" role="alert">{error}</p>}
        {loading&&!choices.length?<p className="flow-muted">불러오는 중입니다.</p>:!choices.length&&!error?<p className="flow-muted">찾은 자료가 없습니다.</p>:<ul className="flow-resource-options">{choices.map(item=><li key={flowResourceKey(item)}><button type="button" disabled={busy||Boolean(choosing)||selected.has(flowResourceKey(item))} onClick={()=>{if(browseOnly){onOpen(item);setPicker(false)}else void change([...references,flowResourceIdentity(item)],flowResourceKey(item))}}><span>{item.detail}</span><strong>{item.title}</strong>{selected.has(flowResourceKey(item))&&<small>연결됨</small>}</button></li>)}</ul>}
        {kind==="library-issue"&&libraryMore&&<Button color="primary" variant="ghost" pill={false} size="sm" disabled={loading} onClick={()=>void moreLibrary()}>더 보기</Button>}
      </>}</>}
    </div>}
  </section>;
}
