import React from 'react';
import './work-surface.css';

export function WorkSurface({composition,renderers,context={},label='작업물',className='',id}) {
 const byId=new Map(composition.blocks.map(block=>[block.id,block]));
 return <div id={id} className={'ws-surface '+className} role="region" aria-label={label}>
  {composition.rows.map(row=><div className="ws-row" key={row.id} data-row={row.id}>
   {row.columns.map((column,index)=><div className="ws-column" key={row.id+'-'+index} style={{'--ws-span':column.span}}>
    {column.ids.map(id=>{
     const block=byId.get(id),Renderer=renderers[block.kind];
     return <div className="ws-block" data-kind={block.kind} key={id}>
      {Renderer?<Renderer block={block} context={block.id===context.primaryHeadingId?{...context,headingLevel:1}:context}/>:<section className="ws-unsupported" role="status">이 콘텐츠는 현재 화면에서 열 수 없습니다.</section>}
     </div>;
    })}
   </div>)}
  </div>)}
 </div>;
}
