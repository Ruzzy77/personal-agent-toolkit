import React,{useState,useRef} from 'react';
import {ExternalLink,Check,Link2,Pencil,Plus} from 'lucide-react';
import {ButtonLink} from '@openai/apps-sdk-ui/components/Button';
import {Input} from '@openai/apps-sdk-ui/components/Input';
import {Textarea} from '@openai/apps-sdk-ui/components/Textarea';
import {Field} from '@personal-agent/ui-kit/react';
import {B} from './ui.jsx';
import {fileContentUrl} from './file-preview.js';
import {resourceLink} from './resource-link.js';
import {Disclosure,LibraryBrowser,ArtifactPreview,TextContentView,HtmlContentView,FilePreview,workspaceFilePresentation,contentRenderers} from './work-surface/index.js';

export function LibraryItemBody({item,workspaceId,toolkitUrl,onHeadingChange,actions}){
 const filePath=item.filePath||item.path;
 const original=item.reference&&resourceLink(item.reference,toolkitUrl,workspaceId);
 const external=item.reference&&(item.reference.kind!=='host-file'||item.reference.root!=='workspace');
 const actionRow=actions&&<div className="library-item-actions su-row">{actions}</div>;
 const display=content=>item.body||item.artifact?<>{content}{actionRow}</>:<>{actionRow}{content}</>;
 if(external)return <div className="su-stack">{item.body&&<div className="reading-text">{item.body}</div>}{!original&&<p className="muted">원본을 열려면 Toolkit 웹 연결이 필요합니다.</p>}{(original||actions)&&<div className="library-item-actions su-row">{original&&<ButtonLink href={original} target="_blank" rel="noopener noreferrer" color="primary" variant="outline" size="md" pill={false}><ExternalLink size="1em" aria-hidden="true"/>원본 열기</ButtonLink>}{actions}</div>}</div>;
 if(item.live&&filePath&&workspaceId){
  const preview=workspaceFilePresentation(filePath)?.viewer?<FilePreview href={fileContentUrl(workspaceId,filePath)} name={item.title} headingLevel={2}/>:<p className="muted">이 파일은 미리보기를 지원하지 않습니다.</p>;
  return display(item.body?<div className="su-stack"><div className="reading-text">{item.body}</div><Disclosure label="원본">{preview}</Disclosure></div>:preview);
 }
 if(item.artifact)return display(<ArtifactPreview onHeadingChange={onHeadingChange} artifact={item.artifact} renderers={contentRenderers} headingLevel={2}/>);
 if(filePath)return display(/\.html?$/i.test(filePath)?<HtmlContentView body={item.body||''} name={item.title}/>:<TextContentView body={item.body||''} path={filePath} title={item.title} headingLevel={2}/>);
 return display(item.body?<div className="reading-text">{item.body}</div>:<p className="muted">본문이 없습니다.</p>);
}
export function LibraryItemActions({item,work,onConnect,onFrom,onOpenWork}){
 const linked=work.sourceIds.includes(item.id);
 return <>
  <B disabled={linked} onClick={()=>onConnect(item)}>{linked?<Check size="1em"/>:<Link2 size="1em"/>}{linked?'연결됨':'참고 자료로 연결'}</B>
  {item.workId&&<B variant="outline" onClick={()=>onOpenWork(item.workId,item.artifactId)}>원래 작업 열기</B>}
  <B variant="outline" onClick={()=>onFrom(item)}><Plus size="1em" aria-hidden="true"/>{item.artifact?'복사해서 새 작업':'이 자료로 새 작업'}</B>
 </>;
}
function EntryContent({item,onUpdate,renderBody,actions}){
 const [editing,setEditing]=useState(false),[title,setTitle]=useState(item.title),[body,setBody]=useState(item.body||''),[error,setError]=useState('');
 const baseline=useRef(item);
 function save(){try{onUpdate(baseline.current,{title:title.trim(),body});setEditing(false);setError('')}catch(e){setError(e.message)}}
 if(editing)return <form className="su-stack" onSubmit={e=>{e.preventDefault();save()}}>
  <Field label="이름"><Input value={title} onChange={e=>setTitle(e.target.value)} maxLength={160}/></Field>
  <Field label="내용"><Textarea value={body} onChange={e=>setBody(e.target.value)} rows={12}/></Field>
  {error&&<p role="alert">{error}</p>}
  <div className="su-row"><B type="submit" variant="solid" disabled={!title.trim()||(!body.trim()&&!item.reference)}>저장</B><B variant="outline" onClick={()=>setEditing(false)}>취소</B></div>
 </form>;
 return renderBody(<>{onUpdate&&item.scope&&<B size="md" variant="outline" onClick={()=>{baseline.current=item;setTitle(item.title);setBody(item.body||'');setEditing(true)}}><Pencil size="1em" aria-hidden="true"/>자료 수정</B>}{actions}</>);
}
export function LibraryView({items,work,workspaceId,toolkitUrl,onConnect,onFrom,onOpenWork,onUpdateEntry}){
 return <LibraryBrowser items={items} renderItem={(item,{onHeadingChange})=><EntryContent key={item.id} item={item} onUpdate={onUpdateEntry}
  actions={<LibraryItemActions item={item} work={work} onConnect={onConnect} onFrom={onFrom} onOpenWork={onOpenWork}/>}
  renderBody={actions=><LibraryItemBody item={item} workspaceId={workspaceId} toolkitUrl={toolkitUrl} onHeadingChange={onHeadingChange} actions={actions}/>}/>}/>;
}
