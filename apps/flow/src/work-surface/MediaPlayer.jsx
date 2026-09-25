import React,{useState} from 'react';
import './media-player.css';

export function MediaPlayer({kind,src,poster,label='',className=''}) {
 const [failedSource,setFailedSource]=useState(null);
 const failed=failedSource===src;
 return <div className={'ws-media-player '+className} data-kind={kind}>
  {kind==='video'
   ? <video src={src} poster={poster} controls preload="metadata" aria-label={label||'영상'} onError={()=>setFailedSource(src)}/>
   : <audio src={src} controls preload="metadata" aria-label={label||'오디오'} onError={()=>setFailedSource(src)}/>}
  {failed&&<p role="alert">파일을 재생할 수 없습니다.</p>}
 </div>;
}
