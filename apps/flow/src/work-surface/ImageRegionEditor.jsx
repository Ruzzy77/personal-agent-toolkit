"use client";

import React,{useRef} from 'react';
import {Field} from '@personal-agent/ui-kit/react';
import {Input} from '@openai/apps-sdk-ui/components/Input';
import {fullImageRegion,imageRegionBetween,validImageRegion} from './image-region.js';
import './image-region-editor.css';

const fields=[['x','왼쪽'],['y','위쪽'],['width','너비'],['height','높이']];
const percent=value=>Math.round(value*1000)/10;

export function ImageRegionEditor({src,alt='',width,height,value,onChange,disabled=false,onError}){
 const frame=useRef(null),drag=useRef(null);
 const region=value??fullImageRegion,valid=validImageRegion(region),ratio=width/height;
 const canDraw=Boolean(src&&Number.isFinite(ratio)&&ratio>0&&!disabled);
 function point(event){
  const box=frame.current.getBoundingClientRect();
  return {x:(event.clientX-box.left)/box.width,y:(event.clientY-box.top)/box.height};
 }
 function begin(event){
  if(!canDraw||event.button!==0)return;
  event.preventDefault();
  drag.current={start:point(event),clientX:event.clientX,clientY:event.clientY,before:region,moved:false};
  event.currentTarget.setPointerCapture(event.pointerId);
 }
 function move(event){
  if(!drag.current)return;
  if(Math.hypot(event.clientX-drag.current.clientX,event.clientY-drag.current.clientY)<4&&!drag.current.moved)return;
  drag.current.moved=true;
  onChange(imageRegionBetween(drag.current.start,point(event)));
 }
 function end(event){
  if(!drag.current)return;
  move(event);
  drag.current=null;
 }
 function cancel(){if(drag.current){onChange(drag.current.before);drag.current=null}}
 return <div className="flow-image-region-editor">
  <div className="flow-image-region-stage" ref={frame} role="img" aria-label="이미지에서 영역 선택" style={{aspectRatio:ratio,width:`min(100%, ${ratio*60}vh)`}}
   onPointerDown={begin} onPointerMove={move} onPointerUp={end} onPointerCancel={cancel}>
   <img src={src} alt={alt} draggable={false} onError={onError}/>
   {valid&&<span className="flow-image-region-mark" aria-hidden="true" style={{left:percent(region.x)+'%',top:percent(region.y)+'%',width:percent(region.width)+'%',height:percent(region.height)+'%'}}/>}
  </div>
  <div className="flow-image-region-controls">
   <p className="flow-image-region-hint">이미지에서 영역을 끌거나 아래 값을 입력하세요.</p>
   <div className="flow-image-region-fields">{fields.map(([key,label])=><Field label={label+' (%)'} key={key}><Input aria-label={label+' (%)'} type="number" min={key==='width'||key==='height'?0.1:0} max={100} step={0.1} value={percent(region[key])} disabled={disabled} onChange={event=>onChange({...region,[key]:Number(event.target.value)/100})}/></Field>)}</div>
   {!valid&&<p className="flow-image-region-error" role="alert">선택 영역이 이미지 안에 들어오도록 조정해 주세요.</p>}
  </div>
 </div>;
}
