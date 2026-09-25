"use client";

import { useEffect, useState } from "react";
import { ResourceBlock } from "@personal-agent/flow-surface";
import type { ContentBlock } from "@personal-agent/flow-surface";
import type { FlowLinkedResource } from "../../lib/flow-content";
import type { FlowResourceSummary } from "../../lib/flow-resources";
import { flowResourceKey } from "../../lib/flow-resources";
import { readLinked } from "../../lib/flow-resource-read";
import { FlowResourceView } from "./flow-resource-view";

export function FlowResourceBlock({block,context,artifactTitle}: {block:ContentBlock;context?:{headingLevel?:number};artifactTitle?:string}){
  const reference=block.content.reference as FlowLinkedResource;
  const key=flowResourceKey(reference);
  const [resource,setResource]=useState<FlowResourceSummary|null>(null);
  const [error,setError]=useState(false);
  useEffect(()=>{
    const controller=new AbortController();
    queueMicrotask(()=>{if(!controller.signal.aborted){setResource(null);setError(false)}});
    void readLinked(reference,controller.signal).then(result=>{
      if(!controller.signal.aborted)setResource(result);
    }).catch(()=>{if(!controller.signal.aborted)setError(true)});
    return()=>controller.abort();
  // Read again when the source identity changes.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  },[key]);
  if(resource)return <FlowResourceView resource={resource} hideTitle={resource.title===artifactTitle}/>;
  return <div>
    {block.content.title===artifactTitle
      ? <span className="flow-resource-kind">{String(block.content.detail??"연결 자료")}</span>
      : <ResourceBlock block={block} context={context}/>}
    <p className="flow-muted" role={error?"alert":"status"}>{error?"자료를 열지 못했습니다. 원본에서 다시 확인해 주세요.":"자료를 불러오는 중입니다."}</p>
  </div>;
}
