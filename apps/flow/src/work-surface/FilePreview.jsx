import React from 'react';
import {ImageBlock} from './ContentBlocks.jsx';
import {MediaPlayer} from './MediaPlayer.jsx';
import {PdfPreview} from './PdfPreview.jsx';
import {TextFilePreview} from './TextFilePreview.jsx';
import {inlineFileSource} from './content-links.js';
import {workspaceFilePresentation} from './file-types.js';

export const filePreviewRenderers={
 image:({href,name})=><ImageBlock block={{kind:'image',content:{src:href,alt:name}}}/>,
 video:({href,name})=><MediaPlayer kind="video" src={href} label={name}/>,
 audio:({href,name})=><MediaPlayer kind="audio" src={href} label={name}/>,
 pdf:({href,name})=><PdfPreview href={href} name={name}/>,
 text:({href,name,headingLevel})=><TextFilePreview href={href} name={name} headingLevel={headingLevel}/>,
};

export function FilePreview({href,name='',headingLevel=3,resolvePresentation=workspaceFilePresentation,renderers=filePreviewRenderers}){
 const source=inlineFileSource(href);
 const presentation=source&&resolvePresentation(source.path);
 const Renderer=presentation&&renderers[presentation.viewer];
 return Renderer?<div className="ws-file-preview"><Renderer {...source} name={name} headingLevel={headingLevel}/></div>:null;
}
