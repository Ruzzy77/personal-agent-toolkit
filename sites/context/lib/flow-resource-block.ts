import { stackComposition } from "@personal-agent/flow-surface/composition";
import type { FlowArtifact, FlowBlock, FlowComposition, FlowLinkedResource } from "./flow-content";
import type { FlowResourceSummary } from "./flow-resources";
import { flowResourceIdentity, flowResourceKey } from "./flow-resources.ts";

export function resourceBlockFromSummary(resource:FlowResourceSummary,id:string):FlowBlock {
  return {id,kind:"resource",content:{
    reference:flowResourceIdentity(resource),
    title:resource.title.slice(0,500),
    detail:resource.detail.slice(0,100),
  }};
}

export function resourceContentArtifact(resource:FlowResourceSummary,artifactId:string,blockId:string):FlowArtifact {
  return {
    id:artifactId,kind:"content",revision:0,title:resource.title.slice(0,160),
    composition:stackComposition([resourceBlockFromSummary(resource,blockId)]),
  };
}

export function hasResourceBlock(composition:FlowComposition,resource:FlowResourceSummary):boolean {
  const key=flowResourceKey(flowResourceIdentity(resource));
  return composition.blocks.some(block=>block.kind==="resource"&&
    flowResourceKey(block.content.reference as FlowLinkedResource)===key);
}
