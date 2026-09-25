"use client";

import { Button } from "@openai/apps-sdk-ui/components/Button";
import { Input } from "@openai/apps-sdk-ui/components/Input";
import { Textarea } from "@openai/apps-sdk-ui/components/Textarea";
import type { FlowArtifact } from "../../lib/flow-content";
import { canConvertArtifactToContent, contentFromArtifact } from "@personal-agent/flow-surface";

export function DocumentEditor({ draft, onChange, onSave, onCancel, busy, dirty, submitLabel="저장" }: {
  draft: FlowArtifact;
  onChange: (draft: FlowArtifact) => void;
  onSave: () => void;
  onCancel: () => void;
  busy: boolean;
  dirty: boolean;
  submitLabel?: string;
}) {
  const blocks=draft.blocks??[];
  function updateBlock(id:string, patch:Partial<{heading:string;text:string}>) {
    onChange({...draft,blocks:blocks.map(block=>block.id===id?{...block,...patch}:block)});
  }
  return <form className="flow-document-editor" onSubmit={event=>{event.preventDefault();if(dirty&&draft.title.trim())onSave()}}>
    <label>작업물 제목<Input disabled={busy} value={draft.title} onChange={event=>onChange({...draft,title:event.target.value})} maxLength={160}/></label>
    {blocks.map((block,index)=><fieldset key={block.id}>
      <legend>{draft.format==="slides"?`${index+1}장`:`내용 ${index+1}`}</legend>
      <label>소제목<Input disabled={busy} value={block.heading} onChange={event=>updateBlock(block.id,{heading:event.target.value})} maxLength={500}/></label>
      <label>내용<Textarea disabled={busy} value={block.text} onChange={event=>updateBlock(block.id,{text:event.target.value})} rows={5} maxLength={20000}/></label>
      {blocks.length>1&&<Button type="button" color="primary" variant="ghost" pill={false} size="sm" disabled={busy} onClick={()=>onChange({...draft,blocks:blocks.filter(item=>item.id!==block.id)})}>{draft.format==="slides"?"이 장 삭제":"이 내용 삭제"}</Button>}
    </fieldset>)}
    <div className="flow-edit-actions">
      <Button type="button" color="primary" variant="ghost" pill={false} size="sm" disabled={busy} onClick={()=>onChange({...draft,blocks:[...blocks,{id:crypto.randomUUID(),heading:"",text:""}]})}>{draft.format==="slides"?"장 추가":"내용 추가"}</Button>
      <Button type="button" color="primary" variant="ghost" pill={false} size="sm" disabled={busy} onClick={()=>onChange({...draft,format:draft.format==="slides"?"document":"slides"})}>{draft.format==="slides"?"문서로 전환":"슬라이드로 전환"}</Button>
      {canConvertArtifactToContent(draft)&&<Button type="button" color="primary" variant="ghost" pill={false} size="sm" disabled={busy} onClick={()=>onChange(contentFromArtifact(draft))}>작업 화면으로 바꾸기</Button>}
      <Button type="submit" color="primary" variant="solid" pill={false} size="sm" disabled={busy||!dirty||!draft.title.trim()}>{busy?"저장 중":submitLabel}</Button>
      <Button type="button" color="primary" variant="ghost" pill={false} size="sm" disabled={busy} onClick={onCancel}>취소</Button>
    </div>
  </form>;
}
