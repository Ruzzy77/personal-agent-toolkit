import { resourceReferenceKey } from "@personal-agent/flow-surface/resource-reference";
import type { UIKitAsset } from "./flow-uikit";
import type { FlowContextLocator, FlowLinkedResource } from "./flow-content";

export type FlowResourceSummary = FlowLinkedResource & {
  title: string;
  detail: string;
  href: string;
  context?: {nodes:Array<{node_id:string;name:string;description?:string}>;predicates:Array<{predicate_id:string;name:string;description?:string}>;edges:Array<{edge_id:string;source_id:string;target_id:string;predicate_id:string}>};
};

type JournalRecord = {
  id: string; title: string; summary: string; weekId: string;
  resolution: "active"|"held"|"completed"|"canceled";
};
type LibraryRecord = {
  id: string; title: string; date: string; collection: string; canonicalPath: string;
};

const resolutionLabel: Record<JournalRecord["resolution"],string> = {
  active:"진행 중",held:"보류",completed:"완료",canceled:"취소",
};
const collectionLabel: Record<string,string> = {daily:"Daily",digest:"Digest",research:"Research"};

export type DesignRecipeRecord = {
  id:string; name:string; description:string; version:string;
  status:string; selection_ready:boolean;
  gallery?:{korean_name?:string;purpose?:string};
  profiles?:Record<string,string[]>; templates:Record<string,string>;
};

export function designResource(recipe:DesignRecipeRecord):FlowResourceSummary {
  const title=recipe.gallery?.korean_name?.trim() || recipe.name;
  const id=/^[a-z0-9][a-z0-9-]{0,63}$/.test(recipe.id)?recipe.id:"";
  return {kind:"design-recipe",id:recipe.id,title,detail:"디자인 자료",href:id?`/design#design-${id}`:"/design"};
}

export function uikitResource(item:UIKitAsset):FlowResourceSummary {
 return {kind:"uikit-asset",id:item.id,revision:item.revision,title:item.title,detail:"UIKit "+({template:"템플릿",reference:"레퍼런스",asset:"애셋"}[item.kind]),href:"https://personal-uikit.hiyaq77.workers.dev/?"+new URLSearchParams({item:item.id,revision:item.revision})};
}

export function flowResourceIdentity(item:FlowResourceSummary):FlowLinkedResource {
  if(item.kind==="uikit-asset")return {kind:item.kind,id:item.id,revision:item.revision};
  if(item.kind==="context")return {kind:"context",locator:item.locator};
  if(item.kind==="host-file")return {kind:"host-file",root:item.root,path:item.path};
  return {kind:item.kind,id:item.id};
}

export const flowResourceKey = resourceReferenceKey;

export function contextHref(locator:FlowContextLocator):string {
  const params=new URLSearchParams();
  if(locator.product==="sense"){
    params.set("sense",locator.sectionId);
    if(locator.skill)params.set("skill","1");
  }else{
    params.set("space",locator.spaceId);
    if(locator.product==="corpus")params.set("document",locator.documentId);
    if(locator.product==="context-item")params.set("item",locator.itemId);
    if(locator.product==="context-skill")params.set("skill","1");
    if(locator.product==="source")params.set("source",locator.readRef);
  }
  return `/context?${params}`;
}

export function contextResource(locator:FlowContextLocator,title:string):FlowResourceSummary {
  const detail=locator.product==="sense"?locator.skill?"작성 지침":"기준"
    :locator.product==="corpus"?"문서"
    :locator.product==="context-item"?"발췌"
    :locator.product==="source"?"원자료":"프로젝트 지침";
  return {kind:"context",locator,title,detail,href:contextHref(locator)};
}

export function hostFileResource(root:string,path:string):FlowResourceSummary {
  const title=path.split("/").at(-1)??path;
  return {kind:"host-file",root,path,title,detail:root==="workspace"?"파일":root,
    href:"/api/file-content?"+new URLSearchParams({root,path})};
}

export function journalResource(item:JournalRecord):FlowResourceSummary {
  const week = /^\d{4}-\d{2}-\d{2}$/.test(item.weekId) ? item.weekId : "";
  return {
    kind:"journal-item",id:item.id,title:item.title,
    detail:`${resolutionLabel[item.resolution] ?? "기록"}`,
    href:week ? `/journal?week=${encodeURIComponent(week)}&item=${encodeURIComponent(item.id)}` : `/journal?item=${encodeURIComponent(item.id)}`,
  };
}

export function libraryResource(item:LibraryRecord):FlowResourceSummary {
  const path = item.canonicalPath;
  const href = /^\/editions\/[A-Za-z0-9/_-]+$/.test(path) ? path : "/library";
  return {
    kind:"library-issue",id:item.id,title:item.title,
    detail:item.date,
    href,
  };
}
