import {HtmlArtifact} from './HtmlArtifact.jsx';
import React,{useState} from 'react';
import {Button} from '@openai/apps-sdk-ui/components/Button';
import {WorkSurface} from './WorkSurface.jsx';
import {splitContentHeading} from './composition.js';
import {DiagramCanvas} from './DiagramCanvas.jsx';
import {imageCropGeometry} from './image-region.js';
import {ImageDialog} from './ImageDialog.jsx';
import './artifact-preview.css';

const identity=value=>value;
const localMedia=value=>typeof value==='string'?value:null;
const clampHeading=value=>Math.max(2,Math.min(6,Number.isInteger(value)?value:4));

function DocumentBlock({block,Heading,page}){
 return <section key={block.id} aria-label={page?page+'번째 장':undefined}>
  {block.heading&&<Heading>{block.heading}</Heading>}
  {block.text&&<p>{block.text}</p>}
 </section>;
}

function DocumentPreview({artifact,headingLevel,primaryHeading}){
 const Heading='h'+clampHeading(headingLevel);
 const blocks=artifact.blocks||[];
 const slides=artifact.format==='slides';
 const [page,setPage]=useState(0);
 const current=Math.min(page,Math.max(0,blocks.length-1));
 return <div className={slides?'fa-document fa-presentation':'fa-document'}>
  {blocks.length===0?<p className="fa-unavailable">내용이 없습니다.</p>:
   slides?<div className="fa-presentation-page"><DocumentBlock block={blocks[current]} Heading={primaryHeading?'h1':Heading} page={current+1}/></div>:
   blocks.map((block,index)=><DocumentBlock key={block.id} block={block} Heading={primaryHeading&&index===0?'h1':Heading}/>)}
  {slides&&blocks.length>0&&<nav className="fa-slide-navigation" aria-label="발표 페이지">
   <Button type="button" color="primary" variant="ghost" pill={false} size="sm" disabled={current===0} onClick={()=>setPage(current-1)}>이전 장</Button>
   <span aria-live="polite">{current+1} / {blocks.length}</span>
   <Button type="button" color="primary" variant="ghost" pill={false} size="sm" disabled={current===blocks.length-1} onClick={()=>setPage(current+1)}>다음 장</Button>
  </nav>}
 </div>;
}

function ImagePreview({artifact,resolveMediaUrl}){
 const [open,setOpen]=useState(false);
 const source=resolveMediaUrl(artifact.src);
 if(!source)return <p className="fa-unavailable" role="status">이미지를 열 수 없습니다.</p>;
 const crop=artifact.crop,geometry=crop&&imageCropGeometry(artifact.width,artifact.height,crop);
 return <figure className="fa-image">
  {geometry?<div className="fa-image-crop" style={{aspectRatio:geometry.aspectRatio}}>
   <img src={source} alt={artifact.alt||artifact.title} style={geometry.imageStyle}/>
  </div>:<img src={source} alt={artifact.alt||artifact.title}/>}
  <div className="fa-image-actions"><Button type="button" color="primary" variant="ghost" pill={false} size="sm" onClick={()=>setOpen(true)}>확대</Button></div>
  <ImageDialog image={open?{src:source,alt:artifact.alt||artifact.title,title:artifact.title}:null} onClose={()=>setOpen(false)}/>
 </figure>;
}

function DiagramPreview({artifact}){
 return <div className="fa-diagram"><DiagramCanvas title={artifact.title} nodes={artifact.nodes||[]} edges={artifact.edges||[]} readOnly/></div>;
}

export function ArtifactPreview({artifact,renderers,prepareContent=identity,resolveMediaUrl=localMedia,headingLevel=4,primaryHeading=false,compact=false,className=''}){
 if(!artifact)return null;
 let content=null;
 if(artifact.kind==='html')content=<HtmlArtifact artifact={artifact} resolveMediaUrl={resolveMediaUrl}/>;
 else if(artifact.kind==='content'&&artifact.composition&&renderers)
  content=<WorkSurface composition={prepareContent(artifact.composition)} renderers={renderers} context={{headingLevel:clampHeading(headingLevel),primaryHeadingId:primaryHeading?splitContentHeading(artifact.composition).heading?.id:undefined}} label={artifact.title+' 내용'}/>;
 else if(artifact.kind==='document')content=<DocumentPreview key={(artifact.id??artifact.title)+':'+(artifact.revision??0)+':'+(artifact.format??'document')} artifact={artifact} headingLevel={headingLevel} primaryHeading={primaryHeading}/>;
 else if(artifact.kind==='image')content=<ImagePreview artifact={artifact} resolveMediaUrl={resolveMediaUrl}/>;
 else if(artifact.kind==='diagram')content=<DiagramPreview artifact={artifact}/>;
 else content=<p className="fa-unavailable">내용이 없습니다.</p>;
 return <div className={'fa-preview '+className} data-compact={compact||undefined}>{content}</div>;
}
