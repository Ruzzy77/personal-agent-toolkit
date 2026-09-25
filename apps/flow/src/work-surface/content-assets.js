import {validContentBlock,validContentComposition} from './validation.js';
import {fileContentFromFile,updateContentBlock} from './composition-editor.js';

export const contentImageSlot=(blockId,itemId)=>JSON.stringify([blockId,itemId??null]);
export const contentImageTargetSlot=target=>contentImageSlot(target.blockId,target.itemId??(target.field==='poster'?'poster':undefined));

export function setContentSource(composition,target,src,size,path){
 const block=composition.blocks.find(item=>item.id===target.blockId);
 if(!block)return composition;
 if(target.kind==='file'){
  if(block.kind!=='file'||target.field!=='href'||target.itemId)return composition;
  return updateContentBlock(composition,block.id,fileContentFromFile(block.content,path,src));
 }
 if(target.itemId){
  if(target.kind!=='image'||target.field!=='src'||!['gallery','comparison'].includes(block.kind))return composition;
  const key=block.kind==='gallery'?'images':'items',items=block.content[key];
  if(!Array.isArray(items)||!items.some(item=>item.id===target.itemId))return composition;
  return updateContentBlock(composition,block.id,{...block.content,[key]:items.map(item=>item.id===target.itemId
   ?{...item,src,...(block.kind==='comparison'?{view:undefined}:{})}:item)});
 }
 const supported=(target.kind==='image'&&((block.kind==='image'&&target.field==='src')||(block.kind==='media'&&target.field==='poster')))
  ||(target.kind==='video'&&block.kind==='media'&&target.field==='src')
  ||(target.kind==='audio'&&block.kind==='audio'&&target.field==='src');
 if(!supported)return composition;
 return updateContentBlock(composition,block.id,{...block.content,[target.field]:src,
  ...(block.kind==='image'?{selection:undefined,crop:null,width:size?.width,height:size?.height}:{})});
}
const sampleImage='/api/flow/assets/'+'0'.repeat(64)+'.png';

export function activeContentSources(composition,sources){
 if(!composition)return {};
 const slots=new Set();
 for(const block of composition.blocks){
  if(block.kind==='image')slots.add(contentImageSlot(block.id));
  if(block.kind==='media')slots.add(contentImageSlot(block.id,'poster'));
  if(block.kind==='gallery'||block.kind==='comparison'){
   const key=block.kind==='gallery'?'images':'items';
   for(const item of block.content[key]||[])slots.add(contentImageSlot(block.id,String(item.id??'')));
  }
 }
 return Object.fromEntries(Object.entries(sources).filter(([slot])=>slots.has(slot)));
}

export function replaceContentImages(composition,sources,resolve){
 return {...composition,blocks:composition.blocks.map(block=>{
  if(block.kind==='media'&&sources[contentImageSlot(block.id,'poster')])
   return {...block,content:{...block.content,poster:resolve(sources[contentImageSlot(block.id,'poster')])}};
  if(block.kind==='image'&&sources[contentImageSlot(block.id)])
   return {...block,content:{...block.content,src:resolve(sources[contentImageSlot(block.id)])}};
  if(block.kind==='gallery'||block.kind==='comparison'){
   const key=block.kind==='gallery'?'images':'items',items=block.content[key];
   if(!Array.isArray(items))return block;
   return {...block,content:{...block.content,[key]:items.map(item=>{
    const source=sources[contentImageSlot(block.id,String(item.id??''))];
    return source?{...item,src:resolve(source)}:item;
   })}};
  }
  return block;
 })};
}

export function validContentDraft(composition,sources){
 return validContentComposition(replaceContentImages(composition,activeContentSources(composition,sources),()=>sampleImage));
}

export function previewableContentDraft(composition,sources){
 try{
  if(!composition||!Array.isArray(composition.blocks)||!Array.isArray(composition.rows))return null;
  const active=activeContentSources(composition,sources);
  const ready=replaceContentImages(composition,active,()=>sampleImage);
  const usable=new Set(ready.blocks.filter(validContentBlock).map(block=>block.id));
  if(!usable.size)return null;
  const preview={
   blocks:composition.blocks.filter(block=>usable.has(block.id)),
   rows:composition.rows.map(row=>({...row,columns:row.columns.map(column=>({...column,ids:column.ids.filter(id=>usable.has(id))})).filter(column=>column.ids.length)})).filter(row=>row.columns.length)
  };
  return validContentDraft(preview,active)?preview:null;
 }catch{return null}
}

export async function materializeContentImages(composition,sources,importImage,keyOf=source=>source){
 const active=activeContentSources(composition,sources);
 if(!validContentDraft(composition,active))throw new Error('invalid_content');
 const imported=new Map();
 for(const source of Object.values(active)){
  const key=keyOf(source);
  if(!imported.has(key))imported.set(key,await importImage(source));
 }
 const result=replaceContentImages(composition,active,source=>imported.get(keyOf(source))??'');
 if(!validContentComposition(result))throw new Error('invalid_content');
 return result;
}
