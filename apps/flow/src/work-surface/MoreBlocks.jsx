import React,{useState} from 'react';
import {Copy,Check,FileText,ExternalLink} from 'lucide-react';
import {Button} from '@openai/apps-sdk-ui/components/Button';
import {safeContentHref,inlineFileSource} from './content-links.js';
import {FilePreview,filePreviewRenderers} from './FilePreview.jsx';
import {workspaceFilePresentation} from './file-types.js';
import './more-blocks.css';
import {BlockTitle} from './BlockTitle.jsx';
import {MediaPlayer} from './MediaPlayer.jsx';
import {ImageDialog} from './ImageDialog.jsx';
import {Disclosure} from './FlowControls.jsx';

export function MetricsBlock({block,context}){
 const {heading,items=[]}=block.content;
 return <section className="ws-metrics">
  {heading&&<BlockTitle context={context}>{heading}</BlockTitle>}
  <dl>{items.map((item,index)=><div key={item.id||index}>
   <dt>{item.label}</dt>
   <dd><strong>{item.value}</strong>{item.unit&&<span className="ws-metric-unit">{item.unit}</span>}</dd>
   {item.detail&&<p>{item.detail}</p>}
  </div>)}</dl>
 </section>;
}

export function BarChartBlock({block,context}){
 const {heading,items=[],unit=''}=block.content;
 const values=items.map(item=>Number(item.value));
 const max=Math.max(1,...values.filter(Number.isFinite));
 const number=new Intl.NumberFormat('ko-KR',{maximumFractionDigits:2});
 return <section className="ws-chart">
  {heading&&<BlockTitle context={context}>{heading}</BlockTitle>}
  <ul>{items.map((item,index)=>{
   const value=Number(item.value),size=Number.isFinite(value)?Math.max(0,Math.min(100,value/max*100)):0;
   return <li key={item.id||index}>
    <span className="ws-chart-label">{item.label}</span>
    <span className="ws-chart-track" aria-hidden="true"><span style={{width:size+'%'}}/></span>
    <span className="ws-chart-value">{Number.isFinite(value)?number.format(value):'—'}{item.unit??unit}</span>
   </li>;
  })}</ul>
  {block.content.caption&&<p className="ws-chart-caption">{block.content.caption}</p>}
 </section>;
}

export function GalleryBlock({block,context={}}){
 const [preview,setPreview]=useState(null);
 const {heading,images=[]}=block.content;
 return <section className="ws-gallery">
  {heading&&<BlockTitle context={context}>{heading}</BlockTitle>}
  <div className="ws-gallery-grid">{images.map((item,index)=><figure key={item.id||index}>
   <button type="button" onClick={()=>context.onOpenMedia?context.onOpenMedia(item):setPreview(item)} aria-label={(item.caption||item.alt||'이미지')+' 확대'}><img src={item.src} alt={item.alt||''} loading="lazy"/></button>
   {item.caption&&<figcaption>{item.caption}</figcaption>}
  </figure>)}</div>
  {!context.onOpenMedia&&<ImageDialog image={preview&&{src:preview.src,alt:preview.alt,title:preview.caption||preview.alt}} onClose={()=>setPreview(null)}/>}
 </section>;
}

export function StepsBlock({block,context}){
 const {heading,steps=[]}=block.content;
 return <section className="ws-steps">
  {heading&&<BlockTitle context={context}>{heading}</BlockTitle>}
  <ol>{steps.map((step,index)=><li key={step.id||index}>
   <span className="ws-step-number" aria-hidden="true">{index+1}</span>
   <div><BlockTitle context={context} sub className="ws-subtitle">{step.title}</BlockTitle>{step.text&&<p>{step.text}</p>}</div>
  </li>)}</ol>
 </section>;
}

export function ReferencesBlock({block,context}){
 const {heading,items=[]}=block.content;
 return <section className="ws-references">
  {heading&&<BlockTitle context={context}>{heading}</BlockTitle>}
  <ul>{items.map((item,index)=>{
   const href=safeContentHref(item.href);
   return <li key={item.id||index}>
    <div><strong>{item.title}</strong>{item.detail&&<p>{item.detail}</p>}</div>
    {href&&<a href={href} target="_blank" rel="noopener noreferrer" aria-label={item.title+' 열기'}><ExternalLink size="1em" aria-hidden="true"/>열기</a>}
   </li>;
  })}</ul>
 </section>;
}

export function FileBlock({block,context}){
 const {name,type,size,description,href}=block.content,link=safeContentHref(href);
 const source=inlineFileSource(href),resolvePresentation=context?.resolveFilePresentation||workspaceFilePresentation;
 const presentation=source&&resolvePresentation(source.path),renderers={...filePreviewRenderers,...context?.filePreviewRenderers};
 const inline=Boolean(presentation&&renderers[presentation.viewer]),text=presentation?.viewer==='text';
 return <section className={'su-panel ws-file'+(inline?' ws-file-inline':'')}>
  <FileText size="1.35em" aria-hidden="true"/>
  <div className="ws-file-body"><BlockTitle context={context}>{name}</BlockTitle><p className="ws-file-metadata">{type&&<span>{type}</span>}{size&&<span>{size}</span>}</p>{description&&<p>{description}</p>}</div>
  {link&&!text&&<a href={link} target="_blank" rel="noopener noreferrer" aria-label={name+' 열기'}><ExternalLink size="1em" aria-hidden="true"/>열기</a>}
  {inline&&<FilePreview href={href} name={name} headingLevel={Math.min(6,(context?.headingLevel||2)+1)} resolvePresentation={resolvePresentation} renderers={renderers}/>}
 </section>;
}

export function CodeBlock({block,context}){
 const {heading,language,code=''}=block.content;
 const [copyState,setCopyState]=useState('idle');
 async function copy(){
  try{await navigator.clipboard.writeText(code);setCopyState('copied')}
  catch{setCopyState('error')}
 }
 return <section className="ws-code">
  <div className="ws-code-heading">{heading&&<BlockTitle context={context}>{heading}</BlockTitle>}<span>{language||'text'}</span><Button color="primary" variant="ghost" pill={false} size="sm" onClick={copy}>{copyState==='copied'?<Check size="1em"/>:<Copy size="1em"/>}{copyState==='copied'?'복사됨':'복사'}</Button></div>
  <pre className="su-code"><code>{code}</code></pre>
  {copyState==='error'&&<p role="alert">복사하지 못했습니다. 코드를 직접 선택해 주세요.</p>}
 </section>;
}

export function AudioBlock({block,context}){
 const {heading,src,caption,transcript}=block.content;
 return <section className="ws-audio">
  {heading&&<BlockTitle context={context}>{heading}</BlockTitle>}
  {src?<MediaPlayer kind="audio" src={src} label={heading||caption||'오디오'}/>:<p>오디오 파일이 연결되지 않았습니다.</p>}
  {caption&&<p>{caption}</p>}
  {transcript&&<Disclosure label="대본"><p>{transcript}</p></Disclosure>}
 </section>;
}

export const additionalRenderers={metrics:MetricsBlock,chart:BarChartBlock,gallery:GalleryBlock,steps:StepsBlock,references:ReferencesBlock,file:FileBlock,code:CodeBlock,audio:AudioBlock};
