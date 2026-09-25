"use client";

import React from 'react';
import {Button} from '@openai/apps-sdk-ui/components/Button';
import {Menu} from '@openai/apps-sdk-ui/components/Menu';
import {Check,ChevronDown} from 'lucide-react';
import {layoutWidthPresets} from './artifact-layout.js';
import './surface-chrome.css';

export function SurfaceHeader({title,meta,actions,className=''}) {
 return <header className={'flow-surface-heading su-toolbar '+className}>
  <div className="flow-surface-heading-main">{meta&&<div className="flow-surface-meta">{meta}</div>}{title}</div>
  {actions&&<div className="flow-surface-heading-actions su-row">{actions}</div>}
 </header>;
}

export function EditorActions({busy=false,disabled=false,cancelDisabled=false,onCancel,saveLabel='저장',className=''}) {
 return <div className={'flow-editor-actions su-row '+className}>
  <Button type="submit" color="primary" variant="solid" pill={false} size="md" disabled={busy||disabled}>{busy?'저장 중':saveLabel}</Button>
  <Button type="button" color="primary" variant="ghost" pill={false} size="md" disabled={busy||cancelDisabled} onClick={onCancel}>취소</Button>
 </div>;
}

export function EditorLayout({children,preview,previewLabel='미리보기',headingLevel=3,className='',fieldsClassName='',previewClassName=''}) {
 const Heading='h'+headingLevel;
 return <div className={'flow-editor-layout '+className}>
  <div className={'flow-editor-fields su-stack '+fieldsClassName}>{children}</div>
  <section className={'flow-editor-preview '+previewClassName} aria-label={previewLabel}>
   <Heading>{previewLabel}</Heading>{preview}
  </section>
 </div>;
}

export function ArtifactLayoutMenu({title='작업물',span,index,count,disabled=false,onChange}) {
 return <Menu><Menu.Trigger><Button type="button" color="primary" variant="ghost" pill={false} size="sm" disabled={disabled} aria-label={title+' 배치'}>배치<ChevronDown size="1em" aria-hidden="true"/></Button></Menu.Trigger>
  <Menu.Content align="end" minWidth={180}>
   {layoutWidthPresets.map(width=><Menu.Item key={width.value} onSelect={()=>onChange('span',width.value)}>{width.label}{span===width.value&&<Check size="1em" aria-hidden="true"/>}</Menu.Item>)}
   <Menu.Item disabled={index===0} onSelect={()=>onChange('move',-1)}>순서 올리기</Menu.Item>
   <Menu.Item disabled={index===count-1} onSelect={()=>onChange('move',1)}>순서 내리기</Menu.Item>
  </Menu.Content>
 </Menu>;
}
