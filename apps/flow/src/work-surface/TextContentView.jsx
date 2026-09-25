import React,{useMemo,useState} from 'react';
import {SegmentedControl} from '@openai/apps-sdk-ui/components/SegmentedControl';
import {MarkdownContent} from './MarkdownContent.jsx';
import {DelimitedTable} from './DelimitedTable.jsx';
import {parseDelimitedText,textContentFormat} from './delimited.js';
import './text-content-view.css';

export function TextContentView({body='',path='',title='',headingLevel=3,resolveImageSrc,className=''}){
 const [view,setView]=useState('structured');
 const format=textContentFormat(path);
 const data=useMemo(()=>format==='csv'||format==='tsv'?parseDelimitedText(body,format):null,[body,format]);
 const structured=format==='markdown'||Boolean(data?.rows.length);
 return <div className={'ws-text-content'+(className?' '+className:'')}>
  {body&&structured&&<div className="ws-text-content-actions">
   <SegmentedControl value={view} onChange={setView} aria-label="파일 표시 방식" size="sm" pill={false}>
    <SegmentedControl.Option value="structured">{format==='markdown'?'문서':'표'}</SegmentedControl.Option>
    <SegmentedControl.Option value="source">원문</SegmentedControl.Option>
   </SegmentedControl>
  </div>}
  {!body?<p>파일이 비어 있습니다.</p>
   :view==='structured'&&format==='markdown'?<MarkdownContent body={body} title={title} headingLevel={headingLevel} resolveImageSrc={resolveImageSrc}/>
   :view==='structured'&&data?.rows.length?<DelimitedTable data={data}/>
   :<pre className="su-code">{body}</pre>}
 </div>;
}
