"use client";

import { useEffect, useState, type ReactNode } from "react";
import { Button } from "@openai/apps-sdk-ui/components/Button";
import { HtmlContentView, MarkdownContent, TextContentView } from "@personal-agent/flow-surface";
import { ownerFetch } from "../../lib/owner-client";
import { readCanonical } from "../../lib/context";
import type { FlowArtifact, FlowContextLocator, FlowSource } from "../../lib/flow-content";
import type { FlowResourceSummary } from "../../lib/flow-resources";
import type { GuidanceSource } from "../../lib/guidance";
import type { ItemDetailResult } from "../../lib/journal";
import { JournalItemContent } from "../journal/journal-item-content";
import { LibraryReader } from "../library/library-reader";
import {FlowUIKitReference} from "./flow-uikit-reference";
import { FlowDesignReference } from "./flow-design-reference";
import { FlowHostFile } from "./flow-host-file";

export type FlowOpenResource = FlowResourceSummary | (FlowSource & {kind:"flow-source";detail:string});

function ReadableText({body,title}: {body:string;title:string}) {
  return body.trim()
    ? <MarkdownContent body={body} title={title} headingLevel={4} className="flow-context-document"/>
    : <p className="flow-muted">본문이 없습니다.</p>;
}

function SourceText({body,title,path}: {body:string;title:string;path?:string}) {
  return body.trim()
    ? <TextContentView body={body} path={path||title} title={title} headingLevel={4} className="flow-context-document"/>
    : <p className="flow-muted">본문이 없습니다.</p>;
}

function JournalRecord({id}: {id:string}) {
  const [detail,setDetail]=useState<ItemDetailResult|null>(null);
  const [error,setError]=useState(false);
  useEffect(()=>{
    const controller=new AbortController();
    void ownerFetch(`/api/journal/items/${encodeURIComponent(id)}`,{signal:controller.signal})
      .then(async response=>{
        if(!response.ok)throw new Error();
        const payload=await response.json() as {ok:boolean;result?:ItemDetailResult};
        if(!payload.ok||payload.result?.item.id!==id)throw new Error();
        if(!controller.signal.aborted)setDetail(payload.result);
      })
      .catch(()=>{if(!controller.signal.aborted)setError(true)});
    return()=>controller.abort();
  },[id]);
  if(error)return <p className="flow-muted" role="alert">기록을 열지 못했습니다. 원본에서 다시 확인해 주세요.</p>;
  if(!detail)return <p className="flow-muted" role="status">기록을 불러오는 중입니다.</p>;
  return <JournalItemContent detail={detail}/>;
}

function ContextDocument({locator}: {locator:FlowContextLocator}) {
  const [source,setSource]=useState<GuidanceSource|null>(null);
  const [error,setError]=useState(false);
  useEffect(()=>{
    let active=true;
    void readCanonical(locator).then(value=>{if(active)setSource(value)})
      .catch(()=>{if(active)setError(true)});
    return()=>{active=false};
  },[locator]);
  if(error)return <p className="flow-muted" role="alert">자료를 열지 못했습니다. 원본에서 다시 확인해 주세요.</p>;
  if(!source)return <p className="flow-muted" role="status">자료를 불러오는 중입니다.</p>;
  return locator.product==="source"
    ? <SourceText body={source.content.body} path={source.title} title={source.title}/>
    : <ReadableText body={source.content.body} title={source.title}/>;
}

function UserContext({resource}:{resource:Extract<FlowResourceSummary,{kind:"user-context"}>}) {
 const data=resource.context;
 if(!data)return <p role="alert">사용자 맥락을 열지 못했습니다.</p>;
 const subject=data.nodes.find(node=>node.node_id===resource.id)||data.predicates.find(item=>item.predicate_id===resource.id);
 const nodes=new Map(data.nodes.map(node=>[node.node_id,node.name]));
 const predicates=new Map(data.predicates.map(item=>[item.predicate_id,item.name]));
 return <div className="su-stack" data-gap="section">
  {subject?.description&&<ReadableText body={subject.description} title={resource.title}/>}
  {data.edges.length>0&&<dl className="flow-context-relations">{data.edges.map(edge=><div key={edge.edge_id}><dt>{nodes.get(edge.source_id)||edge.source_id} / {predicates.get(edge.predicate_id)||edge.predicate_id}</dt><dd>{nodes.get(edge.target_id)||edge.target_id}</dd></div>)}</dl>}
 </div>;
}

function ResourceBody({resource,renderSourceArtifact}: {resource:FlowOpenResource;renderSourceArtifact?:(artifact:FlowArtifact)=>ReactNode}) {
  if(resource.kind==="flow-source") {
    if(resource.artifact&&renderSourceArtifact)return renderSourceArtifact(resource.artifact);
    const path=resource.filePath||resource.path||"";
    return /\.html?$/i.test(path)
      ?<HtmlContentView body={resource.body??""} name={resource.title}/>
      :<SourceText body={resource.body??""} path={path} title={resource.title}/>;
  }
  if(resource.kind==="user-context")return <UserContext resource={resource}/>;
  if(resource.kind==="journal-item")return <JournalRecord key={resource.id} id={resource.id}/>;
  if(resource.kind==="context")return <ContextDocument key={resource.href} locator={resource.locator}/>;
  if(resource.kind==="uikit-asset")return <FlowUIKitReference key={resource.id+resource.revision} id={resource.id} revision={resource.revision}/>;
  if(resource.kind==="design-recipe")return <FlowDesignReference key={resource.id} id={resource.id}/>;
  if(resource.kind==="host-file")return <FlowHostFile key={resource.root+":"+resource.path} root={resource.root} path={resource.path}/>;
  const issuePath=/^\/editions\/[A-Za-z0-9/_-]+$/.test(resource.href)?resource.href:null;
  return issuePath?<LibraryReader key={resource.id} path={issuePath} inline/>:<p className="flow-muted">이 발간물은 여기서 열 수 없습니다. 원본을 확인해 주세요.</p>;
}

export function FlowResourceView({resource,onClose,action,hideTitle=false,renderSourceArtifact}: {resource:FlowOpenResource;onClose?:()=>void;action?:ReactNode;hideTitle?:boolean;renderSourceArtifact?:(artifact:FlowArtifact)=>ReactNode}) {
  return <section className="flow-resource-view" aria-label={resource.title}>
    <header className="flow-resource-view-head">
      <div><span>{resource.detail}</span>{!hideTitle&&<h3>{resource.title}</h3>}</div>
      <div className="flow-resource-view-actions">
        {resource.kind==="host-file"?<a href={resource.href} download>원본 받기</a>:resource.kind!=="flow-source"&&<a href={resource.href} target="_blank" rel="noopener noreferrer">원본 열기</a>}
        {action}
        {onClose&&<Button type="button" color="primary" variant="ghost" pill={false} size="sm" onClick={onClose}>닫기</Button>}
      </div>
    </header>
    <div className="flow-resource-view-body"><ResourceBody resource={resource} renderSourceArtifact={renderSourceArtifact}/></div>
  </section>;
}
