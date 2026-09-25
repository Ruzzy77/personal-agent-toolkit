"use client";
import {useEffect,useState} from 'react';
import {readUIKitAsset} from '../../lib/flow-uikit-client';
import type {UIKitAsset} from '../../lib/flow-uikit';
export function FlowUIKitReference({id,revision}:{id:string;revision:string}){
 const key=id+':'+revision;
 const [result,setResult]=useState<{key:string;item?:UIKitAsset;error?:boolean}|null>(null);
 useEffect(()=>{
  const controller=new AbortController();
  void readUIKitAsset(id,revision,controller.signal).then(item=>{if(!controller.signal.aborted)setResult({key,item})}).catch(()=>{if(!controller.signal.aborted)setResult({key,error:true})});
  return()=>controller.abort();
 },[id,revision,key]);
 const current=result?.key===key?result:null;
 if(current?.error)return <p role="alert" className="flow-muted">UIKit 자료를 열지 못했습니다. 원본에서 다시 확인해 주세요.</p>;
 const item=current?.item;
 if(!item)return <p role="status" className="flow-muted">UIKit 자료를 불러오는 중입니다.</p>;
 return <article className="flow-design-reference"><p>{item.description}</p>{item.preview?<div className="flow-design-preview"><iframe title={item.title+' 미리보기'} src={'/api/uikit/preview?'+new URLSearchParams({id,revision})} sandbox="allow-scripts" referrerPolicy="no-referrer"/></div>:<p className="flow-muted">파일과 사용 방법은 UIKit에서 확인할 수 있습니다.</p>}</article>;
}
