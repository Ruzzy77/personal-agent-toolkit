import React,{useEffect,useRef,useState} from 'react';
import {Folder,FileText,Image as ImageIcon,Film,Music2,ArrowUp,ArrowDown,ChevronRight,X,Download,Link2,Archive,Plus} from 'lucide-react';
import {B,BrowseToolbar} from './ui.jsx';
import {ListItemAction,SurfaceHeader,ImageDialog,MediaPlayer,PdfPreview,TextContentView,HtmlContentView,markdownImageHref} from './work-surface/index.js';
import {filePreviewKind,fileContentUrl} from './file-preview.js';
import {workspaceFileSource} from './model.js';

const fileType=file=>file.type==='directory'?'폴더':({image:'이미지',video:'영상',audio:'오디오',pdf:'PDF',html:'HTML'}[filePreviewKind(file.name)]||'파일');
function FileIcon({file}){const Icon=file.type==='directory'?Folder:({image:ImageIcon,video:Film,audio:Music2}[filePreviewKind(file.name)]||FileText);return <Icon className="flow-list-icon" aria-hidden="true"/>}
export function FileExplorer({compact=false,workspaceId,workspaceName='작업공간',work,onConnect,onFrom,onCollect,library,toast}){
 const [path,setPath]=useState(()=>{try{return localStorage.getItem('toolkit-flow-file-path')||''}catch{return ''}});
 const [items,setItems]=useState([]),[selected,setSelected]=useState(null),[preview,setPreview]=useState(null),[imageOpen,setImageOpen]=useState(false);
 const [query,setQuery]=useState(''),[descending,setDescending]=useState(false),[loading,setLoading]=useState(true),[error,setError]=useState('');
 const detailTitle=useRef(),rowRefs=useRef(new Map()),previewRequest=useRef(0);
 useEffect(()=>{let cancelled=false;previewRequest.current++;setLoading(true);setError('');setSelected(null);setPreview(null);if(location.protocol==='file:'){setLoading(false);setError('파일 탐색은 연결된 화면에서 사용할 수 있습니다.');return}
  fetch('/api/flow/files?workspaceId='+encodeURIComponent(workspaceId)+'&path='+encodeURIComponent(path),{headers:{'X-Toolkit-Flow':'1'}})
   .then(async response=>{const data=await response.json();if(!response.ok)throw new Error(data.error);return data})
   .then(data=>{if(!cancelled){setItems(data.items);setLoading(false)}})
   .catch(e=>{if(!cancelled){setItems([]);setError(e.message||'파일을 열지 못했습니다.');setLoading(false)}});
  return()=>{cancelled=true};
 },[path,workspaceId]);
 useEffect(()=>{if(selected){detailTitle.current?.focus({preventScroll:true});detailTitle.current?.scrollIntoView({block:'nearest'})}},[selected?.path]);
 const visible=items.filter(item=>item.name.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()))
  .sort((a,b)=>(a.type===b.type?0:a.type==='directory'?-1:1)||(descending?-1:1)*a.name.localeCompare(b.name,'ko'));
 const parts=path.split('/').filter(Boolean),linked=selected&&work.sourceIds.includes('workspace-file:'+selected.path);
 const collected=selected&&library.some(item=>item.filePath===selected.path);
 function navigate(next){previewRequest.current++;setImageOpen(false);setPath(next);setQuery('');try{localStorage.setItem('toolkit-flow-file-path',next)}catch{}}
 async function open(file){
  if(file.type==='directory'){navigate(file.path);return}
  const request=++previewRequest.current,kind=filePreviewKind(file.name),src=fileContentUrl(workspaceId,file.path);
  setSelected(file);setPreview(null);setImageOpen(false);
  try{
   if(kind==='image'||kind==='video'||kind==='audio'||kind==='pdf'){
    const response=await fetch(src,{method:'HEAD',headers:{'X-Toolkit-Flow':'1'}});
    const expected=kind==='pdf'?'application/pdf':kind+'/';
    if(!response.ok||(kind==='pdf'?response.headers.get('content-type')!==expected:!response.headers.get('content-type')?.startsWith(expected)))
     throw new Error(kind==='image'?'이미지를 열 수 없습니다.':kind==='pdf'?'PDF를 열 수 없습니다.':'이 파일을 재생할 수 없습니다.');
    if(previewRequest.current===request)setPreview({kind,src});
    return;
   }
   const response=await fetch(src,{headers:{'X-Toolkit-Flow':'1'}});
   const data=await response.json();
   if(!response.ok)throw new Error(data.error);
   if(previewRequest.current===request)setPreview({kind,content:data.content});
  }catch(e){if(previewRequest.current===request)setPreview({kind:'error',message:e.message||'미리보기를 열지 못했습니다.'})}
 }
 function close(){previewRequest.current++;const old=selected?.path;setSelected(null);setPreview(null);setImageOpen(false);requestAnimationFrame(()=>rowRefs.current.get(old)?.focus())}
 async function download(){
  if(!selected||!preview||preview.kind==='error')return;
  try{
   let blob;
   if(preview.kind==='text'||preview.kind==='html')blob=new Blob([preview.content],{type:preview.kind==='html'?'text/html;charset=utf-8':'text/plain;charset=utf-8'});
   else{
    const response=await fetch(preview.src,{headers:{'X-Toolkit-Flow':'1'}});
    if(!response.ok)throw new Error();
    blob=await response.blob();
   }
   const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=selected.name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }catch{toast('파일을 내려받지 못했습니다.')}
 }
 const currentItem=selected?workspaceFileSource(selected):null;
 return <div className={compact?"files-main is-compact":"files-main"}>
  <nav className="file-breadcrumbs su-row" aria-label="폴더 경로"><B variant="ghost" size="sm" onClick={()=>navigate('')}>{workspaceName}</B>{compact
   ?parts.length>0&&<><ChevronRight size={14} aria-hidden="true"/>{parts.length>1&&<span aria-hidden="true">…</span>}<span className="file-current-folder" title={path} aria-current="location">{parts.at(-1)}</span></>
   :parts.map((part,i)=><React.Fragment key={i}><ChevronRight size={14}/><B variant="ghost" size="sm" aria-current={i===parts.length-1?'location':undefined} onClick={()=>navigate(parts.slice(0,i+1).join('/'))}>{part}</B></React.Fragment>)}</nav>
  <BrowseToolbar className="files-toolbar" leading={<B variant="ghost" size="md" uniform aria-label="상위 폴더" disabled={!path} onClick={()=>navigate(parts.slice(0,-1).join('/'))}><ArrowUp size="1em"/></B>} search={{label:'파일 이름 검색',placeholder:'이 폴더에서 찾기',value:query,onChange:setQuery}}/>

  <div className={'file-columns '+(selected?'has-selection':'')}>
   <section className="file-list-pane" aria-label="파일 목록"><div className="su-table-wrap flow-browse-table"><table className="su-table file-table" data-layout="fixed"><thead><tr><th scope="col" aria-sort={descending?'descending':'ascending'}><B variant="ghost" size="md" onClick={()=>setDescending(v=>!v)}>이름{descending?<ArrowUp size={14}/>:<ArrowDown size={14}/>}</B></th><th className="file-type" scope="col">종류</th></tr></thead><tbody>{visible.map(file=><tr className={selected?.path===file.path?'selected':''} key={file.path}><td><ListItemAction label={file.name} icon={<FileIcon file={file}/>} ref={node=>{if(node)rowRefs.current.set(file.path,node);else rowRefs.current.delete(file.path)}} aria-current={selected?.path===file.path?'true':undefined} onClick={()=>open(file)}/></td><td className="file-type muted">{fileType(file)}</td></tr>)}</tbody></table></div>
    {loading&&<p className="empty-state" role="status">파일을 여는 중입니다.</p>}
    {error&&<div className="empty-state"><p className="su-error" role="alert">{error}</p><B onClick={()=>navigate('')}>작업공간으로</B></div>}
    {!loading&&!error&&!visible.length&&<p className="empty-state">{query?'검색 결과가 없습니다.':'폴더가 비어 있습니다.'}</p>}
   </section>
   {selected&&<section className="file-detail su-stack" aria-label="파일 미리보기"><SurfaceHeader className="file-detail-heading" title={<h2 ref={detailTitle} tabIndex={-1}>{selected.name}</h2>} actions={<B variant="ghost" size="sm" uniform aria-label="미리보기 닫기" onClick={close}><X size="1em"/></B>}/>
    <div className="file-detail-actions su-row">{currentItem&&<><B disabled={linked} onClick={()=>onConnect(currentItem)}><Link2 size="1em"/>{linked?'연결됨':'참고 자료로 연결'}</B>{<B disabled={collected} onClick={()=>onCollect({...selected,live:true})}><Archive size="1em"/>{collected?'라이브러리에 있음':'라이브러리에 추가'}</B>}</>}
     {currentItem&&<B variant="outline" onClick={()=>onFrom(currentItem)}><Plus size="1em" aria-hidden="true"/>이 파일로 새 작업</B>}
     {preview?.kind==='image'&&<B variant="outline" onClick={()=>setImageOpen(true)}>확대</B>}
     {preview&&preview.kind!=='error'&&<B variant="ghost" uniform aria-label="파일 다운로드" onClick={download}><Download size="1em"/></B>}</div>
    {!preview&&<p className="empty-state" role="status">미리보기를 여는 중입니다.</p>}
    {preview?.kind==='text'&&<TextContentView key={selected.path} body={preview.content} path={selected.path} title={selected.name} resolveImageSrc={src=>markdownImageHref(fileContentUrl(workspaceId,selected.path),src)}/>}
    {preview?.kind==='html'&&<HtmlContentView key={selected.path} body={preview.content} name={selected.name}/>}
    {preview?.kind==='image'&&<><div className="file-image-preview"><img src={preview.src} alt={selected.name}/></div><ImageDialog image={imageOpen?{src:preview.src,alt:selected.name,title:selected.name}:null} onClose={()=>setImageOpen(false)}/></>}
    {(preview?.kind==='video'||preview?.kind==='audio')&&<MediaPlayer kind={preview.kind} src={preview.src} label={selected.name} className="file-media-preview"/>}
    {preview?.kind==='pdf'&&<PdfPreview key={selected.path} href={preview.src} name={selected.name} className="file-pdf-preview"/>}
    {preview?.kind==='error'&&<p className="su-error" role="alert">{preview.message}</p>}
   </section>}
  </div>
 </div>;
}
