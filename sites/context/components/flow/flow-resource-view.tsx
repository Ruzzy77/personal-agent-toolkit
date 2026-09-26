"use client";

import { useEffect, useState, type ReactNode } from "react";
import { Button,ButtonLink } from "@openai/apps-sdk-ui/components/Button";
import { Download,ExternalLink } from "lucide-react";
import { HtmlContentView, MarkdownContent, TextContentView } from "@personal-agent/flow-surface";
import { ownerFetch } from "../../lib/owner-client";
import type { FlowArtifact, FlowSource } from "../../lib/flow-content";
import type { FlowResourceSummary } from "../../lib/flow-resources";
import type { ItemDetailResult } from "../../lib/journal";
import { JournalItemContent } from "../journal/journal-item-content";
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

function Publication({body,title}:{body:string;title:string}){
 const [content,setContent]=useState<string|null>(null),[error,setError]=useState(""),[attempt,setAttempt]=useState(0);
 useEffect(()=>{
  const controller=new AbortController();
  async function load(){
   const doc=new DOMParser().parseFromString(body,"text/html"),images=[...doc.querySelectorAll("img[src]")];
   let bytes=0;
   const cache=new Map<string,string>();
   for(const image of images){
    const src=image.getAttribute("src")||"";
    if(!/^\/media\/[A-Za-z0-9/_%.+-]+$/.test(src))continue;
    if(!cache.has(src)){
     const response=await ownerFetch(src,{signal:controller.signal});
     if(!response.ok)throw new Error("발간물의 이미지를 열지 못했습니다.");
     const blob=await response.blob();bytes+=blob.size;
     if(bytes>24*1024*1024||!/^image\/(png|jpeg|webp|gif)$/.test(blob.type))throw new Error("발간물의 이미지를 확인하지 못했습니다.");
     const data=await new Promise<string>((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result));reader.onerror=reject;reader.readAsDataURL(blob)});
     cache.set(src,data);
    }
    image.setAttribute("src",cache.get(src)!);image.removeAttribute("srcset");
   }
   if(!controller.signal.aborted)setContent("<!doctype html>"+doc.documentElement.outerHTML);
  }
  void Promise.resolve().then(()=>{if(controller.signal.aborted)return;setContent(null);setError("");return load()}).catch(e=>{if(!controller.signal.aborted)setError(e instanceof Error?e.message:"발간물을 열지 못했습니다.")});
  return()=>controller.abort();
 },[body,attempt]);
 if(error)return <div><p role="alert">{error}</p><Button color="primary" variant="ghost" size="md" pill={false} onClick={()=>setAttempt(value=>value+1)}>다시 열기</Button></div>;
 return content===null?<p role="status">발간물을 여는 중입니다.</p>:<HtmlContentView body={content} name={title} showSourceToggle={false}/>;
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
  if(resource.kind==="journal-item")return resource.resolved?<JournalItemContent detail={JSON.parse(resource.resolved.body) as ItemDetailResult}/>:<p role="alert">기록을 다시 열어 주세요.</p>;
  if(resource.kind==="context")return resource.resolved?(resource.resolved.format==="text"?<SourceText body={resource.resolved.body} title={resource.title}/>:<ReadableText body={resource.resolved.body} title={resource.title}/>):<p role="alert">자료를 다시 열어 주세요.</p>;
  if(resource.kind==="uikit-asset")return <FlowUIKitReference key={resource.id+resource.revision} id={resource.id} revision={resource.revision}/>;
  if(resource.kind==="design-recipe")return <FlowDesignReference key={resource.id} id={resource.id}/>;
  if(resource.kind==="host-file")return <FlowHostFile key={resource.root+":"+resource.path} root={resource.root} path={resource.path}/>;
  return resource.resolved?<Publication body={resource.resolved.body} title={resource.title}/>:<p role="alert">발간물을 다시 열어 주세요.</p>;
}

export function FlowResourceView({resource,onClose,action,hideTitle=false,renderSourceArtifact}: {resource:FlowOpenResource;onClose?:()=>void;action?:ReactNode;hideTitle?:boolean;renderSourceArtifact?:(artifact:FlowArtifact)=>ReactNode}) {
  return <section className="flow-resource-view" aria-label={resource.title}>
    <header className="flow-resource-view-head">
      <div><span>{resource.detail}</span>{!hideTitle&&<h3>{resource.title}</h3>}</div>
      <div className="flow-resource-view-actions">
        {resource.kind==="host-file"?<ButtonLink href={resource.href} download color="primary" variant="ghost" size="md" pill={false}><Download size="1em" aria-hidden="true"/>원본 받기</ButtonLink>:resource.kind!=="flow-source"&&<ButtonLink href={resource.href} target="_blank" rel="noopener noreferrer" color="primary" variant="ghost" size="md" pill={false}><ExternalLink size="1em" aria-hidden="true"/>원본 열기</ButtonLink>}
        {action}
        {onClose&&<Button type="button" color="primary" variant="ghost" pill={false} size="md" onClick={onClose}>닫기</Button>}
      </div>
    </header>
    <div className="flow-resource-view-body"><ResourceBody resource={resource} renderSourceArtifact={renderSourceArtifact}/></div>
  </section>;
}
