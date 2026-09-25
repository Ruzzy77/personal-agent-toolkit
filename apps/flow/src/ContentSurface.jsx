import React,{useEffect,useRef,useState} from 'react';
import {Folder,FileText} from 'lucide-react';
import {Input} from '@openai/apps-sdk-ui/components/Input';
import {B} from './ui.jsx';
import {Editable} from './Editable.jsx';
import {SurfaceHeader,splitContentHeading,ArtifactPreview,EditorActions,EditorLayout,CompositionEditor,TextBlock,WorkSurface,activeContentSources,appendContentBlock,workspaceFileBlock,workspaceFilePresentation,contentImageTargetSlot,contentRenderers,materializeContentImages,setContentSource,workspaceMediaType,previewableContentDraft,replaceContentImages,updateContentBlock,validContentDraft} from './work-surface/index.js';
import './content-surface.css';

const maxMedia=20*1024*1024;

function WorkspaceFilePicker({workspaceId,kind,onChoose,onClose}){
 const request=useRef(0);
 useEffect(()=>()=>{request.current++},[]);
 const [directory,setDirectory]=useState(''),[entries,setEntries]=useState([]),[query,setQuery]=useState(''),[loading,setLoading]=useState(false),[error,setError]=useState(''),[selecting,setSelecting]=useState('');
 useEffect(()=>{
  const controller=new AbortController();
  queueMicrotask(()=>{setLoading(true);setError('')});
  fetch('/api/flow/files?'+new URLSearchParams({workspaceId,path:directory}),{signal:controller.signal})
   .then(async response=>{if(!response.ok)throw new Error();return response.json()})
   .then(data=>{if(!controller.signal.aborted)setEntries(data.items||[])})
   .catch(()=>{if(!controller.signal.aborted)setError('파일 목록을 열지 못했습니다.')})
   .finally(()=>{if(!controller.signal.aborted)setLoading(false)});
  return ()=>controller.abort();
 },[workspaceId,directory]);
 async function choose(path){
  const number=++request.current;
  setSelecting(path);setError('');
  try{
   const source='/api/flow/files/content?'+new URLSearchParams({workspaceId,path});
   const response=await fetch(source,{method:'HEAD',headers:{'X-Toolkit-Flow':'1'}});
   const mime=response.headers.get('content-type')?.split(';',1)[0];
   if(!response.ok||(kind==='file'?mime!==workspaceFilePresentation(path)?.responseType:mime!==workspaceMediaType(path)?.mime))throw new Error();
   if(number===request.current)onChoose(source,path);
  }catch{if(number===request.current)setError('파일을 열지 못했습니다. 다른 파일을 선택해 주세요.')}
  finally{if(number===request.current)setSelecting('')}
 }
 function browse(path){request.current++;setSelecting('');setDirectory(path);setQuery('')}
 const parts=directory?directory.split('/'):[];
 const visible=entries.filter(item=>(item.type==='directory'||item.type==='file'&&(kind==='file'?!!workspaceFilePresentation(item.path):workspaceMediaType(item.path)?.kind===kind))&&item.name.toLocaleLowerCase('ko').includes(query.toLocaleLowerCase('ko')));
 return <section className="content-media-picker" aria-label={kind==='video'?'영상 선택':kind==='audio'?'오디오 선택':'파일 선택'}>
  <div className="content-media-picker-head"><h3>{kind==='video'?'영상 선택':kind==='audio'?'오디오 선택':'파일 선택'}</h3><B variant="ghost" size="sm" onClick={onClose}>닫기</B></div>
  <nav className="content-media-breadcrumbs" aria-label="폴더 경로"><B variant="ghost" size="sm" onClick={()=>browse('')}>작업공간</B>
   {parts.map((part,index)=><React.Fragment key={index}><span aria-hidden="true">/</span><B variant="ghost" size="sm" onClick={()=>browse(parts.slice(0,index+1).join('/'))}>{part}</B></React.Fragment>)}
  </nav>
  <Input aria-label="현재 폴더에서 찾기" type="search" value={query} onChange={event=>setQuery(event.target.value)} placeholder="현재 폴더에서 찾기"/>
  {error&&<p role="alert" className="su-error">{error}</p>}
  {loading?<p className="muted small">파일을 불러오는 중입니다.</p>:<ul>{visible.map(item=><li key={item.path}><B variant="ghost" size="md" block disabled={!!selecting} onClick={()=>item.type==='directory'?browse(item.path):choose(item.path)}><span className="flow-action-label su-row">{item.type==='directory'?<Folder className="flow-list-icon" aria-hidden="true"/>:<FileText className="flow-list-icon" aria-hidden="true"/>}<span className="truncate">{item.name}</span></span></B></li>)}</ul>}
  {!loading&&!visible.length&&!error&&<p className="muted small">{query?'찾은 파일이 없습니다.':'선택할 파일이 없습니다.'}</p>}
 </section>;
}

function InlineTextBlock({block,context}){
 const edit=context.onText;
 if(!edit)return <TextBlock block={block} context={context}/>;
 const {heading='',paragraphs=[]}=block.content;
 return <section className="ws-prose">
  {heading&&<h3 className="ws-block-title"><Editable as="span" value={heading} label="소제목" onChange={value=>edit(block.id,'heading',0,value)}/></h3>}
  {(paragraphs.length?paragraphs:['']).map((paragraph,index)=><p key={index}><Editable as="span" value={paragraph} label={heading?heading+' 본문 '+(index+1):'본문 '+(index+1)} placeholder="내용을 입력하세요." onChange={value=>edit(block.id,'paragraph',index,value)}/></p>)}
 </section>;
}
const inlineRenderers={...contentRenderers,text:InlineTextBlock};

export function ContentSurface({artifact,title,actions,onChange,onCommit,uploadImage,workspaceId,mediaAvailable,onToast,onEditState,editBlocked=false}){
 const [editing,setEditing]=useState(false),[draft,setDraft]=useState(()=>structuredClone(artifact.composition)),[pending,setPending]=useState({}),[picker,setPicker]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const fileInput=useRef(null),imageTarget=useRef(null),editTrigger=useRef(null),editorRef=useRef(null),pendingRef=useRef(pending),request=useRef(0),baseRevision=useRef(artifact.revision);
 useEffect(()=>{pendingRef.current=pending},[pending]);
 useEffect(()=>()=>{request.current++;for(const item of Object.values(pendingRef.current))URL.revokeObjectURL(item.url)},[]);
 const {heading,body}=splitContentHeading(artifact.composition);
 const active=activeContentSources(draft,pending);
 const dirty=JSON.stringify(draft)!==JSON.stringify(artifact.composition)||Object.keys(active).length>0;
 const valid=validContentDraft(draft,pending);
 const previewComposition=previewableContentDraft(draft,pending);
 const preview=previewComposition&&replaceContentImages(previewComposition,active,item=>item.url);
 useEffect(()=>{onEditState?.(artifact.id,editing,editing&&dirty)},[artifact.id,editing,dirty,onEditState]);
 useEffect(()=>()=>onEditState?.(artifact.id,false,false),[artifact.id,onEditState]);
 useEffect(()=>{if(!editing||!dirty)return;const leave=event=>event.preventDefault();window.addEventListener('beforeunload',leave);return()=>window.removeEventListener('beforeunload',leave)},[editing,dirty]);
 function changeText(blockId,field,index,value){
  if(!onChange)return;
  if(value.length>(field==='heading'?500:20000)){onToast?.('입력한 내용이 너무 깁니다.');return}
  onChange(current=>{
   if(current.kind!=='content'||!current.composition)throw new Error('작업 화면을 확인해 주세요.');
   const block=current.composition.blocks.find(item=>item.id===blockId);
   if(block?.kind!=='text')throw new Error('본문을 찾지 못했습니다.');
   const paragraphs=[...(block.content.paragraphs||[])];
   if(field==='paragraph')paragraphs[index]=value;
   const content=field==='heading'?{...block.content,heading:value}:{...block.content,paragraphs};
   return {composition:updateContentBlock(current.composition,blockId,content)};
  });
 }
 function clearPending(){for(const item of Object.values(pendingRef.current))URL.revokeObjectURL(item.url);pendingRef.current={};setPending({})}
 function start(){if(editBlocked)return;baseRevision.current=artifact.revision;setDraft(structuredClone(artifact.composition));clearPending();setError('');setPicker(null);setEditing(true);requestAnimationFrame(()=>{document.getElementById('artifact-'+artifact.id)?.scrollIntoView({block:'start'});const target=[...(editorRef.current?.querySelectorAll('.content-editor-fields input:not([type=hidden]),.content-editor-fields textarea')||[])].find(element=>element.getClientRects().length)||editorRef.current?.querySelector('.flow-content-block-trigger');(target||editorRef.current)?.focus({preventScroll:true})})}
 function close(force=false){
  if(busy&&!force)return;
  if(!force&&dirty&&!confirm('저장하지 않은 내용이 사라집니다. 닫을까요?'))return;
  request.current++;clearPending();setPicker(null);setEditing(false);setBusy(false);setError('');requestAnimationFrame(()=>editTrigger.current?.focus({preventScroll:true}));
 }
 function pick(kind,blockId,field,itemId){
  if(!mediaAvailable){setError('파일은 작업공간에 연결된 후 선택할 수 있습니다.');return}
  const target={kind,blockId,field,itemId};
  if(kind==='image'){imageTarget.current=target;fileInput.current?.click()}
  else setPicker(target);
 }
 async function chooseImage(file){
  const target=imageTarget.current;
  if(!target||!file)return;
  const number=++request.current;
  setError('');
  let url='';
  try{
   if(file.size>maxMedia||!['image/png','image/jpeg','image/webp','image/gif'].includes(file.type))throw new Error('format');
   url=URL.createObjectURL(file);
   const size=await new Promise((resolve,reject)=>{const image=new Image();image.onload=()=>image.naturalWidth>0&&image.naturalHeight>0?resolve({width:image.naturalWidth,height:image.naturalHeight}):reject(new Error('size'));image.onerror=reject;image.src=url});
   if(number!==request.current){URL.revokeObjectURL(url);return}
   const slot=contentImageTargetSlot(target),old=pendingRef.current[slot];
   if(old)URL.revokeObjectURL(old.url);
   const next={...pendingRef.current,[slot]:{file,url}};pendingRef.current=next;setPending(next);
   setDraft(current=>setContentSource(current,target,url,size));
  }catch{if(url)URL.revokeObjectURL(url);if(number===request.current)setError('이미지를 열지 못했습니다. 파일을 확인해 주세요.')}
  finally{if(fileInput.current)fileInput.current.value=''}
 }
 async function save(){
  if(busy||!dirty||!valid)return;
  if(artifact.revision!==baseRevision.current){setError('작업물이 바뀌었습니다. 현재 내용을 확인한 뒤 다시 수정해 주세요.');return}
  const number=++request.current,base=baseRevision.current;
  setBusy(true);setError('');
  try{
   const result=await materializeContentImages(draft,pending,async item=>(await uploadImage(item.file)).src,item=>item.file);
   if(number!==request.current)return;
   if(!onCommit({composition:result},base))throw new Error('conflict');
   close(true);
   onToast?.('작업 화면을 저장했습니다');
  }catch(error){
   if(number===request.current)setError(error?.message==='conflict'?'작업물이 바뀌었습니다. 현재 내용을 확인한 뒤 다시 수정해 주세요.':'작업 화면을 저장하지 못했습니다. 파일과 내용을 확인해 주세요.');
  }finally{if(number===request.current)setBusy(false)}
 }
 return <>
  <input ref={fileInput} type="file" accept="image/png,image/jpeg,image/webp,image/gif" hidden onChange={event=>void chooseImage(event.target.files?.[0])}/>
  <SurfaceHeader className="work-piece-heading content-heading" title={heading?<div className="ws-heading"><h2 className="ws-heading-title">{heading.content.title}</h2>{heading.content.description&&<p>{heading.content.description}</p>}</div>:editing?<h2 className="artifact-title">{artifact.title||'제목 없음'}</h2>:title}
   actions={!editing&&<>{actions}<B ref={editTrigger} variant="ghost" size="sm" disabled={editBlocked} onClick={start}>내용 수정</B></>}/>
  {editing?<form ref={editorRef} tabIndex={-1} className="content-editor-form" aria-label="작업 화면 수정" onSubmit={event=>{event.preventDefault();void save()}}>
    <EditorActions busy={busy} disabled={!valid||!dirty} onCancel={()=>close()}/>
    <EditorLayout fieldsClassName="content-editor-fields" preview={preview?<ArtifactPreview artifact={{...artifact,composition:preview}} renderers={contentRenderers} compact/>:<p className="muted small">내용을 입력하면 여기에 표시됩니다.</p>}>
     <CompositionEditor composition={draft} onChange={value=>{setDraft(value);setError('')}} onPick={pick} onAddFile={()=>{if(!mediaAvailable){setError('파일은 작업공간에 연결된 후 선택할 수 있습니다.');return}setPicker({kind:'file',mode:'add'});setError('')}} busy={busy}/>
     {picker&&<WorkspaceFilePicker key={JSON.stringify(picker)} workspaceId={workspaceId} kind={picker.kind} onClose={()=>setPicker(null)} onChoose={(src,path)=>{setDraft(current=>picker.mode==='add'?appendContentBlock(current,workspaceFileBlock(path,src)):setContentSource(current,picker,src,undefined,path));setPicker(null)}}/>}
     {error&&<p className="su-error" role="alert">{error}</p>}
     {!valid&&dirty&&<p className="muted small" role="status">내용이나 연결할 파일을 확인해 주세요.</p>}
    </EditorLayout>
   </form>:body.blocks.length>0&&<WorkSurface className="content-artifact-surface" composition={body} renderers={inlineRenderers} context={{headingLevel:3,onText:onChange?changeText:null}} label={(heading?.content.title||artifact.title||'작업물')+' 내용'}/>}
 </>;
}
