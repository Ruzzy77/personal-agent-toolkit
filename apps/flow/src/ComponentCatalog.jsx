import React,{useEffect,useState} from 'react';
import {AppHeader,B} from './ui.jsx';
import {ArtifactPreview,EditorLayout,SurfaceHeader,CompositionEditor,DiagramCanvas,ImageRegionEditor,ReviewComparison,WorkSurface,appendContentBlock,workspaceFileBlock,contentRenderers,layoutDiagramNodes,validContentDraft,validImageRegion} from './work-surface/index.js';
import {catalogArtifacts,catalogReview,catalogSections} from './examples/catalog.js';
import './component-catalog.css';

export function ComponentCatalog(){
 useEffect(()=>{
  document.title='작업 화면 구성 요소 · Toolkit';
  const id=location.hash.slice(1),target=document.getElementById(id);
  if(!id.startsWith('catalog-')||!target?.classList.contains('catalog-section'))return;
  const frame=requestAnimationFrame(()=>target.scrollIntoView({block:'start'}));
  return()=>cancelAnimationFrame(frame);
 },[]);
 const [demo,setDemo]=useState(()=>structuredClone(catalogSections[1].composition));
 const [demoMessage,setDemoMessage]=useState('');
 const [demoPicker,setDemoPicker]=useState(false);
 const [region,setRegion]=useState({x:.12,y:.16,width:.62,height:.67});
 const [diagram,setDiagram]=useState(()=>structuredClone(catalogArtifacts.find(item=>item.kind==='diagram')));
 const [diagramSelection,setDiagramSelection]=useState(null);
 const demoValid=validContentDraft(demo,{});
 function pickDemo(kind,blockId,field,itemId){
  if(kind==='video'){setDemoMessage('영상은 작업 화면에서 파일을 선택해 주세요.');return}
  if(kind==='file'){setDemoMessage('작업공간 파일은 작업 화면에서 선택해 주세요.');return}
  const src=kind==='audio'?'/examples/sample-tone.wav':'/examples/metal.png';
  setDemo(current=>({...current,blocks:current.blocks.map(block=>{
   if(block.id!==blockId)return block;
   if(itemId){const key=block.kind==='gallery'?'images':'items';return {...block,content:{...block.content,[key]:(block.content[key]||[]).map(item=>item.id===itemId?{...item,src}:item)}}}
   return {...block,content:{...block.content,[field]:src}};
  })}));
  setDemoMessage('');
 }
 return <div className="app-shell ws-catalog">
  <a className="su-skip" href="#catalog-content">본문으로</a>
  <AppHeader/>
  <main id="catalog-content" className="catalog-main">
   <SurfaceHeader className="catalog-intro" title={<><h1>작업 화면 구성 요소</h1><p>글과 이미지, 수치, 파일을 작업에 맞게 조합합니다.</p></>} actions={<B variant="ghost" size="md" onClick={()=>location.assign("/examples/inspection.html")}>검사 예시</B>}/>
   <nav className="catalog-index" aria-label="구성 요소 종류"><div><span>콘텐츠</span><div>{catalogSections.map(section=><a href={'#catalog-'+section.id} key={section.id}>{section.title}</a>)}</div></div><div><span>편집</span><div><a href="#catalog-editor">내용과 배치</a><a href="#catalog-image-region">이미지 영역</a><a href="#catalog-diagram">도식 배치</a></div></div><div><span>검토</span><div><a href="#catalog-artifacts">작업물 보기</a><a href="#catalog-review">수정안 비교</a></div></div></nav>
   {catalogSections.map(section=><section className="catalog-section" id={'catalog-'+section.id} key={section.id}>
    <h2>{section.title}</h2>
    <WorkSurface composition={section.composition} renderers={contentRenderers} context={{headingLevel:3}} label={section.title+' 예시'}/>
   </section>)}
   <section className="catalog-section" id="catalog-editor">
    <div className="catalog-section-head"><h2>직접 배치하기</h2><B variant="ghost" size="sm" onClick={()=>{setDemo(structuredClone(catalogSections[1].composition));setDemoMessage('');setDemoPicker(false)}}>처음 상태로</B></div>
    <EditorLayout className="catalog-editor-grid" preview={demoValid?<WorkSurface composition={demo} renderers={contentRenderers} context={{headingLevel:4}} label="배치 미리보기"/>:<p className="small muted" role="status">내용을 입력하면 여기에 표시됩니다.</p>}>
     <div className="catalog-editor-controls"><CompositionEditor composition={demo} onChange={value=>{setDemo(value);setDemoMessage('')}} onPick={pickDemo} onAddFile={()=>setDemoPicker(true)}/>{demoPicker&&<section className="su-stack" aria-label="예시 파일"><div className="su-toolbar"><h3>예시 파일</h3><B variant="ghost" size="sm" onClick={()=>setDemoPicker(false)}>닫기</B></div>{[["금속 부품.png","metal.png"],["촬영 장비.png","camera.png"],["신호음.wav","sample-tone.wav"]].map(([name,file])=><B key={file} variant="ghost" size="sm" onClick={()=>{setDemo(current=>appendContentBlock(current,workspaceFileBlock(name,"/examples/"+file)));setDemoPicker(false)}}>{name}</B>)}</section>}{demoMessage&&<p className="small muted" role="status">{demoMessage}</p>}</div>
    </EditorLayout>
   </section>
   <section className="catalog-section" id="catalog-image-region">
    <div className="catalog-section-head"><h2>이미지 영역</h2><B variant="ghost" size="sm" onClick={()=>setRegion({x:0,y:0,width:1,height:1})}>전체 영역 선택</B></div>
    <div className="catalog-region-grid"><ImageRegionEditor src="/examples/metal.png" alt="금속 부품 상단" width={1672} height={941} value={region} onChange={setRegion}/>
     <div className="catalog-region-preview"><h3>선택한 영역</h3>{validImageRegion(region)?<ArtifactPreview artifact={{kind:'image',title:'선택한 영역',src:'/examples/metal.png',alt:'금속 부품 상단의 선택한 영역',width:1672,height:941,crop:region}} compact/>:<p className="small muted">영역의 크기와 위치를 확인해 주세요.</p>}</div>
    </div>
   </section>
   <section className="catalog-section" id="catalog-diagram">
    <div className="catalog-section-head"><h2>도식 배치</h2><div className="su-row"><B variant="ghost" size="sm" onClick={()=>setDiagram(current=>({...current,nodes:layoutDiagramNodes(current.nodes,'vertical')}))}>세로 배치</B><B variant="ghost" size="sm" onClick={()=>setDiagram(current=>({...current,nodes:layoutDiagramNodes(current.nodes,'horizontal')}))}>가로 배치</B></div></div>
    <div className="catalog-diagram-grid">
     <div className="catalog-diagram-frame"><DiagramCanvas title={diagram.title} nodes={diagram.nodes} edges={diagram.edges} selected={diagramSelection} onSelect={setDiagramSelection} onMoveNode={({id,x,y})=>setDiagram(current=>({...current,nodes:current.nodes.map(node=>node.id===id?{...node,x,y}:node)}))}/></div>
     <div className="catalog-diagram-preview"><h3>미리보기</h3><ArtifactPreview artifact={diagram} compact/><p className="small muted">항목을 끌거나 방향키로 위치를 옮길 수 있습니다.</p></div>
    </div>
   </section>
   <section className="catalog-section" id="catalog-artifacts">
    <h2>작업물 보기</h2>
    <div className="catalog-artifact-grid">{catalogArtifacts.map(artifact=><article key={artifact.id} className="catalog-artifact">
     <h3>{artifact.title}</h3><ArtifactPreview artifact={artifact} renderers={contentRenderers} headingLevel={4}/>
    </article>)}</div>
   </section>
   <section className="catalog-section" id="catalog-review">
    <h2>수정안 비교</h2>
    <ReviewComparison current={<ArtifactPreview artifact={catalogReview.current} compact/>} proposed={<ArtifactPreview artifact={catalogReview.proposed} compact/>}/>
   </section>
  </main>
 </div>;
}
