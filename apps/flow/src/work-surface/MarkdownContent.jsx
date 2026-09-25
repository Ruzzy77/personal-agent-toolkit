import React,{useMemo} from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import './markdown-content.css';

export function safeMarkdownHref(value){
 if(typeof value!=='string'||/[\u0000-\u001f\u007f]/.test(value))return null;
 if(value.startsWith('#'))return value;
 try{
  const url=new URL(value);
  return ['http:','https:','mailto:'].includes(url.protocol)?value:null;
 }catch{return null}
}

function nodeText(node){
 if(typeof node?.value==='string')return node.value;
 return Array.isArray(node?.children)?node.children.map(nodeText).join(''):'';
}

function omitRepeatedTitle(title){
 return tree=>{
  const first=tree.children?.[0];
  if(first?.type==='heading'&&first.depth===1&&nodeText(first).trim()===title.trim())tree.children.shift();
 };
}

export function MarkdownContent({body,title='',headingLevel=3,className='',resolveImageSrc}){
 const base=Math.max(1,Math.min(6,Number.isInteger(headingLevel)?headingLevel:3));
 const components=useMemo(()=>{
  const heading=depth=>({children})=>{
   const Tag=`h${Math.min(6,base+depth)}`;
   return <Tag>{children}</Tag>;
  };
  return {
   h1:heading(0),h2:heading(1),h3:heading(2),h4:heading(3),h5:heading(4),h6:heading(5),
   a:({href,children,title:linkTitle})=>href
    ?<a href={href} title={linkTitle} target={href.startsWith('#')?undefined:'_blank'} rel={href.startsWith('#')?undefined:'noopener noreferrer'}>{children}</a>
    :<>{children}</>,
   img:({src,alt,title:imageTitle})=>{
    const resolved=resolveImageSrc?.(src);
    return typeof resolved==='string'&&resolved.startsWith('/')&&!resolved.startsWith('//')&&!/[\u0000-\u001f\u007f]/.test(resolved)
     ?<img src={resolved} alt={alt||''} title={imageTitle} loading="lazy"/>
     :<span>{alt||src||'이미지'}</span>;
   },
   pre:({children})=><pre className="su-code">{children}</pre>,
   table:({children})=><div className="ws-markdown-table"><table>{children}</table></div>,
  };
 },[base,resolveImageSrc]);
 const plugins=useMemo(()=>title?[remarkGfm,()=>omitRepeatedTitle(title)]:[remarkGfm],[title]);
 return <div className={'ws-markdown'+(className?' '+className:'')}>
  <Markdown remarkPlugins={plugins} components={components} urlTransform={(value,key)=>key==='src'?value:safeMarkdownHref(value)??''}>{body}</Markdown>
 </div>;
}
