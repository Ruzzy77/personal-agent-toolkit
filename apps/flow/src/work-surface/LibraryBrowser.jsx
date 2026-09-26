import React,{useEffect,useRef,useState} from 'react';
import {Button} from '@openai/apps-sdk-ui/components/Button';
import {Input} from '@openai/apps-sdk-ui/components/Input';
import {ArrowLeft,FileText,Search} from 'lucide-react';
import './library-browser.css';
import {splitContentHeading} from './composition.js';

const asPage=value=>Array.isArray(value)?{items:value,nextCursor:null}:value;
export function LibraryBrowser({items,renderItem,onOpen,onSearch,onLoadMore,hasMore=false,actions,compact=false,showSearch=true,backLabel='자료 목록',emptyLabel='아직 정리한 자료가 없습니다.'}){
 const [query,setQuery]=useState(''),[selectedId,setSelectedId]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[opened,setOpened]=useState({});
 const rows=useRef(new Map()),container=useRef(null),heading=useRef(null),reader=useRef(null);
 const [results,setResults]=useState({term:'',items:[],nextCursor:null,error:'',partial:false}),[paging,setPaging]=useState(false),[searchAttempt,setSearchAttempt]=useState(0);
 const listed=items.find(item=>item.id===selectedId)||results.items.find(item=>item.id===selectedId)||opened[selectedId];
 const loaded=opened[selectedId];
 const selected=listed?(loaded&&listed.revision>loaded.revision?{...loaded,...listed}:{...listed,...loaded}):null;
 const originalHeading=selected?.original?.resolved?.format==='html'&&/<h1(?:\s|>)/i.test(selected.original.resolved.body)||selected?.resource?.resolved?.format==='html'&&/<h1(?:\s|>)/i.test(selected.resource.resolved.body);
 const hasHeading=originalHeading||(selected?.artifact?.kind==='html'?/<h1(?:\s|>)/i.test(selected.artifact.html||''):(selected?.artifact?.kind==='content'&&splitContentHeading(selected.artifact.composition).heading)||selected?.artifact?.blocks?.[0]?.heading===selected?.title);
 const Heading=compact?'h3':'h1';
 const focusHeading=()=>heading.current?.focus({preventScroll:true});
 const term=query.trim();
 useEffect(()=>{
  if(!onSearch||!term)return;
  const controller=new AbortController();
  const timer=setTimeout(()=>{Promise.resolve(onSearch(term,controller.signal)).then(value=>{if(!controller.signal.aborted)setResults({...asPage(value),term,error:''})}).catch(()=>{if(!controller.signal.aborted)setResults({term,items:[],nextCursor:null,error:'자료를 찾지 못했습니다.',partial:false})})},200);
  return()=>{clearTimeout(timer);controller.abort()};
 },[onSearch,term,searchAttempt]);
 useEffect(()=>()=>reader.current?.abort(),[]);
 const searching=Boolean(onSearch&&term&&results.term!==term);
 const matching=onSearch&&term?(results.term===term?results.items:[]):items.filter(item=>[item.title,item.body,item.collection].filter(Boolean).join(' ').toLocaleLowerCase('ko').includes(term.toLocaleLowerCase('ko')));
 async function open(item){
  reader.current?.abort();const controller=new AbortController();reader.current=controller;
  setSelectedId(item.id);setBusy(Boolean(onOpen));setError('');
  requestAnimationFrame(focusHeading);
  try{const resolved=await onOpen?.(item,controller.signal);if(!controller.signal.aborted&&resolved)setOpened(current=>({...current,[item.id]:resolved}))}
  catch(e){if(!controller.signal.aborted)setError(e.message||'자료를 열지 못했습니다.')}
  finally{if(!controller.signal.aborted)setBusy(false)}
 }
 function back(){reader.current?.abort();const id=selectedId;setSelectedId(null);setBusy(false);setError('');requestAnimationFrame(()=>(rows.current.get(id)||container.current)?.focus({preventScroll:true}))}
 async function more(){
  setPaging(true);setError('');
  const controller=new AbortController();reader.current?.abort();reader.current=controller;
  try{
   if(term&&onSearch){const page=asPage(await onSearch(term,controller.signal,results.nextCursor));if(!controller.signal.aborted)setResults(previous=>({...page,term,error:'',items:[...previous.items,...page.items.filter(item=>!previous.items.some(old=>old.id===item.id))]}))}
   else await onLoadMore?.(controller.signal);
  }catch{if(!controller.signal.aborted)setError('목록을 더 불러오지 못했습니다.')}
  finally{if(!controller.signal.aborted)setPaging(false)}
 }
 return <div ref={container} tabIndex={-1} className={'flow-library-browser'+(compact?' is-compact':'')}>
  {selected?<section className="flow-library-detail">
   <div className="su-row"><Button color="primary" variant="ghost" pill={false} size="md" onClick={back}><ArrowLeft size="1em"/>{backLabel}</Button></div>
   <Heading ref={heading} tabIndex={-1} className={hasHeading?'sr-only':'flow-library-detail-title'}>{selected.title}</Heading>
   {error?<div className="su-stack"><p role="alert">{error}</p><div><Button color="primary" variant="ghost" size="md" pill={false} onClick={()=>open(listed)}>다시 열기</Button></div></div>:busy?<p role="status">자료를 여는 중입니다.</p>:renderItem(selected,{focusHeading})}
   {!busy&&!error&&actions?.(selected,{back})}
  </section>:<>
   {showSearch&&<div className="flow-library-search"><Input type="search" size="md" aria-label="라이브러리 검색" placeholder="자료 찾기" value={query} onChange={e=>{reader.current?.abort();setPaging(false);setError("");setQuery(e.target.value)}} startAdornment={<Search size="1em" aria-hidden="true"/>}/></div>}
   {searching?<p role="status">자료를 찾는 중입니다.</p>:onSearch&&term&&results.error?<div className="su-stack"><p role="alert">{results.error}</p><div><Button variant="ghost" size="md" pill={false} onClick={()=>setSearchAttempt(value=>value+1)}>다시 검색</Button></div></div>:matching.length?<ul className="flow-library-list">{matching.map(item=><li key={item.id}>
    <button ref={node=>{if(node)rows.current.set(item.id,node);else rows.current.delete(item.id)}} className="su-tools-item" onClick={()=>open(item)}>
     <FileText size="1em" aria-hidden="true"/><span className="flow-library-name">{item.title}</span>
     {item.collection&&<span className="flow-library-group">{item.collection}</span>}
    </button>
   </li>)}</ul>:<p className="flow-library-empty">{term?'찾는 자료가 없습니다.':emptyLabel}</p>}
   {term&&results.partial&&<div><p role="alert">일부 자료에 연결하지 못했습니다. 연결된 자료부터 표시합니다.</p><Button variant="ghost" size="md" pill={false} onClick={()=>setSearchAttempt(value=>value+1)}>다시 검색</Button></div>}
   {error&&<p role="alert">{error}</p>}
   {!searching&&(term&&onSearch?results.nextCursor!==null:hasMore)&&<div><Button variant="ghost" color="primary" size="md" pill={false} disabled={paging} onClick={()=>void more()}>{paging?'불러오는 중':'더 보기'}</Button></div>}
  </>}
 </div>;
}
