import React,{useState} from 'react';
import {B} from './ui.jsx';
import {ArtifactPreview,ReviewComparison,contentRenderers} from './work-surface/index.js';

async function action(change,kind,workspaceId){
 const response=await fetch('/api/flow/changes/'+encodeURIComponent(change.id)+'/'+kind,{method:'POST',headers:{'X-Toolkit-Flow':'1','Content-Type':'application/json'},body:JSON.stringify({workspaceId}),signal:AbortSignal.timeout(10000)});
 const data=await response.json();
 if(!response.ok)throw new Error(data.error||'변경을 처리하지 못했습니다.');
 return data.change;
}
function Artifact({artifact}){
 if(!artifact)return null;
 return <div className="comparison-document"><h3>{artifact.title}</h3><ArtifactPreview artifact={artifact} renderers={contentRenderers} compact/></div>;
}
export function ChangesPanel({work,artifact,changes,workspaceId,onApplied}){
 const [busy,setBusy]=useState(null),[error,setError]=useState('');
 async function run(change,kind){setBusy(change.id);setError('');try{await action(change,kind,workspaceId);await onApplied()}catch(e){setError(e.message)}finally{setBusy(null)}}
 const visible=changes.filter(c=>c.workId===work.id&&c.kind==='change'&&['review','conflict','completed','undone'].includes(c.status)).slice().reverse();
 return <div className="request-panel su-stack" data-gap="section">
  {error&&<p className="su-error" role="alert">{error}</p>}
  {!visible.length&&<p className="small muted">이 작업물에 확인할 변경이 없습니다.</p>}
  {visible.map(c=>{const current=work.artifacts.find(a=>a.id===c.artifactId)||c.before;return <section className="request-job" key={c.id}>
   <div className="request-job-heading"><span className="small muted">{({review:'수정안',conflict:'재확인이 필요한 수정안',completed:c.mode==='add'?'추가된 작업물':'반영된 변경',undone:'되돌린 변경'})[c.status]}</span>{current?.title&&<h3>{current.title}</h3>}</div>
   {['review','conflict'].includes(c.status)&&<ReviewComparison current={<Artifact artifact={current}/>} proposed={c.proposal?.artifact?<Artifact artifact={c.proposal.artifact}/>:c.proposal?.replacement!==undefined?<p>{c.proposal.replacement}</p>:<dl className="request-fields">{Object.entries(c.proposal?.changes||{}).map(([key,value])=><div key={key}><dt>{key}</dt><dd>{typeof value==='string'?value:JSON.stringify(value)}</dd></div>)}</dl>}/>}
   {c.status==='review'&&<div className="su-row"><B variant="solid" disabled={!!busy} onClick={()=>run(c,'apply')}>수정안 반영</B></div>}
   {c.status==='completed'&&(c.before||c.mode==='add')&&<div className="su-row"><B variant="ghost" disabled={!!busy} onClick={()=>run(c,'undo')}>변경 되돌리기</B></div>}
  </section>})}
 </div>;
}
