import {defineComposition} from './composition.js';
import {safeContentHref} from './content-links.js';
import {validImageRegion} from './image-region.js';
import {validResourceReference} from './resource-reference.js';
import {workspaceMediaType} from './composition-editor.js';

const kinds=new Set(['heading','text','image','comparison','diagram','media','table','metrics','chart','gallery','steps','references','file','code','audio','resource']);
const string=(value,max=20000)=>typeof value==='string'&&value.length<=max;
const optional=(value,max=20000)=>value===undefined||string(value,max);
const list=(value,max,check)=>Array.isArray(value)&&value.length<=max&&value.every(check);
const textOrNumber=value=>string(value,2000)||typeof value==='number'&&Number.isFinite(value);
const rect=validImageRegion;
const imageExtension=/\.(?:png|jpe?g|webp|gif)$/i;
const mediaSource=(value,type='image')=>{
 if(type==='image'&&string(value,2800000)&&/^data:image\/(?:png|jpeg|webp|gif);base64,[A-Za-z0-9+/]+={0,2}$/.test(value))return true;
 if(!string(value,2048)||!value.startsWith('/')||value.startsWith('//'))return false;
 if(/^\/api\/flow\/assets\/[a-f0-9]{64}\.(png|jpg|webp|gif)$/.test(value))return type==='image';
 if(value.startsWith('/examples/'))return /^\/examples\/[A-Za-z0-9._-]+$/.test(value)&&
  (type==='image'?imageExtension.test(value):workspaceMediaType(value.slice('/examples/'.length))?.kind===type);
 try{
  const url=new URL(value,'http://flow.local');
  const path=url.searchParams.get('path');
  return url.pathname==='/api/flow/files/content'&&url.searchParams.has('workspaceId')&&!!path&&path.length<=2048&&
   (type==='image'?imageExtension.test(path):workspaceMediaType(path)?.kind===type);
 }catch{return false}
};
const link=value=>value===undefined||string(value,2048)&&safeContentHref(value)!==null;
const itemId=item=>item&&typeof item==='object'&&optional(item.id,160);
const image=item=>itemId(item)&&mediaSource(item.src)&&optional(item.alt,1000)&&optional(item.caption,3000);
const diagramNode=node=>node&&typeof node==='object'&&string(node.id,160)&&node.id&&string(node.label,120)&&node.label.trim()&&optional(node.detail,2000)&&Number.isFinite(node.x)&&node.x>=0&&node.x<=1000&&Number.isFinite(node.y)&&node.y>=0&&node.y<=600;
const diagramEdge=(edge,nodeIds)=>edge&&typeof edge==='object'&&string(edge.id,160)&&edge.id&&nodeIds.has(edge.from)&&nodeIds.has(edge.to)&&edge.from!==edge.to&&optional(edge.label,120);
const uniqueIds=items=>new Set(items.map(item=>item.id)).size===items.length;
const diagram=content=>{
 if(!optional(content.heading,500)||!list(content.nodes,100,diagramNode)||!content.nodes.length||!uniqueIds(content.nodes))return false;
 const nodeIds=new Set(content.nodes.map(node=>node.id));
 return list(content.edges,200,edge=>diagramEdge(edge,nodeIds))&&uniqueIds(content.edges);
};

export function validContentBlock(block){
 if(!block||typeof block!=='object'||!string(block.id,160)||!block.id||!kinds.has(block.kind)||!block.content||typeof block.content!=='object'||Array.isArray(block.content))return false;
 const c=block.content;
 switch(block.kind){
  case 'heading': return string(c.title,500)&&optional(c.description,5000);
  case 'text': return optional(c.heading,500)&&list(c.paragraphs,100,value=>string(value,20000));
  case 'image': return image(c)&&(c.selection===undefined||rect(c.selection))&&
   (c.width===undefined&&c.height===undefined||Number.isFinite(c.width)&&c.width>0&&Number.isFinite(c.height)&&c.height>0)&&
   (c.crop===undefined||c.crop===null||c.width!==undefined&&c.height!==undefined&&rect(c.crop));
  case 'comparison': return optional(c.heading,500)&&list(c.items,24,item=>image(item)&&string(item.label,500)&&(!item.view||Number.isFinite(item.view.scale)&&item.view.scale>0&&item.view.scale<=20&&Number.isFinite(item.view.x)&&Math.abs(item.view.x)<=20&&Number.isFinite(item.view.y)&&Math.abs(item.view.y)<=20));
  case 'diagram': return diagram(c);
  case 'media': return optional(c.heading,500)&&optional(c.alt,1000)&&optional(c.caption,3000)&&(c.src!==undefined||c.poster!==undefined)&&(c.src===undefined||mediaSource(c.src,'video'))&&(c.poster===undefined||mediaSource(c.poster));
  case 'table': return optional(c.heading,500)&&list(c.columns,30,value=>string(value,500))&&c.columns.length>0&&list(c.rows,1000,row=>list(row,c.columns.length,textOrNumber)&&row.length===c.columns.length);
  case 'metrics': return optional(c.heading,500)&&list(c.items,40,item=>itemId(item)&&string(item.label,500)&&textOrNumber(item.value)&&optional(item.unit,100)&&optional(item.detail,2000));
  case 'chart': return optional(c.heading,500)&&optional(c.unit,100)&&optional(c.caption,3000)&&list(c.items,100,item=>itemId(item)&&string(item.label,500)&&typeof item.value==='number'&&Number.isFinite(item.value)&&item.value>=0&&optional(item.unit,100));
  case 'gallery': return optional(c.heading,500)&&list(c.images,100,image);
  case 'steps': return optional(c.heading,500)&&list(c.steps,100,item=>itemId(item)&&string(item.title,500)&&optional(item.text,3000));
  case 'references': return optional(c.heading,500)&&list(c.items,100,item=>itemId(item)&&string(item.title,500)&&optional(item.detail,3000)&&link(item.href));
  case 'file': return string(c.name,500)&&optional(c.type,100)&&optional(c.size,100)&&optional(c.description,3000)&&link(c.href);
  case 'code': return optional(c.heading,500)&&optional(c.language,100)&&string(c.code,100000);
  case 'audio': return optional(c.heading,500)&&mediaSource(c.src,'audio')&&optional(c.caption,3000)&&optional(c.transcript,50000);
  case 'resource': return validResourceReference(c.reference)&&string(c.title,500)&&c.title.trim().length>0&&optional(c.detail,100);
  default: return false;
 }
}

export function validContentComposition(composition){
 if(!composition||!Array.isArray(composition.blocks)||!composition.blocks.length||composition.blocks.length>100||!Array.isArray(composition.rows)||composition.rows.length>100)return false;
 if(!composition.blocks.every(validContentBlock))return false;
 try{defineComposition(composition);return true}catch{return false}
}

export function contentAssetSources(composition){
 if(!validContentComposition(composition))return [];
 const sources=[];
 for(const block of composition.blocks){
  const c=block.content;
  if(c.src)sources.push(c.src);
  if(c.poster)sources.push(c.poster);
  for(const item of [...(c.items||[]),...(c.images||[])])if(item?.src)sources.push(item.src);
 }
 return sources.filter(src=>src.startsWith('/api/flow/assets/'));
}
