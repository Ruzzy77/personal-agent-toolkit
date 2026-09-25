"use client";

import { Button } from "@openai/apps-sdk-ui/components/Button";
import { DiagramFields, canConvertArtifactToContent, contentFromArtifact } from "@personal-agent/flow-surface";
import type { FlowArtifact } from "../../lib/flow-content";

export function DiagramEditor({draft,onChange,onSave,onCancel,busy,dirty,submitLabel="저장"}:{
  draft:FlowArtifact;
  onChange:(draft:FlowArtifact)=>void;
  onSave:()=>void;
  onCancel:()=>void;
  busy:boolean;
  dirty:boolean;
  submitLabel?:string;
}) {
  const nodes=draft.nodes??[];
  const edges=draft.edges??[];
  const nodeIds=new Set(nodes.map(node=>node.id));
  const valid=Boolean(draft.title.trim()&&nodes.length&&nodes.every(node=>node.label.trim())&&edges.every(edge=>nodeIds.has(edge.from)&&nodeIds.has(edge.to)&&edge.from!==edge.to));
  return <form className="flow-diagram-editor" onSubmit={event=>{event.preventDefault();if(valid&&dirty)onSave()}}>
    <DiagramFields content={{heading:draft.title,nodes,edges}} headingLabel="작업물 제목" headingMaxLength={160} busy={busy} onChange={content=>onChange({...draft,title:content.heading??"",nodes:content.nodes,edges:content.edges})}/>
    <div className="flow-edit-actions">{canConvertArtifactToContent(draft)&&<Button type="button" color="primary" variant="ghost" pill={false} size="sm" disabled={busy} onClick={()=>onChange(contentFromArtifact(draft))}>작업 화면으로 바꾸기</Button>}<Button type="submit" color="primary" variant="solid" pill={false} size="sm" disabled={busy||!dirty||!valid}>{busy?"저장 중":submitLabel}</Button><Button type="button" color="primary" variant="ghost" pill={false} size="sm" disabled={busy} onClick={onCancel}>취소</Button></div>
  </form>;
}
