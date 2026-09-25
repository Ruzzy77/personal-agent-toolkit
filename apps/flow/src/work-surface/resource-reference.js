const JOURNAL_ID=/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i;
const LIBRARY_ID=/^(daily|digest|research):\d{4}-\d{2}-\d{2}(?::(?:[01]\d|2[0-3]))?$/;
const DESIGN_ID=/^[a-z0-9][a-z0-9-]{0,63}$/;
const HOST_ROOT=/^[a-z0-9][a-z0-9._-]*(?:\/[a-z0-9][a-z0-9._-]*)*$/;
const SPACE_ID=/^[a-z0-9][a-z0-9._-]{0,63}$/;
const DOCUMENT_ID=/^[A-Za-z0-9][A-Za-z0-9._:@-]{0,199}$/;
const SECTION_ID=/^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const exact=(value,keys)=>value&&typeof value==='object'&&!Array.isArray(value)&&Object.keys(value).sort().join(',')===keys.sort().join(',');
const READ_REF=/^read1\.[A-Za-z0-9_-]+$/;
const validSourceReference=(readRef,spaceId)=>{
 if(typeof readRef!=='string'||readRef.length>8192||!READ_REF.test(readRef))return false;
 try{
  const encoded=readRef.slice(6);
  const parsed=JSON.parse(new TextDecoder().decode(Uint8Array.from(atob(encoded.replaceAll('-','+').replaceAll('_','/')),char=>char.charCodeAt(0))));
  return exact(parsed,['version','spaceId','connectionId','corpusId','unitId'])&&parsed.version===1&&
   parsed.spaceId===spaceId&&['connectionId','corpusId','unitId'].every(key=>typeof parsed[key]==='string'&&parsed[key].length>0);
 }catch{return false;}
};
const validHostPath=value=>typeof value==='string'&&value.length>0&&
 new TextEncoder().encode(value).length<=4096&&!value.startsWith('/')&&!value.startsWith('~')&&
 !value.includes('\\')&&!/[\x00-\x1f\x7f]/.test(value)&&
 value.split('/').every(part=>part&&part!=='.'&&part!=='..');

export function validContextLocator(locator){
 if(!locator||typeof locator!=='object'||Array.isArray(locator))return false;
 if(locator.product==='sense')return exact(locator,locator.skill===true?['product','sectionId','skill']:['product','sectionId'])&&typeof locator.sectionId==='string'&&SECTION_ID.test(locator.sectionId)&&locator.sectionId.length<=64;
 if(locator.product==='corpus')return exact(locator,['product','spaceId','documentId'])&&typeof locator.spaceId==='string'&&typeof locator.documentId==='string'&&SPACE_ID.test(locator.spaceId)&&locator.documentId.length<=200&&DOCUMENT_ID.test(locator.documentId);
 if(locator.product==='context-item')return exact(locator,['product','spaceId','itemId'])&&typeof locator.spaceId==='string'&&typeof locator.itemId==='string'&&SPACE_ID.test(locator.spaceId)&&locator.itemId.length<=200&&DOCUMENT_ID.test(locator.itemId);
 if(locator.product==='context-skill')return exact(locator,['product','spaceId'])&&typeof locator.spaceId==='string'&&SPACE_ID.test(locator.spaceId);
 if(locator.product==='source')return exact(locator,['product','spaceId','readRef'])&&typeof locator.spaceId==='string'&&SPACE_ID.test(locator.spaceId)&&validSourceReference(locator.readRef,locator.spaceId);
 return false;
}
export function resourceReferenceKey(item){
 if(item.kind==='host-file')return 'host-file:'+item.root+':'+item.path;
 if(item.kind==='uikit-asset')return 'uikit-asset:'+item.id+':'+item.revision;
 if(item.kind!=='context')return item.kind+':'+item.id;
 const loc=item.locator;
 return 'context:'+loc.product+('spaceId' in loc?':'+loc.spaceId:'')+('sectionId' in loc?':'+loc.sectionId+(loc.skill?':skill':''):'')+('documentId' in loc?':'+loc.documentId:'')+('itemId' in loc?':'+loc.itemId:'')+('readRef' in loc?':'+loc.readRef:'');
}
export function validResourceReference(item){
 if(item?.kind==='user-context')return exact(item,['kind','id'])&&typeof item.id==='string'&&/^(node|pred)_[a-f0-9]{32}$/.test(item.id);
 if(item?.kind==='journal-item')return exact(item,['kind','id'])&&typeof item.id==='string'&&JOURNAL_ID.test(item.id);
 if(item?.kind==='library-issue')return exact(item,['kind','id'])&&typeof item.id==='string'&&LIBRARY_ID.test(item.id);
 if(item?.kind==='uikit-asset')return exact(item,['kind','id','revision'])&&typeof item.id==='string'&&/^[a-z0-9][a-z0-9-]{0,100}$/.test(item.id)&&typeof item.revision==='string'&&/^[a-f0-9]{64}$/.test(item.revision);
 if(item?.kind==='design-recipe')return exact(item,['kind','id'])&&typeof item.id==='string'&&DESIGN_ID.test(item.id);
 if(item?.kind==='host-file')return exact(item,['kind','root','path'])&&typeof item.root==='string'&&item.root.length<=256&&HOST_ROOT.test(item.root)&&validHostPath(item.path);
 if(item?.kind==='context')return exact(item,['kind','locator'])&&validContextLocator(item.locator);
 return false;
}
export function validLinkedResources(items){
 return Array.isArray(items)&&items.length<=24&&items.every(validResourceReference)&&new Set(items.map(resourceReferenceKey)).size===items.length;
}
