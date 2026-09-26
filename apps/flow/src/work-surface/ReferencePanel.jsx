import React,{useState} from 'react';
import {Button} from '@openai/apps-sdk-ui/components/Button';
import {Unlink} from 'lucide-react';
import {LibraryBrowser} from './LibraryBrowser.jsx';
import {workReferences} from './work-references.js';

export function ReferencePanel({work,sources,root,readReference,readSource,renderSource,renderReference,onDisconnect,busy=false}){
 const items=workReferences(work,sources,root);
 const [removing,setRemoving]=useState(false),[failure,setFailure]=useState(null);
 async function open(item,signal){
  if(item.missing)throw new Error('자료를 찾지 못했습니다.');
  if(item.source){const source=readSource?await readSource(item.source,signal):item.source;return {...item,title:source.title,source,artifact:source.artifact}}
  const resource=await readReference(item.reference,signal);
  return {...item,title:resource.title,resource};
 }
 async function disconnect(item,back){
  setRemoving(true);setFailure(null);
  try{await onDisconnect(item);back()}catch(e){setFailure({id:item.id,message:e.message||'연결을 해제하지 못했습니다.'})}
  finally{setRemoving(false)}
 }
 return <LibraryBrowser items={items} compact showSearch={false} onOpen={open} backLabel="참고 자료" emptyLabel="연결한 자료가 없습니다."
  renderItem={(item,{focusHeading,onHeadingChange,back})=>{
   const actions=onDisconnect?<Button color="primary" variant="outline" size="md" pill={false} disabled={busy||removing} onClick={()=>void disconnect(item,back)}><Unlink size="1em" aria-hidden="true"/>연결 해제</Button>:null;
   return <>
    {failure?.id===item.id&&<p role="alert">{failure.message}</p>}
    {item.source?renderSource(item.source,{focusHeading,onHeadingChange,actions}):renderReference(item.reference,item.resource,item.title,{actions})}
   </>;
  }}/>;
}
