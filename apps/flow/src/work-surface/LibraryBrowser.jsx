import React,{useEffect,useRef,useState} from 'react';
import {Button} from '@openai/apps-sdk-ui/components/Button';
import {Input} from '@openai/apps-sdk-ui/components/Input';
import {ArrowLeft,FileText,Search} from 'lucide-react';
import './library-browser.css';
import {splitContentHeading} from './composition.js';

export function LibraryBrowser({items,renderItem,onOpen,onSearch,actions,compact=false,emptyLabel='아직 정리한 자료가 없습니다.'}){
 const [query,setQuery]=useState(''),[selectedId,setSelectedId]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const rows=useRef(new Map()),heading=useRef(null),request=useRef(0);
 const [results,setResults]=useState({term:'',items:[],error:''});
 const selected=items.find(item=>item.id===selectedId)||results.items.find(item=>item.id===selectedId);
 const hasHeading=selected?.artifact?.kind==='html'||(selected?.artifact?.kind==='content'&&splitContentHeading(selected.artifact.composition).heading)||selected?.artifact?.blocks?.[0]?.heading===selected?.title;
 const term=query.trim().toLocaleLowerCase('ko');
 useEffect(()=>{
  if(!onSearch||!term)return;
  const controller=new AbortController();
  const timer=setTimeout(()=>{Promise.resolve(onSearch(term,controller.signal)).then(found=>{if(!controller.signal.aborted)setResults({term,items:found,error:''})}).catch(()=>{if(!controller.signal.aborted)setResults({term,items:[],error:'자료를 찾지 못했습니다. 다시 검색해 주세요.'})})},200);
  return()=>{clearTimeout(timer);controller.abort()};
 },[onSearch,term]);
 const searching=Boolean(onSearch&&term&&results.term!==term);
 const matching=onSearch&&term?(results.term===term?results.items:[]):items.filter(item=>[item.title,item.body,item.collection].filter(Boolean).join(' ').toLocaleLowerCase('ko').includes(term));
 async function open(item){
  const number=++request.current;setSelectedId(item.id);setBusy(Boolean(onOpen));setError('');
  requestAnimationFrame(()=>heading.current?.focus({preventScroll:true}));
  try{await onOpen?.(item)}catch(e){if(number===request.current)setError(e.message||'자료를 열지 못했습니다.')}
  finally{if(number===request.current)setBusy(false)}
 }
 function back(){request.current++;const id=selectedId;setSelectedId(null);setError('');requestAnimationFrame(()=>rows.current.get(id)?.focus({preventScroll:true}))}
 return <div className={'flow-library-browser'+(compact?' is-compact':'')}>
  {selected?<section className="flow-library-detail">
   <div className="su-row"><Button color="primary" variant="ghost" pill={false} size="md" onClick={back}><ArrowLeft size="1em"/>자료 목록</Button></div>
   <h1 ref={heading} tabIndex={-1} className={hasHeading?'sr-only':undefined}>{selected.title}</h1>
   {error?<p role="alert">{error}</p>:busy?<p role="status">자료를 여는 중입니다.</p>:renderItem(selected)}
   {!busy&&!error&&actions?.(selected)}
  </section>:<>
   <div className="flow-library-search"><Input type="search" size="md" aria-label="라이브러리 검색" placeholder="자료 찾기" value={query} onChange={e=>setQuery(e.target.value)} startAdornment={<Search size="1em" aria-hidden="true"/>}/></div>
   {searching?<p role="status">자료를 찾는 중입니다.</p>:onSearch&&term&&results.term===term&&results.error?<p role="alert">{results.error}</p>:matching.length?<ul className="flow-library-list">{matching.map(item=><li key={item.id}>
    <button ref={node=>{if(node)rows.current.set(item.id,node);else rows.current.delete(item.id)}} className="su-tools-item" onClick={()=>open(item)}>
     <FileText size="1em" aria-hidden="true"/><span className="flow-library-name">{item.title}</span>
     {item.collection&&<span className="flow-library-group">{item.collection}</span>}
    </button>
   </li>)}</ul>:<p className="flow-library-empty">{term?'찾는 자료가 없습니다.':emptyLabel}</p>}
  </>}
 </div>;
}
