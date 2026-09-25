import React,{useEffect,useState} from 'react';
import {Camera,FileText,TriangleAlert,Box} from 'lucide-react';
import {AppHeader,B,Overlay} from './ui.jsx';
import {FieldSelect} from '@personal-agent/ui-kit/react';
import {WorkSurface,contentRenderers} from './work-surface/index.js';
import {inspectionLayouts} from './examples/inspection.js';
import './inspection-example.css';

function FactorsBlock({block,context}){
 const {heading,description,question,items}=block.content;
 const selected=context.factorSelection;
 const icons={product:Box,capture:Camera,error:TriangleAlert};
 return <section className="ws-factors">
  <h2>{heading}</h2>{description&&<p className="ws-factor-description">{description}</p>}
  <div className="ws-factor-map">
   {items.map(item=>{const Icon=icons[item.id]||Box;return <button key={item.id} type="button" className="ws-factor" aria-pressed={selected===item.id} onClick={()=>context.onFactorSelect?.(item.id)}>
    <Icon size="1.5em" aria-hidden="true"/><span><strong>{item.title}</strong><small>{item.detail}</small></span>
   </button>})}
   <div className="ws-factor-question" aria-live="polite">{items.find(item=>item.id===selected)?.note||question}</div>
  </div>
 </section>;
}

const renderers={...contentRenderers,factors:FactorsBlock};

export function InspectionExample(){
 useEffect(()=>{document.title='검사 데이터 검토 · Toolkit'},[]);
 const [mode,setMode]=useState('side'),[showSelection,setShowSelection]=useState(true),[selectedFactor,setSelectedFactor]=useState(null),[sourcesOpen,setSourcesOpen]=useState(false);
 const context={
  selectionVisible:{'inspection-image':showSelection},
  factorSelection:selectedFactor,
  onFactorSelect:setSelectedFactor,
  onSelectionToggle:()=>setShowSelection(value=>!value)
 };
 return <div className="app-shell ws-example">
  <a className="su-skip" href="#example-content">작업물로</a>
  <AppHeader leading={<span className="ws-example-work-name">검사 데이터 검토</span>} actions={<>
   <FieldSelect aria-label="배치" size="md" value={mode} options={[{value:'side',label:'가로 배치'},{value:'sequence',label:'세로 배치'}]} onChange={option=>setMode(option.value)}/>
   <B variant="ghost" size="md" aria-label="자료" onClick={()=>setSourcesOpen(true)}><FileText size="1em"/><span className="header-action-label">자료</span></B>
  </>}/>
  <main id="example-content" className="ws-example-main">
   <WorkSurface composition={inspectionLayouts[mode]} renderers={renderers} context={context} label="검사 데이터 검토 작업물"/>
  </main>
  <Overlay open={sourcesOpen} onClose={()=>setSourcesOpen(false)} title="자료" placement="right">
   <div className="su-stack" data-gap="section">
    <div className="ws-source"><strong>금속 부품 촬영 이미지</strong><span>표면 이미지와 흠집 후보 영역</span></div>
    <div className="ws-source"><strong>광학 검사 장비 이미지</strong><span>촬영 환경 참고</span></div>
   </div>
  </Overlay>
 </div>;
}
