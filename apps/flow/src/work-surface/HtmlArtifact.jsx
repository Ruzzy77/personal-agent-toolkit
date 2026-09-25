import React,{useEffect,useMemo,useRef,useState} from 'react';
import {htmlArtifactDocument,htmlArtifactContainer,validHtmlArtifact} from './html-artifact.js';
import './html-artifact.css';
import {readHtmlAssets} from './html-assets.js';
const identity=value=>value;
export function HtmlArtifact({artifact,resolveMediaUrl=identity}){
 const frame=useRef(null),channel=useMemo(()=>crypto.randomUUID(),[artifact.id]);
 const [height,setHeight]=useState(320),[loaded,setLoaded]=useState(null),[error,setError]=useState('');
 const valid=validHtmlArtifact(artifact);
 const assetKey=JSON.stringify([artifact.assets,valid?artifact.assets.map(asset=>resolveMediaUrl(asset.src)):[]]);
 useEffect(()=>{
  const controller=new AbortController();setLoaded(null);setError('');
  if(!valid){setError('작업물 형식을 확인해 주세요.');return()=>controller.abort()}
  readHtmlAssets(artifact,resolveMediaUrl,controller.signal).then(values=>{if(!controller.signal.aborted)setLoaded({key:assetKey,values})})
   .catch(e=>{if(!controller.signal.aborted)setError(e.message)});
  return()=>controller.abort();
  // The manifest is immutable for an artifact revision.
  // eslint-disable-next-line react-hooks/exhaustive-deps
 },[artifact.id,artifact.revision,assetKey,valid]);
 const document=useMemo(()=>loaded?.key===assetKey?htmlArtifactDocument(artifact.html,loaded.values,channel,globalThis.document?.documentElement.dataset.theme||'light'):null,[artifact.html,loaded,assetKey,channel]);
 useEffect(()=>{
  const receive=event=>{if(event.source===frame.current?.contentWindow&&event.data?.channel===channel&&event.data?.type==='flow-artifact-size'&&Number.isFinite(event.data.height))setHeight(Math.min(50000,Math.max(160,event.data.height)))};
  window.addEventListener('message',receive);
  const observer=new MutationObserver(()=>frame.current?.contentWindow?.postMessage({channel,theme:globalThis.document.documentElement.dataset.theme||'light'},'*'));
  observer.observe(globalThis.document.documentElement,{attributes:true,attributeFilter:['data-theme']});
  return()=>{window.removeEventListener('message',receive);observer.disconnect()};
 },[channel]);
 if(error)return <p role="alert">{error}</p>;
 if(!document)return <p role="status">작업물을 여는 중입니다.</p>;
 return <iframe ref={frame} title={artifact.title||'작업물'} className="flow-html-artifact" srcDoc={htmlArtifactContainer(document,channel)} sandbox="allow-scripts" referrerPolicy="no-referrer" style={{height}}/>;
}
