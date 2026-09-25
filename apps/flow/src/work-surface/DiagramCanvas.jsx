import React,{useId,useRef,useState} from 'react';
import {DIAGRAM_WIDTH,DIAGRAM_HEIGHT,diagramLabelLines,diagramNodeHeight,clampDiagramPosition,nudgeDiagramPosition} from './diagram-geometry.js';
import './diagram-canvas.css';

export function DiagramCanvas({title='',nodes=[],edges=[],selected=null,onSelect,onMoveNode,revision,readOnly=false,disabled=false,fit=false,className=''}){
 const svg=useRef(null),moving=useRef(null),latestDrag=useRef(null),[drag,setDrag]=useState(null);
 const arrow='ws-diagram-arrow-'+useId().replaceAll(':','');
 const shownNodes=drag?nodes.map(node=>node.id===drag.id?{...node,x:drag.x,y:drag.y}:node):nodes;
 const nodeById=new Map(shownNodes.map(node=>[node.id,node]));
 const interactive=!readOnly&&!disabled;
 function select(selection,source){if(interactive)onSelect?.(selection,{source})}
 function point(event){
  const bounds=svg.current?.getBoundingClientRect();
  if(!bounds?.width||!bounds.height)return null;
  return {x:(event.clientX-bounds.left)*DIAGRAM_WIDTH/bounds.width,y:(event.clientY-bounds.top)*DIAGRAM_HEIGHT/bounds.height};
 }
 function pointerDown(event,node){
  if(!interactive||!onMoveNode||event.button!==0)return;
  const start=point(event);if(!start)return;
  event.preventDefault();
  event.currentTarget.setPointerCapture(event.pointerId);
  moving.current={id:node.id,dx:start.x-node.x,dy:start.y-node.y,start,moved:false,revision,commit:onMoveNode};
  event.currentTarget.focus();
 }
 function pointerMove(event){
  const active=moving.current;if(!active)return;
  const current=point(event);if(!current)return;
  if(Math.hypot(current.x-active.start.x,current.y-active.start.y)>4)active.moved=true;
  if(!active.moved)return;
  const position=clampDiagramPosition(current.x-active.dx,current.y-active.dy);
  latestDrag.current={id:active.id,...position};
  setDrag(latestDrag.current);
 }
 function pointerEnd(){
  const active=moving.current,position=latestDrag.current;
  moving.current=null;latestDrag.current=null;setDrag(null);
  if(!active)return;
  if(active.moved&&position)active.commit({id:active.id,x:position.x,y:position.y,source:'pointer',revision:active.revision});
  select({kind:'node',id:active.id},'pointer');
 }
 function pointerCancel(){moving.current=null;latestDrag.current=null;setDrag(null)}
 function nodeKeyDown(event,node){
  if(!interactive)return;
  if(event.key==='Escape'){onSelect?.(null,{source:'keyboard'});event.currentTarget.blur();return}
  if(event.key==='Enter'||event.key===' '){event.preventDefault();select({kind:'node',id:node.id},'keyboard');return}
  const position=nudgeDiagramPosition(node,event.key);
  if(position&&onMoveNode){event.preventDefault();onMoveNode({id:node.id,...position,source:'keyboard',revision})}
 }
 return <svg ref={svg} className={'ws-diagram-canvas '+className} data-fit={fit||undefined} viewBox={`0 0 ${DIAGRAM_WIDTH} ${DIAGRAM_HEIGHT}`} role={readOnly?'img':'group'} aria-label={title?title+' 도식':'도식'} onPointerDown={event=>{if(event.target===event.currentTarget)select(null,'pointer')}}>
  <defs><marker id={arrow} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="var(--su-muted)"/></marker></defs>
  {edges.map(edge=>{
   const from=nodeById.get(edge.from),to=nodeById.get(edge.to);if(!from||!to)return null;
   const dx=to.x-from.x,dy=to.y-from.y;
   const scale=1/Math.max(Math.abs(dx)/124,Math.abs(dy)/(24+diagramLabelLines(to.label).length*11),1);
   const end={x:to.x-dx*scale,y:to.y-dy*scale};
   const label=edge.label||from.label+' → '+to.label;
   const isSelected=selected?.kind==='edge'&&selected.id===edge.id;
   return <g key={edge.id} role={readOnly?undefined:'button'} tabIndex={interactive?0:-1} aria-label={'연결: '+label} aria-pressed={readOnly?undefined:isSelected} className="ws-diagram-edge" data-selected={isSelected||undefined} data-disabled={!interactive||undefined} onClick={()=>select({kind:'edge',id:edge.id},'pointer')} onKeyDown={event=>{if(interactive&&['Enter',' '].includes(event.key)){event.preventDefault();select({kind:'edge',id:edge.id},'keyboard')}}}>
    <line className="ws-diagram-edge-hit" x1={from.x} y1={from.y} x2={end.x} y2={end.y}/>
    <line className="ws-diagram-edge-line" x1={from.x} y1={from.y} x2={end.x} y2={end.y} markerEnd={'url(#'+arrow+')'}/>
    {edge.label&&<text className="ws-diagram-edge-label" x={(from.x+to.x)/2} y={(from.y+to.y)/2-10} textAnchor="middle">{edge.label}</text>}
   </g>;
  })}
  {shownNodes.map(node=>{
   const words=diagramLabelLines(node.label),height=diagramNodeHeight(node.label);
   const isSelected=selected?.kind==='node'&&selected.id===node.id;
   return <g key={node.id} transform={`translate(${node.x} ${node.y})`} className="ws-diagram-node" data-selected={isSelected||undefined} data-disabled={!interactive||undefined} role={readOnly?undefined:'button'} tabIndex={interactive?0:-1} aria-label={'항목: '+(node.label||'이름 없음')} aria-pressed={readOnly?undefined:isSelected} onFocus={()=>{if(interactive&&!moving.current)select({kind:'node',id:node.id},'focus')}} onPointerDown={event=>pointerDown(event,node)} onPointerMove={pointerMove} onPointerUp={pointerEnd} onPointerCancel={pointerCancel} onKeyDown={event=>nodeKeyDown(event,node)}>
    <rect x="-120" y={-height/2} width="240" height={height} rx="var(--su-radius-sm)"/>
    <text textAnchor="middle" aria-hidden="true">{words.map((word,index)=><tspan key={index} x="0" y={(index-(words.length-1)/2)*22+6}>{word}</tspan>)}</text>
    {node.detail&&<title>{node.label+': '+node.detail}</title>}
   </g>;
  })}
 </svg>;
}
