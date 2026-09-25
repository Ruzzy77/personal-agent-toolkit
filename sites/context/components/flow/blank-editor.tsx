"use client";

import { Button } from "@openai/apps-sdk-ui/components/Button";
import { Input } from "@openai/apps-sdk-ui/components/Input";
import { Textarea } from "@openai/apps-sdk-ui/components/Textarea";
import { blankTextReady } from "../../lib/flow-blank";
import type { FlowArtifact } from "../../lib/flow-content";

export function BlankEditor({draft,busy,dirty,onChange,onSave,onCancel,onEdit}:{
  draft:FlowArtifact;busy:boolean;dirty:boolean;
  onChange:(draft:FlowArtifact)=>void;onSave:()=>void;onCancel:()=>void;onEdit:()=>void;
}) {
  const text=draft.draftText??"";
  return <form className="flow-blank-editor" onSubmit={event=>{event.preventDefault();if(blankTextReady(draft)&&dirty)onSave()}}>
    <label>작업물 제목<Input disabled={busy} value={draft.title} onChange={event=>onChange({...draft,title:event.target.value})} maxLength={160}/></label>
    <label>내용<Textarea aria-label="내용 입력" disabled={busy} value={text} onChange={event=>onChange({...draft,draftText:event.target.value})} placeholder="내용을 입력하세요." rows={7} maxLength={20000}/></label>
    {text.trim()&&!blankTextReady(draft)&&<p className="flow-muted" role="status">제목을 쓰고 문단은 100개 이하로 정리해 주세요.</p>}
    <div className="flow-edit-actions">
      <Button type="submit" color="primary" variant="solid" pill={false} size="sm" disabled={busy||!dirty||!blankTextReady(draft)}>{busy?"저장 중":"작업 화면에 넣기"}</Button>
      <Button type="button" color="primary" variant="ghost" pill={false} size="sm" disabled={busy} onClick={onEdit}>내용과 배치 편집</Button>
      {dirty&&<Button type="button" color="primary" variant="ghost" pill={false} size="sm" disabled={busy} onClick={onCancel}>취소</Button>}
    </div>
  </form>;
}
