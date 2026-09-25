import React,{useState} from 'react';
import {ZoomIn} from 'lucide-react';
import {Button} from '@openai/apps-sdk-ui/components/Button';
import './content-blocks.css';
import {BlockTitle} from './BlockTitle.jsx';
import {MediaPlayer} from './MediaPlayer.jsx';
import {ImageDialog} from './ImageDialog.jsx';
import {DiagramCanvas} from './DiagramCanvas.jsx';
import {DataTable} from './DataTable.jsx';
import {imageCropGeometry,fullImageRegion} from './image-region.js';

export function HeadingBlock({block,context}){
 const {title,description}=block.content;
 return <header className="ws-heading"><BlockTitle context={context} className="ws-heading-title">{title}</BlockTitle>{description&&<p>{description}</p>}</header>;
}

export function TextBlock({block,context}){
 const {heading,paragraphs=[]}=block.content;
 return <section className="ws-prose">{heading&&<BlockTitle context={context}>{heading}</BlockTitle>}{paragraphs.map((text,i)=><p key={i}>{text}</p>)}</section>;
}

export function ImageBlock({block,context={}}){
 const [preview,setPreview]=useState(false);
 const {src,alt,caption,selection,width,height,crop}=block.content;
 const geometry=imageCropGeometry(width,height,crop),region=crop||fullImageRegion;
 const shown=selection&&(context.selectionVisible?.[block.id]??true);
 const selectionStyle=shown?{
  left:(selection.x-region.x)/region.width*100+'%',top:(selection.y-region.y)/region.height*100+'%',
  width:selection.width/region.width*100+'%',height:selection.height/region.height*100+'%'
 }:undefined;
 return <figure className="ws-image" data-natural={geometry?true:undefined} style={geometry?{aspectRatio:geometry.aspectRatio}:undefined}>
  <img src={src} alt={alt||''} style={geometry?.imageStyle}/>
  {shown&&<span className="ws-image-selection" style={selectionStyle} aria-hidden="true"/>}
  <div className="ws-image-tools">
   {selection&&<Button color="primary" pill={false} variant="ghost" size="sm" aria-pressed={!!shown} onClick={()=>context.onSelectionToggle?.(block)}>영역 표시</Button>}
   <Button color="primary" pill={false} variant="ghost" size="sm" onClick={()=>context.onOpen?context.onOpen(block):setPreview(true)}><ZoomIn size="1em"/>확대</Button>
  </div>
  {caption&&<figcaption>{caption}</figcaption>}
  {!context.onOpen&&<ImageDialog image={preview?{src,alt,title:caption||alt}:null} onClose={()=>setPreview(false)}/>}
 </figure>;
}

export function ComparisonBlock({block,context}){
 const {heading,items=[]}=block.content;
 return <section className="su-panel ws-panel">{heading&&<BlockTitle context={context}>{heading}</BlockTitle>}<div className="ws-comparison">
  {items.map((item,i)=><div className="ws-compare-item" key={item.id||i}>
   <BlockTitle context={context} sub className="ws-subtitle">{item.label}</BlockTitle>
   <div className="ws-crop"><img src={item.src} alt={item.alt||''} style={{width:((item.view?.scale||1)*100)+'%',left:((item.view?.x||0)*100)+'%',top:((item.view?.y||0)*100)+'%'}}/></div>
   {item.caption&&<p>{item.caption}</p>}
  </div>)}
 </div></section>;
}

export function DiagramBlock({block,context}){
 const {heading,nodes=[],edges=[]}=block.content;
 return <section className="ws-diagram-block">
  {heading&&<BlockTitle context={context}>{heading}</BlockTitle>}
  <div className="ws-diagram-frame"><DiagramCanvas title={heading||''} nodes={nodes} edges={edges} readOnly/></div>
 </section>;
}

export function MediaBlock({block,context={}}){
 const [preview,setPreview]=useState(false);
 const {heading,src,poster,alt,caption}=block.content;
 return <section className="su-panel ws-panel ws-media-panel">
  {heading&&<BlockTitle context={context}>{heading}</BlockTitle>}
  {src?<MediaPlayer kind="video" src={src} poster={poster} label={alt||heading}/>:<div className="ws-media-still">
   <img src={poster} alt={alt||''}/>
   <div className="ws-image-tools"><Button color="primary" pill={false} variant="ghost" size="sm" onClick={()=>context.onOpen?context.onOpen(block):setPreview(true)}><ZoomIn size="1em" aria-hidden="true"/>이미지 확대</Button></div>
  </div>}
  {caption&&<p className="ws-media-caption">{caption}</p>}
  {!src&&!context.onOpen&&<ImageDialog image={preview?{src:poster,alt,title:caption||heading||alt}:null} onClose={()=>setPreview(false)}/>}
 </section>;
}

export function TableBlock({block,context}){
 const {heading,columns=[],rows=[]}=block.content;
 return <section className="ws-table-block">
  {heading&&<BlockTitle context={context}>{heading}</BlockTitle>}
  <DataTable columns={columns} rows={rows}/>
 </section>;
}

export const contentRenderers={heading:HeadingBlock,text:TextBlock,image:ImageBlock,comparison:ComparisonBlock,diagram:DiagramBlock,media:MediaBlock,table:TableBlock};
