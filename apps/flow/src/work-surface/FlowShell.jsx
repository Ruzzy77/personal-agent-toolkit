import './flow-shell.css';
import React,{useEffect,useRef,useState} from 'react';
import {Button} from '@openai/apps-sdk-ui/components/Button';
import {Input} from '@openai/apps-sdk-ui/components/Input';
import {Menu} from '@openai/apps-sdk-ui/components/Menu';
import {Dialog,NavigationAction} from '@personal-agent/ui-kit/react';
import {ArrowLeft,BookOpen,Check,ChevronDown,ChevronRight,FileText,FolderOpen,Plus,Search,Settings,X} from 'lucide-react';

export const B=({children,...props})=><Button color="primary" variant="outline" size="md" pill={false} {...props}>{children}</Button>;

export function NavAction({active=false,children,...props}){
 return <NavigationAction current={active} {...props}>{children}</NavigationAction>;
}

const screens=[['work','작업공간',FileText],['library','라이브러리',BookOpen],['files','파일',FolderOpen]];

function WorkMenuItems({groups,currentId,reviewCount,onSelect,onCreate,focusOnMount=false}){
 const [searching,setSearching]=useState(false),[query,setQuery]=useState('');
 const searchAction=useRef(null),searchInput=useRef(null),firstWork=useRef(null);
 useEffect(()=>{if(!focusOnMount)return;const frame=requestAnimationFrame(()=>firstWork.current?.closest('[role="menuitem"]')?.focus());return()=>cancelAnimationFrame(frame)},[focusOnMount]);
 useEffect(()=>{if(!searching)return;const frame=requestAnimationFrame(()=>searchInput.current?.focus({preventScroll:true}));return()=>cancelAnimationFrame(frame)},[searching,query]);
 const all=[...groups.recent,...groups.other],term=query.trim().toLocaleLowerCase('ko');
 const visible=searching?all.filter(work=>work.name.toLocaleLowerCase('ko').includes(term)):all.slice(0,5);
 function searchKeys(event){
  if(event.nativeEvent.isComposing){event.stopPropagation();return}
  if(event.key==='Escape')return;
  event.stopPropagation();
  if(['ArrowDown','ArrowUp','Tab'].includes(event.key)){
   event.preventDefault();
   const menu=event.currentTarget.closest('[role="menu"]'),items=[...menu.querySelectorAll('[role="menuitem"]:not([data-disabled])')];
   (event.key==='ArrowUp'||event.shiftKey?items.at(-1):menu.querySelector('.work-menu-option')||searchAction.current?.closest('[role="menuitem"]'))?.focus();
  }
 }
 function toggleSearch(event){
  event.preventDefault();
  setSearching(value=>!value);setQuery('');
  if(searching)requestAnimationFrame(()=>searchAction.current?.closest('[role="menuitem"]')?.focus());
 }
 return <>
  {searching&&<Menu.Item><Input ref={searchInput} type="search" size="md" aria-label="작업 검색" placeholder="작업 찾기" value={query} autoFocus onChange={event=>setQuery(event.target.value)} onKeyDown={searchKeys} startAdornment={<Search size="1em" aria-hidden="true"/>}/></Menu.Item>}
  {visible.map((work,index)=><Menu.Item key={work.id} className="work-menu-option" onSelect={()=>onSelect(work.id)}>
   <span ref={index===0?firstWork:undefined} className="flow-action-label su-row"><span className="truncate" title={work.name}>{work.name}</span>{reviewCount?.get(work.id)>0&&<small className="work-menu-count">수정안 {reviewCount.get(work.id)}건</small>}{work.id===currentId&&<><Check size="1em" aria-hidden="true"/><span className="sr-only">현재 작업</span></>}</span>
  </Menu.Item>)}
  {!visible.length&&<Menu.Item><span className="muted small" role="status">찾은 작업이 없습니다.</span></Menu.Item>}
  <Menu.Separator/>
  <Menu.Item onSelect={toggleSearch}><span ref={searchAction} className="su-row">{searching?<X size="1em" aria-hidden="true"/>:<Search size="1em" aria-hidden="true"/>}{searching?'검색 닫기':'작업 찾기'}</span></Menu.Item>
  <Menu.Item onSelect={onCreate}><Plus size="1em" aria-hidden="true"/>새 작업</Menu.Item>
 </>;
}


export function AppHeader({screen,onNavigate,workMenu,navigationReturnRef,leading,actions,status,onSettings}){
 const navigationTrigger=useRef(null),workTrigger=useRef(null),settingsTrigger=useRef(null),primaryNavigation=useRef(null),navigationOpen=useRef(false),workOpen=useRef(false),currentNavigation=useRef(null),workOption=useRef(null);
 const [workExpanded,setWorkExpanded]=useState(false);
 const [compact,setCompact]=useState(()=>typeof window!=='undefined'&&window.matchMedia('(max-width: 900px)').matches);
 useEffect(()=>{
  const media=window.matchMedia('(max-width: 900px)');
  const update=()=>{
   const restoreFocus=media.matches?workOpen.current||primaryNavigation.current?.contains(document.activeElement):navigationOpen.current||document.activeElement===navigationTrigger.current;
   const restoreMenuTarget=navigationReturnRef?.current===navigationTrigger.current||primaryNavigation.current?.contains(navigationReturnRef?.current);
   navigationOpen.current=false;workOpen.current=false;
   setCompact(media.matches);setWorkExpanded(false);
   if(restoreMenuTarget)requestAnimationFrame(()=>{navigationReturnRef.current=media.matches?navigationTrigger.current:workTrigger.current});
   if(restoreFocus)requestAnimationFrame(()=>{
    const target=media.matches?navigationTrigger.current:primaryNavigation.current?.querySelector('[aria-current="page"]')||primaryNavigation.current?.querySelector('a');
    target?.focus();
   });
  };
  media.addEventListener('change',update);
  return()=>media.removeEventListener('change',update);
 },[navigationReturnRef]);
 const navigate=onNavigate||((id)=>location.assign('/?screen='+id));
 return <header className="flow-global su-appbar"><div className="flow-global-inner su-appbar__inner">
  <div ref={primaryNavigation} className="primary-navigation">
   <a className="brand" href="/" onClick={onNavigate?event=>{event.preventDefault();navigate('work')}:undefined}>Toolkit</a>
   <nav className="top-navigation" aria-label="주요 화면">
    {screens.map(([id,label,Icon])=>id==='work'&&workMenu?<Menu key={compact?'compact-work':'wide-work'} onOpen={()=>{workOpen.current=true}} onClose={()=>{workOpen.current=false}}>
     <Menu.Trigger><B ref={workTrigger} variant="ghost" size="md" aria-current={screen===id?'page':undefined} selected={screen===id}><Icon size="1em" aria-hidden="true"/>{label}<ChevronDown size="1em" aria-hidden="true"/></B></Menu.Trigger>
     <Menu.Content align="start" alignOffset={0} sideOffset={8} width={280} minWidth="auto" maxHeight={480}><WorkMenuItems {...workMenu} onCreate={()=>workMenu.onCreate(workTrigger.current)}/></Menu.Content>
    </Menu>:<B key={id} variant="ghost" size="md" aria-current={screen===id?'page':undefined} selected={screen===id} onClick={()=>navigate(id)}><Icon size="1em" aria-hidden="true"/>{label}</B>)}
   </nav>
  </div>
  <nav className="compact-navigation" aria-label="주요 화면">
   <Menu key={compact?'compact':'wide'} onOpen={()=>{navigationOpen.current=true;requestAnimationFrame(()=>currentNavigation.current?.closest('[role="menuitem"]')?.focus())}} onClose={()=>{navigationOpen.current=false;setWorkExpanded(false)}}><Menu.Trigger><B ref={navigationTrigger} variant="ghost" size="md" aria-label="주요 화면"><span className="brand">Toolkit</span><ChevronDown size="1em" aria-hidden="true"/></B></Menu.Trigger>
    <Menu.Content align="start" alignOffset={0} width={workExpanded?280:240} minWidth="auto" maxHeight={480} sideOffset={8}>
     {workExpanded&&workMenu?<>
      <Menu.Item onSelect={event=>{event.preventDefault();setWorkExpanded(false);requestAnimationFrame(()=>workOption.current?.closest('[role="menuitem"]')?.focus())}}><ArrowLeft size="1em" aria-hidden="true"/>주요 메뉴</Menu.Item>
      <Menu.Separator/>
      <WorkMenuItems {...workMenu} focusOnMount onCreate={()=>workMenu.onCreate(navigationTrigger.current)}/>
     </>:<>
      {screens.map(([id,label,Icon])=><Menu.Item key={id} onSelect={event=>{if(id==='work'&&workMenu){event.preventDefault();setWorkExpanded(true)}else navigate(id)}}>
       <span ref={node=>{if(id==='work')workOption.current=node;if(screen===id)currentNavigation.current=node}} className="app-navigation-option"><span className="su-row"><Icon size="1em" aria-hidden="true"/><span>{label}</span></span>{(screen===id||id==='work'&&workMenu)&&<span className="su-row">{screen===id&&<><Check size="1em" aria-hidden="true"/><span className="sr-only">현재 화면</span></>}{id==='work'&&workMenu&&<ChevronRight size="1em" aria-hidden="true"/>}</span>}</span>
      </Menu.Item>)}
      {onSettings&&<><Menu.Separator/><Menu.Item onSelect={()=>onSettings(navigationTrigger.current)}><span className="app-navigation-option"><span className="su-row"><Settings size="1em" aria-hidden="true"/><span>설정</span></span></span></Menu.Item></>}
     </>}
    </Menu.Content>
   </Menu>
  </nav>
  {leading&&<div className="toolbar-leading">{leading}</div>}
  {(status||actions||onSettings)&&<div className="global-end">
   {status&&<span className="toolbar-save small muted" aria-live="polite">{status}</span>}
   {actions&&<div className="su-row header-actions">{actions}</div>}
   {onSettings&&<B ref={settingsTrigger} className="global-settings" size="md" variant="ghost" uniform aria-label="설정" onClick={()=>onSettings(settingsTrigger.current)}><Settings size="1em" aria-hidden="true"/></B>}
  </div>}
 </div></header>;
}

export function BrowseToolbar({search,leading,filters,actions,className=''}){
 return <div className={'flow-browse-toolbar su-toolbar '+className}>
  {leading&&<div className="flow-browse-leading">{leading}</div>}
  <div className="flow-browse-search"><Input type="search" size="md" aria-label={search.label} placeholder={search.placeholder} value={search.value} onChange={event=>search.onChange(event.target.value)} startAdornment={<Search size="1em" aria-hidden="true"/>}/></div>
  {filters&&<div className="flow-browse-filters">{filters}</div>}
  {actions&&<div className="flow-browse-actions su-row">{actions}</div>}
 </div>;
}

export function Overlay({open,onClose,title,children,placement='center',returnFocusRef,className='flow-dialog'}){
 return <Dialog open={open} onOpenChange={next=>{if(!next)onClose()}} title={title} placement={placement} returnFocusRef={returnFocusRef} className={className}>{children}</Dialog>;
}
