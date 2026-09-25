import React from 'react';
import {Button} from '@openai/apps-sdk-ui/components/Button';
import {ArtifactPreview} from './ArtifactPreview.jsx';
import {splitContentHeading} from './composition.js';
import './work-canvas.css';

export function WorkCanvas({work,artifactId,onSelect,renderers,prepareContent,resolveMediaUrl}){
 const artifact=work.artifacts.find(a=>a.id===(artifactId||work.activeArtifactId))||work.artifacts[0];
 const split=artifact.kind==='content'?splitContentHeading(artifact.composition):null;
 const hasHeading=artifact.kind==='html'||Boolean(split?.heading)||(artifact.kind==='document'&&artifact.blocks?.[0]?.heading?.trim()===artifact.title?.trim());
 const other=work.artifacts.filter(a=>a.id!==artifact.id&&(a.kind!=='blank'||a.title?.trim()||a.draftText?.trim()));
 return <div className="flow-canvas">
  <article id={'artifact-'+artifact.id} className="flow-canvas-primary" data-kind={artifact.kind} aria-label={artifact.title||work.name} tabIndex={-1}>
   {!hasHeading&&artifact.kind!=='blank'&&artifact.title&&<h1 className="flow-canvas-title">{artifact.title}</h1>}
   {artifact.kind==='blank'?<div className="flow-canvas-empty"><h1>{work.name}</h1>{artifact.draftText?<p>{artifact.draftText}</p>:<p>아직 작업물이 없습니다.</p>}</div>:
    <ArtifactPreview artifact={artifact} renderers={renderers} prepareContent={prepareContent} resolveMediaUrl={resolveMediaUrl} headingLevel={2}/>}
  </article>
  {other.length>0&&<nav className="flow-related-results" aria-label="다른 작업물">{other.map(a=><Button key={a.id} color="primary" variant="ghost" size="md" pill={false} onClick={()=>onSelect?.(a.id)}>{a.title||'제목 없는 작업물'}</Button>)}</nav>}
 </div>;
}
