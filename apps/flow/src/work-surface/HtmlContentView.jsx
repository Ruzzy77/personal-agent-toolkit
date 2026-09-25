import React,{useMemo,useState} from 'react';
import {SegmentedControl} from '@openai/apps-sdk-ui/components/SegmentedControl';
import {htmlPreviewDocument} from './html-preview.js';
import './html-content-view.css';

export function HtmlContentView({body,src,name='',className='',showSourceToggle=true}) {
 const [view,setView]=useState('preview');
 const hasBody=typeof body==='string';
 const safeSrc=typeof src==='string'&&src.startsWith('/')&&!src.startsWith('//')?src:undefined;
 const srcDoc=useMemo(()=>hasBody?htmlPreviewDocument(body):undefined,[body,hasBody]);
 return <div className={'ws-html-content'+(className?' '+className:'')}>
  {showSourceToggle&&hasBody&&body&&<div className="ws-html-content-actions">
   <SegmentedControl value={view} onChange={setView} aria-label="HTML 표시 방식" size="sm" pill={false}>
    <SegmentedControl.Option value="preview">화면</SegmentedControl.Option>
    <SegmentedControl.Option value="source">원문</SegmentedControl.Option>
   </SegmentedControl>
  </div>}
  {hasBody&&!body?<p>파일이 비어 있습니다.</p>
   :showSourceToggle&&view==='source'&&hasBody?<pre className="su-code">{body}</pre>
   :srcDoc||safeSrc?<iframe title={(name||'HTML')+' 미리보기'} srcDoc={srcDoc} src={hasBody?undefined:safeSrc} sandbox="" referrerPolicy="no-referrer" className="ws-html-content-frame" loading="lazy"/>
   :<p role="alert">HTML을 표시하지 못했습니다.</p>}
 </div>;
}
