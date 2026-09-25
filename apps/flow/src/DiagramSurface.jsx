import React,{useRef,useState,useLayoutEffect,useEffect} from 'react';
import {Field,FieldSelect} from '@personal-agent/ui-kit/react';
import {Input} from '@openai/apps-sdk-ui/components/Input';
import {Textarea} from '@openai/apps-sdk-ui/components/Textarea';
import {Plus,X,Link2,Trash2,Maximize2} from 'lucide-react';
import {B} from './ui.jsx';
import {layoutNodes} from './model.js';
import {DiagramCanvas} from './work-surface/index.js';

export function DiagramSurface({artifact:a,onChange,onCommit,readOnly=false,fitView,onFitChange,onSelection}){
 const [selected,setSelected]=useState(null),[proposal,setProposal]=useState(null),[target,setTarget]=useState(''),[localFit,setLocalFit]=useState(readOnly);
 const fit=fitView??localFit;
 const viewport=useRef(),editor=useRef();
 useLayoutEffect(()=>{const el=viewport.current;if(el)el.scrollLeft=Math.max(0,(el.scrollWidth-el.clientWidth)/2)},[fit,selected?.id]);
 const nodes=proposal?.nodes||a.nodes;
 const node=a.nodes.find(n=>selected?.kind==='node'&&n.id===selected.id),edge=a.edges.find(e=>selected?.kind==='edge'&&e.id===selected.id);
 useEffect(()=>{if(!readOnly)onSelection?.(node||edge?{kind:'diagram-object',objectKind:selected.kind,objectId:selected.id}:null)},[selected?.kind,selected?.id,a.revision,readOnly]);
 function select(kind,id){if(readOnly)return;setSelected({kind,id});setTarget('')}
 function changeNode(changes){onChange(current=>({nodes:current.nodes.map(n=>n.id===node.id?{...n,...changes}:n)}))}
 function revealEditor(){if(window.innerWidth<=1000)requestAnimationFrame(()=>editor.current?.scrollIntoView({block:'nearest'}))}
 function moveNode({id,x,y,source,revision}){
  if(source==='pointer')onCommit({nodes:a.nodes.map(item=>item.id===id?{...item,x,y}:item)},revision);
  else onChange(current=>({nodes:current.nodes.map(item=>item.id===id?{...item,x,y}:item)}));
 }
 function connect(){
  if(!node||!target||a.edges.some(e=>e.from===node.id&&e.to===target))return;
  onCommit({edges:[...a.edges,{id:crypto.randomUUID(),from:node.id,to:target,label:''}]},a.revision);setTarget('');
 }
 return <div className="su-stack" data-gap="section">
  {!readOnly&&<div className="artifact-tools su-toolbar">
   {proposal?<><span className="small muted">배치 미리보기</span><div className="su-row"><B variant="solid" onClick={()=>{if(onCommit({nodes:proposal.nodes},proposal.revision))setProposal(null)}}>적용</B><B variant="ghost" onClick={()=>setProposal(null)}>취소</B></div></>:<><div className="su-row">
    <B onClick={()=>{const n={id:crypto.randomUUID(),label:'새 항목',detail:'',x:500,y:300};onCommit({nodes:[...a.nodes,n]},a.revision);select('node',n.id)}}><Plus size="1em"/>항목 추가</B>
    <B variant="ghost" disabled={!a.nodes.length} onClick={()=>setProposal({nodes:layoutNodes(a.nodes,'vertical'),revision:a.revision})}>세로 배치</B>
    <B variant="ghost" disabled={!a.nodes.length} onClick={()=>setProposal({nodes:layoutNodes(a.nodes,'horizontal'),revision:a.revision})}>가로 배치</B>
   </div><B uniform variant="ghost" aria-label={fit?'크게 보기':'전체 보기'} title={fit?'크게 보기':'전체 보기'} onClick={()=>onFitChange?onFitChange(!fit):setLocalFit(v=>!v)}><Maximize2 size="1em"/></B></>}
  </div>}
  <div className={'artifact-detail-layout '+(!readOnly&&!proposal&&(node||edge)?'has-detail':'')}>
  <div ref={viewport} className="diagram-viewport su-panel" onKeyDown={e=>{if(e.key==='Escape'){setSelected(null);setProposal(null)}}}>
   <DiagramCanvas title={a.title} nodes={nodes} edges={a.edges} selected={selected} revision={a.revision} readOnly={readOnly} disabled={!!proposal} fit={fit} onSelect={(selection,meta)=>{if(!selection){setSelected(null);setTarget('');return}select(selection.kind,selection.id);if(selection.kind==='node'&&(meta?.source==='pointer'||meta?.source==='keyboard'))revealEditor()}} onMoveNode={moveNode}/>
  </div>
  {!proposal&&(node||edge)&&<section ref={editor} className="selection-editor su-stack" aria-label={node?'선택한 항목':'선택한 연결'}>
   <div className="su-toolbar"><span className="small muted">{node?'선택한 항목':'선택한 연결'}</span><B uniform variant="ghost" aria-label="선택 해제" onClick={()=>setSelected(null)}><X size="1em"/></B></div>
   <Field label={node?'이름':'연결 이름'}><Input aria-label={node?'항목 이름':'연결 이름'} value={node?node.label:edge.label} onChange={e=>node?changeNode({label:e.target.value}):onChange(current=>({edges:current.edges.map(item=>item.id===edge.id?{...item,label:e.target.value}:item)}))} maxLength={120}/></Field>
   {node&&<>
    <Field label="내용"><Textarea aria-label="항목 내용" value={node.detail||''} onChange={e=>changeNode({detail:e.target.value})} rows={2}/></Field>
    {a.nodes.length>1&&<div className="su-row connection-picker"><FieldSelect visibleLabel aria-label="연결할 항목" placeholder="항목 선택" value={target} options={a.nodes.filter(n=>n.id!==node.id).map(n=>({value:n.id,label:n.label||'제목 없음',disabled:a.edges.some(e=>e.from===node.id&&e.to===n.id)}))} onChange={option=>setTarget(option.value)}/><B disabled={!target} onClick={connect}><Link2 size="1em"/>연결</B></div>}
    <p className="small muted">항목을 끌거나, 항목에 초점을 둔 뒤 방향키로 이동할 수 있습니다.</p>
   </>}
   <div><B variant="ghost" onClick={()=>{onCommit(node?{nodes:a.nodes.filter(n=>n.id!==node.id),edges:a.edges.filter(e=>e.from!==node.id&&e.to!==node.id)}:{edges:a.edges.filter(e=>e.id!==edge.id)},a.revision);setSelected(null)}}><Trash2 size="1em"/>{node?'항목 삭제':'연결 삭제'}</B></div>
  </section>}
  </div>
 </div>;
}
