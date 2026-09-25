import React,{useEffect,useState} from 'react';
import {ChevronLeft,ChevronRight} from 'lucide-react';
import {Button} from '@openai/apps-sdk-ui/components/Button';
import {pdfPreviewHref} from './content-links.js';
import './pdf-preview.css';

export function PdfPreview({href,name='',className=''}) {
 const [page,setPage]=useState(1);
 const [metadata,setMetadata]=useState({href:null,pages:0,error:''});
 const [failedSrc,setFailedSrc]=useState('');
 useEffect(()=>{
  const first=pdfPreviewHref(href);
  const controller=new AbortController();
  setPage(1);setFailedSrc('');
  if(!first){setMetadata({href,pages:0,error:'PDF 미리보기를 사용할 수 없습니다.'});return()=>controller.abort()}
  setMetadata({href,pages:0,error:''});
  fetch(first,{method:'HEAD',signal:controller.signal}).then(response=>{
   const pages=Number(response.headers.get('x-flow-pdf-pages'));
   if(!response.ok||!Number.isInteger(pages)||pages<1||pages>10000)throw new Error();
   return pages;
  }).then(pages=>{if(!controller.signal.aborted)setMetadata({href,pages,error:''})})
   .catch(()=>{if(!controller.signal.aborted)setMetadata({href,pages:0,error:'PDF 미리보기를 사용할 수 없습니다.'})});
  return()=>controller.abort();
 },[href]);
 const ready=metadata.href===href&&metadata.pages>0;
 const src=ready?pdfPreviewHref(href,page):null;
 return <div className={'ws-pdf-preview '+className}>
  {metadata.href!==href||!metadata.pages&&!metadata.error?<p role="status">PDF를 여는 중입니다.</p>:null}
  {metadata.href===href&&metadata.error?<p role="alert">{metadata.error} 원본을 내려받아 확인해 주세요.</p>:null}
  {ready&&<>
   <div className="ws-pdf-preview-toolbar" aria-label="PDF 쪽 이동">
    {metadata.pages>1&&<Button type="button" color="primary" variant="ghost" pill={false} size="sm" disabled={page===1} onClick={()=>setPage(value=>value-1)}><ChevronLeft size="1em"/>이전</Button>}
    <span>{page}쪽 / {metadata.pages}쪽</span>
    {metadata.pages>1&&<Button type="button" color="primary" variant="ghost" pill={false} size="sm" disabled={page===metadata.pages} onClick={()=>setPage(value=>value+1)}>다음<ChevronRight size="1em"/></Button>}
   </div>
   {failedSrc===src?<p role="alert">{page}쪽을 표시하지 못했습니다.</p>:<img key={src} src={src||''} alt={name+' '+page+'쪽'} loading="lazy" onError={()=>setFailedSrc(src||'')}/>}
  </>}
 </div>;
}
