import {stackComposition} from './composition.js';
import {appendContentBlock} from './composition-editor.js';
import {blankParagraphs,contentFromBlank} from './blank-content.js';
import {validContentBlock,validContentComposition} from './validation.js';

const invalid=()=>new TypeError('작업 화면으로 전환할 내용을 확인해 주세요.');

export function contentFromArtifact(artifact,newId=()=>crypto.randomUUID()){
 if(!artifact||typeof artifact.id!=='string'||!artifact.id||
    typeof artifact.title!=='string'||!Number.isSafeInteger(artifact.revision)||artifact.revision<0)
  throw invalid();
 let blocks;
 if(artifact.kind==='document'&&artifact.format==='document'&&
    Array.isArray(artifact.blocks)&&artifact.blocks.length&&artifact.blocks.length<=100){
  blocks=artifact.blocks.map(block=>({
   id:block?.id,kind:'text',content:{heading:block?.heading,paragraphs:[block?.text]}
  }));
 }else if(artifact.kind==='image'){
  blocks=[{id:newId(),kind:'image',content:{
   src:artifact.src,alt:artifact.alt||artifact.title,width:artifact.width,height:artifact.height,crop:artifact.crop??null
  }}];
 }else if(artifact.kind==='diagram'&&Array.isArray(artifact.nodes)&&Array.isArray(artifact.edges)){
  blocks=[{id:newId(),kind:'diagram',content:{
   heading:'',nodes:artifact.nodes.map(node=>({...node})),edges:artifact.edges.map(edge=>({...edge}))
  }}];
 }else throw invalid();
 let composition;
 try{composition=stackComposition(blocks)}catch{throw invalid()}
 if(!validContentComposition(composition))throw invalid();
 return {id:artifact.id,kind:'content',revision:artifact.revision,title:artifact.title,composition};
}

export function canConvertArtifactToContent(artifact){
 try{contentFromArtifact(artifact,()=> 'converted-diagram');return true}catch{return false}
}

export function keepsOriginalImage(artifact,composition){
 return artifact?.kind==='image'&&Array.isArray(composition?.blocks)&&composition.blocks.some(block=>
  block.kind==='image'&&block.content?.src===artifact.src&&
  block.content?.width===artifact.width&&block.content?.height===artifact.height);
}

export function contentBlockCapacity(artifact){
 if(!artifact||typeof artifact!=='object')return 0;
 if(artifact.kind==='blank'){
  const paragraphs=blankParagraphs(artifact.draftText??'');
  return paragraphs===null?0:100-(paragraphs.length?1:0);
 }
 if(artifact.kind==='content'){
  const composition=artifact.composition;
  return Array.isArray(composition?.blocks)&&Array.isArray(composition?.rows)&&
   composition.blocks.length>0&&composition.blocks.length<=100
   ?100-composition.blocks.length:0;
 }
 try{return 100-contentFromArtifact(artifact,()=> 'converted-block').composition.blocks.length}
 catch{return 0}
}

export function appendBlocksToArtifact(artifact,blocks,newId=()=>crypto.randomUUID()){
 if(!Array.isArray(blocks)||!blocks.length||blocks.length>contentBlockCapacity(artifact)||
    !blocks.every(validContentBlock))
  throw new TypeError('작업 화면에 넣을 내용을 확인해 주세요.');
 const content=artifact.kind==='blank'
  ?contentFromBlank(artifact,blocks[0],newId)
  :artifact.kind==='content'?artifact:contentFromArtifact(artifact,newId);
 let composition=content.composition;
 for(const block of blocks.slice(artifact.kind==='blank'?1:0))
  composition=appendContentBlock(composition,block,null,false,newId);
 return {...content,composition};
}
