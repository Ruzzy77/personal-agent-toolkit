"use client";

import { useEffect, useRef, useState } from "react";
import { Button } from "@openai/apps-sdk-ui/components/Button";
import { Input } from "@openai/apps-sdk-ui/components/Input";
import { Textarea } from "@openai/apps-sdk-ui/components/Textarea";
import { ArtifactPreview, ImageRegionEditor, fullImageRegion, validImageRegion, canConvertArtifactToContent, contentFromArtifact } from "@personal-agent/flow-surface";
import { fileUrl, hostCall } from "../../lib/host-files";
import { flowMediaUrl, type FlowArtifact } from "../../lib/flow-content";
import { FlowFilePicker } from "./flow-file-picker";

export type ImageSource = {root:string;path:string;version:string};

function imageSize(url:string):Promise<{width:number;height:number}> {
  return new Promise((resolve,reject)=>{
    const image=new window.Image();
    image.onload=()=>image.naturalWidth&&image.naturalHeight
      ?resolve({width:image.naturalWidth,height:image.naturalHeight})
      :reject(new Error("empty image"));
    image.onerror=()=>reject(new Error("image unavailable"));
    image.src=url;
  });
}

export function ImageEditor({draft,workspaceId,rootId,source,onSource,onChange,onSave,onCancel,busy,dirty,submitLabel="저장"}:{
  draft:FlowArtifact;
  workspaceId:string;
  rootId?:string;
  source:ImageSource|null;
  onSource:(source:ImageSource)=>void;
  onChange:(draft:FlowArtifact)=>void;
  onSave:()=>void;
  onCancel:()=>void;
  busy:boolean;
  dirty:boolean;
  submitLabel?:string;
}) {
  const [selecting,setSelecting]=useState(false);
  const [pickerOpen,setPickerOpen]=useState(!draft.src);
  const [regionOpen,setRegionOpen]=useState(Boolean(draft.crop));
  const [error,setError]=useState("");
  const request=useRef(0);
  useEffect(()=>()=>{request.current++;},[]);
  const canChoose=Boolean(rootId);
  const preview=source?fileUrl(source.root,source.path,true):flowMediaUrl(workspaceId,draft.src);
  const valid=Boolean(draft.title.trim()&&preview&&draft.width&&draft.height&&(!draft.crop||validImageRegion(draft.crop)));
  async function choose(root:string,path:string) {
    if(busy||selecting)return;
    const number=++request.current;
    setSelecting(true);setError("");
    try {
      const info=await hostCall<{path:string;type:string;bytes:number;version:string}>("host_files",{root,operation:"stat",path});
      if(number!==request.current)return;
      if(info.path!==path||info.type!=="file"||info.bytes>20*1024*1024||!/^sha256:[a-f0-9]{64}$/.test(info.version))
        throw new Error("invalid image file");
      const url=fileUrl(root,path,true);
      const size=await imageSize(url);
      if(number!==request.current)return;
      const name=path.split("/").at(-1)?.replace(/\.[^.]+$/,"")??"이미지";
      onSource({root,path,version:info.version});
      onChange({...draft,src:url,width:size.width,height:size.height,title:draft.title||name,crop:null});
      setPickerOpen(false);setRegionOpen(false);
    } catch {if(number===request.current)setError("이미지를 열지 못했습니다. 파일을 확인해 주세요.");}
    finally {if(number===request.current)setSelecting(false);}
  }
  return <form className="flow-image-editor" onSubmit={event=>{event.preventDefault();if(valid&&dirty&&!selecting)onSave()}}>
    <label>작업물 제목<Input disabled={busy||selecting} value={draft.title} onChange={event=>onChange({...draft,title:event.target.value})} maxLength={160}/></label>
    {canChoose&&<section className="flow-image-source"><div className="flow-image-source-head"><h4>이미지 파일</h4>{source&&<Button type="button" color="primary" variant="ghost" pill={false} size="sm" disabled={busy||selecting} onClick={()=>setPickerOpen(value=>!value)}>{pickerOpen?"파일 목록 닫기":"다른 파일 선택"}</Button>}</div>
      {source&&<p>{source.path}</p>}
      {pickerOpen&&<FlowFilePicker rootId={rootId} imagesOnly selected={new Set()} busy={busy||selecting} onPick={(root,path)=>void choose(root,path)}/>}
    </section>}
    {preview&&<div className="flow-image-preview"><ArtifactPreview artifact={draft.crop&&!validImageRegion(draft.crop)?{...draft,crop:null}:draft} resolveMediaUrl={()=>preview} compact/></div>}
    {preview&&<div className="flow-image-region-actions"><Button type="button" color="primary" variant="ghost" pill={false} size="sm" disabled={busy||selecting} aria-expanded={regionOpen} onClick={()=>setRegionOpen(value=>!value)}>{regionOpen?"영역 선택 닫기":draft.crop?"영역 조정":"영역 선택"}</Button>
      {draft.crop&&<Button type="button" color="primary" variant="ghost" pill={false} size="sm" disabled={busy||selecting} onClick={()=>onChange({...draft,crop:null})}>자르기 해제</Button>}
    </div>}
    {preview&&regionOpen&&<section className="flow-image-region-section" aria-label="이미지 영역"><ImageRegionEditor src={preview} alt={draft.alt||draft.title} width={draft.width??0} height={draft.height??0} value={draft.crop??fullImageRegion} disabled={busy||selecting} onChange={crop=>onChange({...draft,crop})}/></section>}
    <label>대체 텍스트<Textarea disabled={busy||selecting} value={draft.alt??""} onChange={event=>onChange({...draft,alt:event.target.value})} rows={2} maxLength={1000}/></label>
    {selecting&&<p className="flow-muted" role="status">이미지를 확인하는 중입니다.</p>}
    {error&&<p className="flow-error" role="alert">{error}</p>}
    <div className="flow-edit-actions">{canConvertArtifactToContent(draft)&&!source&&<Button type="button" color="primary" variant="ghost" pill={false} size="sm" disabled={busy||selecting} onClick={()=>onChange(contentFromArtifact(draft))}>작업 화면으로 바꾸기</Button>}<Button type="submit" color="primary" variant="solid" pill={false} size="sm" disabled={busy||selecting||!dirty||!valid}>{busy?"저장 중":submitLabel}</Button><Button type="button" color="primary" variant="ghost" pill={false} size="sm" disabled={busy||selecting} onClick={onCancel}>취소</Button></div>
  </form>;
}
