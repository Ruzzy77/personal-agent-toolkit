import {stackComposition} from './composition.js';
import {contentKinds,newContentBlock} from './composition-editor.js';
import {validContentBlock} from './validation.js';

export function blankParagraphs(value){
 if(typeof value!=='string')return null;
 const text=value.trim();
 if(text.length>20000)return null;
 const paragraphs=text?text.split(/\n\s*\n/).map(part=>part.trim()).filter(Boolean):[];
 return paragraphs.length<=100?paragraphs:null;
}

export function contentDraftFromBlank(artifact,kind='text',newId=()=>crypto.randomUUID()){
 if(!artifact||artifact.kind!=='blank'||typeof artifact.id!=='string'||!artifact.id||
    typeof artifact.title!=='string'||!Number.isSafeInteger(artifact.revision)||artifact.revision<0)
  throw new TypeError('빈 작업물을 확인해 주세요.');
 if(!contentKinds.some(([name])=>name===kind))throw new TypeError('콘텐츠 형식을 확인해 주세요.');
 const paragraphs=blankParagraphs(artifact.draftText??'');
 if(!paragraphs)throw new TypeError('작업 화면에 넣을 내용을 확인해 주세요.');
 const blocks=[];
 if(paragraphs.length){
  const textBlock=newContentBlock('text',newId());
  textBlock.content.paragraphs=paragraphs;
  blocks.push(textBlock);
 }
 if(kind!=='text'||!blocks.length)blocks.push(newContentBlock(kind,newId(),newId));
 return {id:artifact.id,kind:'content',revision:artifact.revision,title:artifact.title,composition:stackComposition(blocks)};
}

export function contentFromBlank(artifact,additionalBlock=null,newId=()=>crypto.randomUUID()){
 if(!artifact||artifact.kind!=='blank'||typeof artifact.id!=='string'||!artifact.id||
    typeof artifact.title!=='string'||!Number.isSafeInteger(artifact.revision)||artifact.revision<0)
  throw new TypeError('빈 작업물을 확인해 주세요.');
 const paragraphs=blankParagraphs(artifact.draftText??'');
 if(!paragraphs||!paragraphs.length&&!additionalBlock||
    additionalBlock&&!validContentBlock(additionalBlock))
  throw new TypeError('작업 화면에 넣을 내용을 확인해 주세요.');
 const blocks=[];
 if(paragraphs.length){
  const textBlock=newContentBlock('text',newId());
  textBlock.content.paragraphs=paragraphs;
  blocks.push(textBlock);
 }
 if(additionalBlock)blocks.push(additionalBlock);
 return {id:artifact.id,kind:'content',revision:artifact.revision,title:artifact.title.trim(),composition:stackComposition(blocks)};
}
