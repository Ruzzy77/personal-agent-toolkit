import {validResourceReference,resourceReferenceKey} from './resource-reference.js';

export function sourceReference(source,root){
 if(validResourceReference(source?.reference))return source.reference;
 if(source?.live&&source.filePath&&root){
  const reference={kind:'host-file',root:source.root||root,path:source.filePath};
  if(validResourceReference(reference))return reference;
 }
 return null;
}
export function referenceTitle(reference){
 if(reference.kind==='host-file')return reference.path.split('/').at(-1);
 if(reference.kind==='context'){
  const locator=reference.locator;
  return locator.documentId||locator.sectionId||locator.itemId||locator.spaceId||'연결한 자료';
 }
 return ({'user-context':'사용자 맥락','journal-item':'연결한 기록','library-issue':'연결한 발간물'})[reference.kind]||reference.id;
}
export function workReferences(work,sources=[],root){
 const catalog=new Map(sources.map(source=>[source.id,source]));
 const items=[...new Set(work.sourceIds||[])].map(id=>{
  const source=catalog.get(id),reference=sourceReference(source,root);
  return {id:'source:'+id,title:source?.title||'연결한 자료',source,reference,
   sourceIds:[id],linkedReferences:[],missing:!source};
 });
 for(const reference of work.linkedResources||[]){
  if(!validResourceReference(reference))continue;
  const key=resourceReferenceKey(reference);
  const existing=items.find(item=>item.reference&&resourceReferenceKey(item.reference)===key);
  if(existing){existing.linkedReferences.push(reference);continue;}
  const known=sources.find(source=>{const ref=sourceReference(source,root);return ref&&resourceReferenceKey(ref)===key});
  items.push({id:'reference:'+key,title:known?.title||referenceTitle(reference),reference,sourceIds:[],linkedReferences:[reference]});
 }
 return items;
}
export function disconnectWorkReference(work,item){
 const sourceIds=new Set(item.sourceIds),references=new Set(item.linkedReferences.map(resourceReferenceKey));
 return {sourceIds:(work.sourceIds||[]).filter(id=>!sourceIds.has(id)),
  linkedResources:(work.linkedResources||[]).filter(reference=>!references.has(resourceReferenceKey(reference)))};
}
