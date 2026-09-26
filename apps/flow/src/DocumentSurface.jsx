import React,{useEffect,useState} from 'react';
import {ChevronLeft,ChevronRight,Plus} from 'lucide-react';
import {B} from './ui.jsx';
import {Editable} from './Editable.jsx';

export function DocumentSurface({artifact:a,onChange,viewBlockId,onViewBlock,onSelection}){
 const [slide,setSlide]=useState(()=>Math.max(0,a.blocks.findIndex(b=>b.id===(viewBlockId||a.lastBlockId))));
 useEffect(()=>{if(a.format==='slides')setSlide(Math.max(0,a.blocks.findIndex(b=>b.id===(viewBlockId||a.lastBlockId))))},[a.format]);
 useEffect(()=>{const element=document.getElementById('block-'+(viewBlockId||a.lastBlockId));if(element){const r=element.getBoundingClientRect();if(r.top<0||r.bottom>window.innerHeight)element.scrollIntoView({block:'center'})}},[]);
 function patchBlock(id,changes){onChange(current=>({blocks:current.blocks.map(b=>b.id===id?{...b,...changes}:b)}))}
 function block(b,index){return <section className="content-section su-stack" key={b.id} id={'block-'+b.id}>
  <Editable as="h3" className="section-title" value={b.heading} onChange={heading=>patchBlock(b.id,{heading})} label={'소제목 '+(index+1)} placeholder="소제목"/>
  <div className="paragraph-wrap"><Editable value={b.text} onChange={text=>patchBlock(b.id,{text})} onActivate={()=>{if(viewBlockId!==b.id)onViewBlock(b.id)}} label={'본문 '+(index+1)} onSelectText={text=>onSelection?.({blockId:b.id,text})} placeholder="내용을 입력하세요."/></div>
 </section>}
 const current=Math.min(slide,a.blocks.length-1);
 function go(index){setSlide(index);onViewBlock(a.blocks[index].id)}
 return <>{a.format==='slides'?<>{block(a.blocks[current],current)}<nav className="slide-navigation su-row" aria-label="발표 페이지">
  <B variant="ghost" uniform disabled={current===0} aria-label="이전 장" onClick={()=>go(current-1)}><ChevronLeft size="1em"/></B><span>{current+1} / {a.blocks.length}</span>
  <B variant="ghost" uniform disabled={current===a.blocks.length-1} aria-label="다음 장" onClick={()=>go(current+1)}><ChevronRight size="1em"/></B><B variant="outline" onClick={()=>onChange({format:'document'})}>전체 문서</B>
 </nav></>:a.blocks.map(block)}
 <div className="add-section"><B variant="outline" onClick={()=>onChange(current=>({blocks:[...current.blocks,{id:crypto.randomUUID(),heading:'',text:''}]}))}><Plus size="1em"/>내용 추가</B></div></>;
}
