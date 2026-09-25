import React,{useEffect,useRef,useState} from 'react';
import {Field} from '@personal-agent/ui-kit/react';
import {ImageRegionEditor,fullImageRegion,validImageRegion,imageCropGeometry} from './work-surface/index.js';
import {Input} from '@openai/apps-sdk-ui/components/Input';
import {B} from './ui.jsx';
import {Crop,Download,Maximize2,ZoomIn} from 'lucide-react';
const full=fullImageRegion;
export function ImageContent({artifact:a,crop=a.crop,onError}){
 const geometry=imageCropGeometry(a.width,a.height,crop);
 return <img src={a.src} alt={a.alt||a.title} draggable={false} onError={onError} style={geometry?.imageStyle}/>;
}
export function ImageSurface({artifact:a,onChange,onCommit,onSelection,zoom='fit',onZoom=()=>{}}){
 const [mode,setMode]=useState('view'),[rect,setRect]=useState(a.crop||full),[failed,setFailed]=useState(false);
 const baseRevision=useRef(a.revision);
 const crop=mode==='select'?null:mode==='preview'?rect:a.crop;
 const ratio=imageCropGeometry(a.width,a.height,crop)?.aspectRatio||a.width/a.height;
 const valid=validImageRegion(rect),selection=mode==='select';
 useEffect(()=>{onSelection?.((selection||mode==='preview')&&valid?{kind:'image-region',rect}:null)},[mode,rect.x,rect.y,rect.width,rect.height]);
 function begin(){baseRevision.current=a.revision;setRect(a.crop||full);setMode('select');onZoom('fit')}
 async function download(){
  try{const response=await fetch(a.src);if(!response.ok)throw new Error();const url=URL.createObjectURL(await response.blob()),link=document.createElement('a');link.href=url;link.download=a.originalName||a.title;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}catch{setFailed(true)}
 }
 return <div className="su-stack" data-gap="section" onKeyDown={e=>{if(e.key==='Escape')setMode('view')}}>
  <div className="artifact-tools su-toolbar">
   {mode==='preview'?<><span className="small muted">자르기 미리보기</span><div className="su-row"><B variant="solid" onClick={()=>{if(onCommit({crop:rect},baseRevision.current))setMode('view')}}>적용</B><B variant="ghost" onClick={()=>setMode('select')}>범위 조정</B><B variant="ghost" onClick={()=>setMode('view')}>취소</B></div></>:<><div className="su-row">
    <B selected={selection} aria-pressed={selection} onClick={()=>selection?setMode('view'):begin()}><Crop size="1em"/>영역 선택</B>
    {a.crop&&!selection&&<B variant="ghost" onClick={()=>onCommit({crop:null},a.revision)}>자르기 해제</B>}
   </div><div className="su-row">
    <B variant="ghost" uniform disabled={selection} aria-label="화면에 맞추기" title="화면에 맞추기" onClick={()=>onZoom('fit')}><Maximize2 size="1em"/></B>
    <B variant="ghost" uniform disabled={selection} aria-label="실제 크기로 보기" title="실제 크기로 보기" onClick={()=>onZoom('actual')}><ZoomIn size="1em"/></B>
    <B variant="ghost" uniform aria-label="원본 이미지 다운로드" title="원본 이미지 다운로드" onClick={download}><Download size="1em"/></B>
   </div></>}
  </div>
  {selection?<div className="su-stack" data-gap="section">
   {failed?<div className="su-stack"><p className="su-error" role="alert">이미지를 불러오지 못했습니다. 저장소 연결을 확인해 주세요.</p><B onClick={()=>setFailed(false)}>이미지 다시 불러오기</B></div>:
    <ImageRegionEditor src={a.src} alt={a.alt||a.title} width={a.width} height={a.height} value={rect} onChange={setRect} onError={()=>setFailed(true)}/>}
   <div className="su-row"><B variant="solid" disabled={!valid||failed} onClick={()=>setMode('preview')}>자르기 미리보기</B><B variant="ghost" onClick={()=>setMode('view')}>취소</B></div>
  </div>:<div className="image-viewport">
   {failed?<div className="su-stack"><p className="su-error" role="alert">이미지를 불러오지 못했습니다. 저장소 연결을 확인해 주세요.</p><B onClick={()=>setFailed(false)}>이미지 다시 불러오기</B></div>:<div className="image-frame" style={{aspectRatio:ratio,width:zoom==='fit'?'min(100%, '+ratio*65+'vh)':a.width*(crop?.width||1)+'px'}}>
    <ImageContent artifact={a} crop={crop} onError={()=>setFailed(true)}/>
   </div>}
  </div>}
  {mode==='view'&&<details className="image-description"><summary className="small muted">이미지 설명</summary><div className="su-section"><Field label="대체 텍스트"><Input aria-label="대체 텍스트" value={a.alt||''} onChange={e=>onChange({alt:e.target.value})}/></Field></div></details>}
 </div>;
}
