import { validContextLocator } from "@personal-agent/flow-surface/resource-reference";
import type { FlowContextLocator } from './flow-content';

const sectionId = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const documentId = /^[A-Za-z0-9][A-Za-z0-9._:@-]*$/;

export function contextLocatorFromSearch(search:string, spaceIds:readonly string[]):FlowContextLocator|null {
  const params=new URLSearchParams(search.includes('?')?search.slice(search.indexOf('?')):search);
  const sense=params.get('sense');
  const skill=params.get('skill')==='1';
  if(sense)return sense.length<=64&&sectionId.test(sense)
    ? {product:'sense',sectionId:sense,...(skill?{skill:true as const}:{})}:null;
  const spaceId=params.get('space');
  if(!spaceId||!spaceIds.includes(spaceId))return null;
  const document=params.get('document');
  if(document)return document.length<=200&&documentId.test(document)
    ? {product:'corpus',spaceId,documentId:document}:null;
  const source=params.get('source');
  if(source){
    const locator={product:'source' as const,spaceId,readRef:source};
    return params.getAll('source').length===1&&validContextLocator(locator)?locator:null;
  }
  const item=params.get('item');
  if(item)return item.length<=200&&documentId.test(item)
    ? {product:'context-item',spaceId,itemId:item}:null;
  return skill?{product:'context-skill',spaceId}:null;
}
