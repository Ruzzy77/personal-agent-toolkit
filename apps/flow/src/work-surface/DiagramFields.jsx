"use client";
import { useState } from "react";
import { Button } from "@openai/apps-sdk-ui/components/Button";
import { Input } from "@openai/apps-sdk-ui/components/Input";
import { Textarea } from "@openai/apps-sdk-ui/components/Textarea";
import { FieldSelect } from "@personal-agent/ui-kit/react";
import { DiagramCanvas } from "./DiagramCanvas.jsx";
import { layoutDiagramNodes } from "./diagram-layout.js";
import "./composition-editor.css";

const str=value=>value===undefined||value===null?"":String(value);
const records=value=>Array.isArray(value)?value:[];
function TextField({label,value,onChange,rows,maxLength=3000,disabled=false}) {
 return <label className="flow-content-field">{label}{rows
  ?<Textarea value={value} onChange={event=>onChange(event.target.value)} rows={rows} maxLength={maxLength} disabled={disabled}/>
  :<Input value={value} onChange={event=>onChange(event.target.value)} maxLength={maxLength} disabled={disabled}/>}
 </label>;
}
function firstConnection(nodes,edges) {
    const used=new Set(edges.map(edge=>JSON.stringify([edge.from,edge.to])));
    for(const from of nodes)for(const to of nodes) {
        if(from.id!==to.id&&!used.has(JSON.stringify([from.id,to.id])))return {from:from.id,to:to.id};
    }
    return null;
}
export function DiagramFields({content,onChange,busy=false,headingLabel="소제목",headingMaxLength=500}) {
    const [selected,setSelected]=useState(null);
    const nodes=records(content.nodes),edges=records(content.edges);
    const next=firstConnection(nodes,edges);
    const changeNode=(id,patch)=>onChange({...content,nodes:nodes.map(node=>node.id===id?{...node,...patch}:node)});
    const changeEdge=(id,patch)=>onChange({...content,edges:edges.map(edge=>edge.id===id?{...edge,...patch}:edge)});
    const addNode=()=>onChange({...content,nodes:layoutDiagramNodes([...nodes,{id:crypto.randomUUID(),label:"",detail:"",x:500,y:300}],"vertical")});
    const removeNode=id=>{
        onChange({...content,nodes:nodes.filter(node=>node.id!==id),edges:edges.filter(edge=>edge.from!==id&&edge.to!==id)});
        if(selected?.kind==="node"&&selected.id===id)setSelected(null);
    };
    return <div className="flow-content-fields">
      <TextField label={headingLabel} value={str(content.heading)} onChange={heading=>onChange({...content,heading})} maxLength={headingMaxLength} disabled={busy}/>
      <div className="flow-content-diagram-preview"><DiagramCanvas title={content.heading||""} nodes={nodes} edges={edges} selected={selected} onSelect={setSelected} onMoveNode={({id,x,y})=>changeNode(id,{x,y})} disabled={busy}/></div>
      <p className="flow-content-diagram-hint">항목을 끌거나 방향키로 위치를 옮길 수 있습니다.</p>
      <div className="flow-content-list-actions">
        <Button type="button" color="primary" variant="outline" pill={false} size="sm" disabled={busy||nodes.length>=100} onClick={addNode}>항목 추가</Button>
        <Button type="button" color="primary" variant="outline" pill={false} size="sm" disabled={busy||nodes.length<2} onClick={()=>onChange({...content,nodes:layoutDiagramNodes(nodes,"vertical")})}>세로 배치</Button>
        <Button type="button" color="primary" variant="outline" pill={false} size="sm" disabled={busy||nodes.length<2} onClick={()=>onChange({...content,nodes:layoutDiagramNodes(nodes,"horizontal")})}>가로 배치</Button>
      </div>
      {nodes.map((node,index)=><div className="flow-content-item" key={node.id}>
        <span className="flow-content-item-heading">항목 {index+1}</span>
        <TextField label="이름" value={str(node.label)} onChange={label=>changeNode(node.id,{label})} maxLength={120} disabled={busy}/>
        <TextField label="내용" value={str(node.detail)} onChange={detail=>changeNode(node.id,{detail})} rows={2} maxLength={2000} disabled={busy}/>
        <Button type="button" color="primary" variant="outline" pill={false} size="sm" disabled={busy||nodes.length<=1} onClick={()=>removeNode(node.id)}>항목 삭제</Button>
      </div>)}
      <div className="flow-content-list-actions">
        <span className="flow-content-item-heading">연결</span>
        <Button type="button" color="primary" variant="outline" pill={false} size="sm" disabled={busy||!next||edges.length>=200} onClick={()=>onChange({...content,edges:[...edges,{id:crypto.randomUUID(),...next,label:""}]})}>연결 추가</Button>
      </div>
      {edges.map((edge,index)=><div className="flow-content-item" key={edge.id}>
        <span className="flow-content-item-heading">연결 {index+1}</span>
        <div className="flow-content-field"><span>시작</span><FieldSelect aria-label={"연결 "+(index+1)+" 시작"} value={edge.from} size="md" pill={false} restoreFocus disabled={busy} options={nodes.filter(node=>node.id!==edge.to).map(node=>({value:node.id,label:node.label||"이름 없는 항목"}))} onChange={option=>changeEdge(edge.id,{from:option.value})}/></div>
        <div className="flow-content-field"><span>끝</span><FieldSelect aria-label={"연결 "+(index+1)+" 끝"} value={edge.to} size="md" pill={false} restoreFocus disabled={busy} options={nodes.filter(node=>node.id!==edge.from).map(node=>({value:node.id,label:node.label||"이름 없는 항목"}))} onChange={option=>changeEdge(edge.id,{to:option.value})}/></div>
        <TextField label="연결 이름" value={str(edge.label)} onChange={label=>changeEdge(edge.id,{label})} maxLength={120} disabled={busy}/>
        <Button type="button" color="primary" variant="outline" pill={false} size="sm" disabled={busy} onClick={()=>{onChange({...content,edges:edges.filter(item=>item.id!==edge.id)});if(selected?.kind==="edge"&&selected.id===edge.id)setSelected(null)}}>연결 삭제</Button>
      </div>)}
    </div>;
}
