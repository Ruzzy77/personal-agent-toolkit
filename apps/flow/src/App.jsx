import React,{useEffect,useLayoutEffect,useRef,useState} from 'react';
import {B,AppHeader,Overlay} from './ui.jsx';
import {Field,UIKitRoot} from '@personal-agent/ui-kit/react';
import {Input} from '@openai/apps-sdk-ui/components/Input';
import {Textarea} from '@openai/apps-sdk-ui/components/Textarea';
import {Menu} from '@openai/apps-sdk-ui/components/Menu';
import {Tooltip} from '@openai/apps-sdk-ui/components/Tooltip';
import {SegmentedControl} from '@openai/apps-sdk-ui/components/SegmentedControl';
import {Archive,Info,Download,Moon,Sun,FileDiff,FileText,X,MoreHorizontal,Undo2} from 'lucide-react';
import {activeArtifact,newWork,workPickerGroups,reviewCounts,libraryItems,sourceItems,saveSnapshot} from './model.js';
import {libraryEntryKey} from './library.js';
import {useWorkspace,downloadText} from './useWorkspace.js';
import {FileExplorer} from './FileExplorer.jsx';
import {LibraryView,LibraryItemBody} from './LibraryView.jsx';
import {disconnectWorkReference} from './work-surface/work-references.js';
import {ChangesPanel} from './ChangesPanel.jsx';
import {WorkCanvas,ReferencePanel,contentRenderers,SurfaceHeader,exportHtmlArtifact} from './work-surface/index.js';

const VIEW_KEY='toolkit-flow-view-v1';
function useViewState(){
 const [view,render]=useState(()=>{let value={};try{value=JSON.parse(localStorage.getItem(VIEW_KEY))||{}}catch{}
  const params=new URLSearchParams(location.search),work=params.get('work'),artifact=params.get('artifact'),screen=params.get('screen');
  return {...value,...(work?{screen:'work',activeId:work,artifactIds:artifact?{...value.artifactIds,[work]:artifact}:value.artifactIds}:['work','library','files'].includes(screen)?{screen}:{})};
 });
 return [view,update=>render(previous=>{const next=typeof update==='function'?update(previous):update;try{localStorage.setItem(VIEW_KEY,JSON.stringify(next))}catch{}return next})];
}
export function App(){
 const {store,setStore,status,workspaceName,toolkitUrl,ready,retry,recover,exportCurrent}=useWorkspace();
 const [view,setView]=useViewState(),[panel,setPanel]=useState(null),[message,setMessage]=useState('');
 const [newName,setNewName]=useState(''),[newSource,setNewSource]=useState(null),[busy,setBusy]=useState(false);
 const [wide,setWide]=useState(()=>matchMedia('(min-width:1200px)').matches);
 const returnRef=useRef(null),sourceTrigger=useRef(null),moreTrigger=useRef(null),timer=useRef(null),scroll=useRef({});
 const work=store.works.find(w=>w.id===(view.activeId||store.activeId))||store.works[0];
 const a=work.artifacts.find(item=>item.id===view.artifactIds?.[work.id])||activeArtifact(work);
 const screen=view.screen||'work',position=screen==='work'?'work:'+work.id:screen;
 const items=libraryItems(store),sources=sourceItems(store);
 const counts=reviewCounts(store.changes),review=(store.changes||[]).some(c=>c.workId===work.id&&['review','conflict'].includes(c.status));
 const undo=(store.changes||[]).findLast(c=>c.workId===work.id&&c.artifactId===a.id&&c.status==='completed'&&c.before&&c.appliedRevision===a.revision);
 useEffect(()=>{const media=matchMedia('(min-width:1200px)'),update=()=>setWide(media.matches);media.addEventListener('change',update);return()=>media.removeEventListener('change',update)},[]);
 useLayoutEffect(()=>{window.scrollTo(0,scroll.current[position]||0)},[position]);
 useEffect(()=>()=>clearTimeout(timer.current),[]);
 function toast(text){setMessage(text);clearTimeout(timer.current);timer.current=setTimeout(()=>setMessage(''),3200)}
 function close(){setPanel(null);requestAnimationFrame(()=>returnRef.current?.focus())}
 function navigate(next){scroll.current[position]=scrollY;setView(v=>({...v,screen:next}));setPanel(null)}
 function openWork(id,artifactId){scroll.current[position]=scrollY;setView(v=>({...v,screen:'work',activeId:id,recentIds:[id,...(v.recentIds||[]).filter(x=>x!==id)].slice(0,5),artifactIds:artifactId?{...v.artifactIds,[id]:artifactId}:v.artifactIds}));setPanel(null)}
 function selectArtifact(id){setView(v=>({...v,artifactIds:{...v.artifactIds,[work.id]:id}}));window.scrollTo(0,0)}
 function patchWork(patch){setStore(s=>({...s,works:s.works.map(w=>w.id===work.id?{...w,...patch}:w)}))}
 function connect(item){
  if(work.sourceIds.includes(item.id)){toast('이미 연결한 자료입니다.');return}
  setStore(s=>({...s,works:s.works.map(w=>w.id===work.id?{...w,sourceIds:[...w.sourceIds,item.id]}:w),
   linkedFiles:item.filePath&&!s.libraryEntries.some(e=>e.id===item.id)?[...(s.linkedFiles||[]).filter(v=>v.id!==item.id),item]:s.linkedFiles}));
  toast('참고 자료로 연결했습니다.');
 }
 function create(){
  if(!newName.trim())return;
  const w=newWork(newName,newSource);
  setStore(s=>({...s,works:[...s.works,w],linkedFiles:newSource?.filePath?[...(s.linkedFiles||[]).filter(item=>item.id!==newSource.id),newSource]:s.linkedFiles}));
  openWork(w.id);setNewName('');setNewSource(null);
 }
 function fromItem(item){setNewSource(item);setNewName(item.title);setPanel('new')}
 function save(){
  if((store.librarySnapshots||[]).some(s=>s.artifactId===a.id&&s.artifact.revision===a.revision)){toast('이미 라이브러리에 있습니다.');return}
  setStore(s=>saveSnapshot(s,work.id,a.id));toast('라이브러리에 추가했습니다.');
 }
 function collectFile(file){
  const entry={id:crypto.randomUUID(),title:file.name,revision:1,scope:{kind:'work',workId:work.id},reference:{kind:'host-file',root:'workspace',path:file.path},body:''};
  if(store.libraryEntries.some(e=>libraryEntryKey(e)===libraryEntryKey(entry))){toast('이미 라이브러리에 있습니다.');return}
  setStore(s=>({...s,libraryEntries:[...s.libraryEntries,entry]}));toast('라이브러리에 추가했습니다.');
 }
 function updateEntry(entry,patch){
  setStore(s=>({...s,libraryEntries:s.libraryEntries.map(e=>{if(e.id!==entry.id)return e;if(e.revision!==entry.revision)throw new Error('자료가 변경되었습니다. 다시 열어 주세요.');return {...e,...patch,revision:e.revision+1}})}));
 }
 async function restore(){
  if(!undo||busy)return;setBusy(true);
  try{const response=await fetch('/api/flow/changes/'+encodeURIComponent(undo.id)+'/undo',{method:'POST',headers:{'X-Toolkit-Flow':'1','Content-Type':'application/json'},body:JSON.stringify({workspaceId:store.workspaceId})});const data=await response.json();if(!response.ok)throw new Error(data.error);await retry();toast('이전 상태로 되돌렸습니다.')}catch(e){toast(e.message)}finally{setBusy(false)}
 }
 async function exportArtifact(){
  try{
  const filename=(a.title||work.name).replace(/[\\/:*?"<>|]/g,'-');
  if(a.kind==='html')downloadText(filename+'.html',await exportHtmlArtifact(a),'text/html;charset=utf-8');
  else if(a.kind==='document')downloadText(filename+'.md',a.blocks.map(b=>[b.heading&&'## '+b.heading,b.text].filter(Boolean).join('\n\n')).join('\n\n'),'text/markdown;charset=utf-8');
  else downloadText(filename+'.json',JSON.stringify(a,null,2));
  }catch(e){toast(e.message||'작업물을 내려받지 못했습니다.');}
 }
 const libraryProps={items,work,workspaceId:store.workspaceId,toolkitUrl,onConnect:connect,onFrom:fromItem,onOpenWork:openWork,onUpdateEntry:updateEntry};
 const filesProps={workspaceName,workspaceId:store.workspaceId,work,onConnect:connect,onFrom:fromItem,onCollect:collectFile,library:items,toast};
 const panelTitle={sources:'참고 자료',changes:'변경 확인',context:'작업 정보',settings:'설정',new:'새 작업'}[panel];
 const auxiliary=screen==='work'&&wide&&['sources','changes','context'].includes(panel);
 const panelContent=panel==='sources'?<ReferencePanel key={work.id} work={work} sources={sources} root="workspace"
  renderSource={item=><LibraryItemBody item={item} workspaceId={store.workspaceId} toolkitUrl={toolkitUrl}/>}
  renderReference={(reference,resource,title)=><LibraryItemBody item={{title,reference,...(reference.kind==='host-file'?{live:true,filePath:reference.path}:{})}} workspaceId={store.workspaceId} toolkitUrl={toolkitUrl}/>}
  onDisconnect={item=>setStore(s=>({...s,works:s.works.map(w=>w.id===work.id?{...w,...disconnectWorkReference(w,item)}:w)}))}/>:panel==='changes'?<ChangesPanel work={work} artifact={a} changes={store.changes||[]} workspaceId={store.workspaceId} onApplied={retry}/>:
 panel==='context'?<div className="su-stack" data-gap="section">
  <Field label="작업 이름"><Input value={work.name} onChange={e=>patchWork({name:e.target.value})}/></Field>
  <Field label="작업 목적"><Textarea value={work.purpose||''} onChange={e=>patchWork({purpose:e.target.value})} rows={3}/></Field>
 </div>:panel==='settings'?<div className="su-stack"><h3>화면</h3><SegmentedControl value={view.theme||store.theme} onChange={theme=>setView(v=>({...v,theme}))} aria-label="화면 테마" size="md" pill={false}><SegmentedControl.Option value="light"><Sun size="1em"/>밝게</SegmentedControl.Option><SegmentedControl.Option value="dark"><Moon size="1em"/>어둡게</SegmentedControl.Option></SegmentedControl></div>:
 panel==='new'?<form className="su-stack" data-gap="section" onSubmit={e=>{e.preventDefault();create()}}>
  <Field label="작업 이름"><Input value={newName} onChange={e=>setNewName(e.target.value)} autoFocus maxLength={100}/></Field>
  {newSource&&<p className="small muted">{newSource.title}</p>}
  <div className="su-row"><B type="submit" variant="solid" disabled={!newName.trim()}>시작하기</B><B variant="ghost" onClick={close}>취소</B></div>
 </form>:null;
 return <UIKitRoot colorScheme={view.theme||store.theme}>
  <div className="app-shell" inert={!ready||undefined}>
   <a className="su-skip" href="#flow-main">본문으로</a>
   <AppHeader screen={screen} onNavigate={navigate} navigationReturnRef={returnRef} onSettings={trigger=>{returnRef.current=trigger;setPanel('settings')}}
    workMenu={{groups:workPickerGroups(store.works,work.id,view.recentIds||[]),currentId:work.id,reviewCount:counts,onSelect:openWork,onCreate:trigger=>{returnRef.current=trigger;setNewSource(null);setNewName('');setPanel('new')}}}
    actions={screen==='work'&&<>
     {undo&&<Tooltip content="이전 상태로" compact><B uniform size="md" variant="ghost" aria-label="이전 상태로" disabled={busy} onClick={restore}><Undo2 size="1em"/></B></Tooltip>}
     <Tooltip content="참고 자료" compact><B ref={sourceTrigger} uniform size="md" variant={panel==='sources'?'soft':'ghost'} aria-label="참고 자료" aria-expanded={panel==='sources'} onClick={()=>{returnRef.current=sourceTrigger.current;setPanel(panel==='sources'?null:'sources')}}><FileText size="1em"/></B></Tooltip>
     <Menu><Menu.Trigger><B ref={moreTrigger} uniform size="md" variant="ghost" aria-label="더 보기"><MoreHorizontal size="1em"/></B></Menu.Trigger><Menu.Content align="end">
      {a.kind!=='blank'&&<Menu.Item onSelect={save}><Archive size="1em"/>라이브러리에 추가</Menu.Item>}
      <Menu.Item onSelect={()=>{returnRef.current=moreTrigger.current;setPanel('context')}}><Info size="1em"/>작업 정보</Menu.Item>
      {review&&<Menu.Item onSelect={()=>{returnRef.current=moreTrigger.current;setPanel('changes')}}><FileDiff size="1em"/>변경 확인</Menu.Item>}
      {a.kind!=='blank'&&<Menu.Item onSelect={exportArtifact}><Download size="1em"/>내려받기</Menu.Item>}
     </Menu.Content></Menu>
    </>}/>
   <div className="flow-body"><main id="flow-main" className="main-surface">
    {status.message&&<div className="storage-notice su-stack"><p role="alert">{status.message}</p><div className="su-row"><B onClick={retry}>다시 연결</B><B variant="ghost" onClick={exportCurrent}>현재 내용 내려받기</B>{status.phase==='conflict'&&<B variant="ghost" onClick={()=>recover().catch(e=>toast(e.message))}>저장본 다시 열기</B>}</div></div>}
    <div hidden={screen!=='work'}><WorkCanvas work={work} artifactId={a.id} onSelect={selectArtifact} renderers={contentRenderers}/></div>
    <div hidden={screen!=='library'}><LibraryView {...libraryProps}/></div>
    <div hidden={screen!=='files'}><FileExplorer {...filesProps}/></div>
   </main>{auxiliary&&<aside className="flow-inspector" aria-label={panelTitle}><SurfaceHeader className="inspector-head" title={<h2>{panelTitle}</h2>} actions={<B uniform size="sm" variant="ghost" aria-label={panelTitle+' 닫기'} onClick={close}><X size="1em"/></B>}/>{panelContent}</aside>}</div>
  </div>
  {!ready&&<div className="startup-state su-stack" role="status"><p>{status.message||'작업을 여는 중입니다.'}</p>{status.phase==='error'&&<B onClick={exportCurrent}>기존 내용 내려받기</B>}</div>}
  {message&&<div className="su-toast" role="status">{message}</div>}
  <Overlay open={Boolean(panel)&&!auxiliary} title={panelTitle||''} onClose={close} returnFocusRef={returnRef} placement={panel==='sources'?'right':'center'}>{panelContent}</Overlay>
 </UIKitRoot>;
}
