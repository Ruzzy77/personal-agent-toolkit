"use client";

import React,{useEffect,useId,useRef,useState} from 'react';
import {Button} from '@openai/apps-sdk-ui/components/Button';
import {ChevronDown} from 'lucide-react';
import {appendContentBlock,contentKinds,contentOrder,newContentBlock,removeContentBlock} from './composition-editor.js';
import {ContentFields} from './ContentFields.jsx';
import './composition-editor.css';

const labels={...Object.fromEntries(contentKinds),resource:'연결 자료'};

export function CompositionEditor({composition,onChange,onPick,onAddFile,resolveImageUrl,busy=false,className=''}) {
 const order=contentOrder(composition),prefix=useId();
 const [active,setActive]=useState(()=>order.length===1?order[0]:null);
 const knownIds=useRef(new Set(order)),triggers=useRef(new Map());
 useEffect(()=>{
  const ids=contentOrder(composition),added=ids.find(id=>!knownIds.current.has(id));
  if(added)setActive(added);
  else if(active&&!ids.includes(active))setActive(ids[0]||null);
  knownIds.current=new Set(ids);
 },[composition,active]);
 const change=value=>onChange(value);
 function updateBlock(id,content){
  change({...composition,blocks:composition.blocks.map(block=>block.id===id?{...block,content}:block)});
 }
 function add(){
  if(busy||composition.blocks.length>=100)return;
  const block=newContentBlock('text',crypto.randomUUID());
  setActive(block.id);change(appendContentBlock(composition,block));
  requestAnimationFrame(()=>triggers.current.get(block.id)?.focus());
 }
 function remove(id){
  const at=order.indexOf(id),next=order[at+1]||order[at-1];
  setActive(next);change(removeContentBlock(composition,id));
  requestAnimationFrame(()=>triggers.current.get(next)?.focus());
 }
 return <div className={'flow-content-composer '+className}>
  <div className="flow-content-composer-head"><h3>내용</h3></div>
  <div className="flow-content-rows">{order.map(id=>{
      const block=composition.blocks.find(item=>item.id===id);
      if(!block)return null;
      const at=order.indexOf(id),label=labels[block.kind]||block.kind,expanded=active===id;
      const title=block.content.heading||block.content.title||block.content.name||label;
      const buttonId=prefix+'-heading-'+id,panelId=prefix+'-fields-'+id;
      return <section className="flow-content-block" key={id}>
       <h4 className="flow-content-block-heading"><Button ref={node=>{if(node)triggers.current.set(id,node);else triggers.current.delete(id)}} id={buttonId} type="button" color="primary" variant={expanded?'soft':'ghost'} pill={false} size="md" block className="flow-content-block-trigger" aria-expanded={expanded} aria-controls={panelId} onClick={()=>setActive(expanded?null:id)}><span className="flow-content-block-title">{title}</span>{title!==label&&<small>{label}</small>}<ChevronDown size="1em" aria-hidden="true"/></Button></h4>
       <div className="flow-content-block-body" id={panelId} role="group" aria-labelledby={buttonId} hidden={!expanded}>
        <ContentFields block={block} busy={busy} onPick={onPick} resolveImageUrl={resolveImageUrl} onChange={content=>updateBlock(id,content)}/>
       </div>
       {expanded&&<div className="flow-content-block-actions">
        <Button type="button" color="primary" variant="ghost" pill={false} size="sm" aria-label={label+' '+(at+1)+' 삭제'} disabled={busy||composition.blocks.length<=1} onClick={()=>remove(id)}>삭제</Button>
       </div>}
      </section>;
  })}</div>
  <div className="flow-content-add">
   <Button type="button" color="primary" variant="ghost" pill={false} size="sm" disabled={busy||composition.blocks.length>=100} onClick={add}>내용 추가</Button>
   {onAddFile&&<Button type="button" color="primary" variant="ghost" pill={false} size="sm" disabled={busy||composition.blocks.length>=100} onClick={onAddFile}>파일 추가</Button>}
  </div>
 </div>;
}
