import React,{useEffect,useState} from 'react';
import {inlineTextHref,markdownImageHref} from './content-links.js';
import {TextContentView} from './TextContentView.jsx';
import {HtmlContentView} from './HtmlContentView.jsx';
import './text-file-preview.css';

export function TextFilePreview({href,name='',headingLevel=3}){
 const [state,setState]=useState({href:null,body:null,error:''});
 useEffect(()=>{
  const source=inlineTextHref(href),controller=new AbortController();
  setState({href,body:null,error:''});
  if(!source){setState({href,body:null,error:'파일 미리보기를 사용할 수 없습니다.'});return()=>controller.abort()}
  const path=new URL(source,'http://flow.local').searchParams.get('path');
  fetch(source,{signal:controller.signal}).then(async response=>{
   if(!response.ok||response.headers.get('content-type')?.split(';',1)[0]!=='application/json')throw new Error();
   const result=await response.json();
   if(result?.type!=='text/plain'||result.path!==path||typeof result.content!=='string'||result.content.length>524288)throw new Error();
   return result.content;
  }).then(body=>{if(!controller.signal.aborted)setState({href,body,error:''})})
   .catch(()=>{if(!controller.signal.aborted)setState({href,body:null,error:'파일을 표시하지 못했습니다. 파일 화면에서 확인해 주세요.'})});
  return()=>controller.abort();
 },[href]);
 const path=inlineTextHref(href)?new URL(href,'http://flow.local').searchParams.get('path'):'';
 return <section className="ws-text-preview" aria-label={name+' 내용'}>
  {state.href!==href||state.body===null&&!state.error?<p role="status">파일을 여는 중입니다.</p>:null}
  {state.href===href&&state.error?<p role="alert">{state.error}</p>:null}
  {state.href===href&&state.body!==null?(/\.html?$/i.test(path||'')
   ?<HtmlContentView key={href} body={state.body} name={name}/>
   :<TextContentView key={href} body={state.body} path={path||''} title={name} headingLevel={headingLevel} resolveImageSrc={src=>markdownImageHref(href,src)}/>):null}
 </section>;
}
