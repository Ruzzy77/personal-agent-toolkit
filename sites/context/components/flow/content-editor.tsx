"use client";

import { useEffect, useRef, useState } from "react";
import { Button } from "@openai/apps-sdk-ui/components/Button";
import { Input } from "@openai/apps-sdk-ui/components/Input";
import { ArtifactPreview, EditorActions, EditorLayout, CompositionEditor, contentImageTargetSlot, contentRenderers, setContentSource, workspaceFilePresentation, workspaceFileBlock, appendContentBlock, workspaceMediaType, previewableContentDraft } from "@personal-agent/flow-surface";
import type { ContentBlock, ContentSourceTarget, ContentPick } from "@personal-agent/flow-surface";
import { fileUrl, hostCall } from "../../lib/host-files";
import { contentDraftValid, contentPreview, imageSlot, type PendingImage, type PendingImages } from "../../lib/flow-composition";
import { flowMediaUrl, type FlowArtifact, type FlowComposition } from "../../lib/flow-content";
import { FlowFilePicker } from "./flow-file-picker";
import { FlowResourceBlock } from "./flow-resource-block";

type Picker=ContentSourceTarget | {kind:"file";mode:"add"};
function imageReady(url:string):Promise<{width:number;height:number}>{
  return new Promise((resolve,reject)=>{
    const image=new window.Image();
    image.onload=()=>image.naturalWidth&&image.naturalHeight?resolve({width:image.naturalWidth,height:image.naturalHeight}):reject(new Error("empty_image"));
    image.onerror=()=>reject(new Error("invalid_image"));
    image.src=url;
  });
}

export function ContentEditor({draft,workspaceId,rootId,imageSources,onImageSource,onChange,onSave,onCancel,busy,dirty,submitLabel="저장"}:{
  draft:FlowArtifact;workspaceId:string;rootId?:string;imageSources:PendingImages;
  onImageSource:(slot:string,source:PendingImage)=>void;
  onChange:(draft:FlowArtifact)=>void;onSave:()=>void;onCancel:()=>void;
  busy:boolean;dirty:boolean;submitLabel?:string;
}) {
  const [picker,setPicker]=useState<Picker|null>(null);
  const [selecting,setSelecting]=useState(false);
  const [error,setError]=useState("");
  const request=useRef(0);
  useEffect(()=>()=>{request.current++;},[]);
  if(!draft.composition)return null;
  const composition:FlowComposition=draft.composition;
  const valid=Boolean(draft.title.trim()&&contentDraftValid(composition,imageSources));
  const previewDraft=previewableContentDraft(composition,imageSources);
  function change(value:FlowComposition){onChange({...draft,composition:value});setError("");}
  const pick:ContentPick=(kind,blockId,field,itemId)=>{
    if(!rootId){setError("파일 작업공간을 확인할 수 없습니다.");return;}
    setPicker({kind:kind==="pdf"?"file":kind,blockId,field,itemId});setError("");
  }
  function addFile(){
    if(!rootId){setError("파일 작업공간을 확인할 수 없습니다.");return;}
    setPicker({kind:"file",mode:"add"});setError("");
  }
  async function chooseFile(root:string,path:string){
    if(!picker||selecting||busy)return;
    const number=++request.current;
    setSelecting(true);setError("");
    try{
      if(root!==rootId)throw new Error("wrong_root");
      const stat=await hostCall<{path:string;type:string;bytes:number;version:string}>("host_files",{root,operation:"stat",path});
      if(number!==request.current)return;
      if(stat.path!==path||stat.type!=="file"||stat.bytes>(workspaceFilePresentation(path)?.maxBytes??20*1024*1024)||!/^sha256:[a-f0-9]{64}$/.test(stat.version))
        throw new Error("invalid_file");
      if(picker.kind==="image"){
        if(!/\.(?:png|jpe?g|webp|gif)$/i.test(path))throw new Error("invalid_type");
        const url=fileUrl(root,path,true);
        const size=await imageReady(url);
        if(number!==request.current)return;
        const slot=contentImageTargetSlot(picker);
        onImageSource(slot,{root,path,version:stat.version});
        change(setContentSource(composition,picker,url,size));
      }else{
        if(picker.kind==="file"?!workspaceFilePresentation(path):workspaceMediaType(path)?.kind!==picker.kind)throw new Error("invalid_type");
        const src="/api/flow/files/content?"+new URLSearchParams({workspaceId,path});
        const url=flowMediaUrl(workspaceId,src);
        if(!url)throw new Error("invalid_url");
        const response=await fetch(url,{method:"HEAD"});
        if(number!==request.current)return;
        const mime=response.headers.get("content-type")?.split(";",1)[0];
        if(!response.ok||(picker.kind==="file"?mime!==workspaceFilePresentation(path)?.responseType:mime!==workspaceMediaType(path)?.mime))throw new Error("unavailable");
        change("mode" in picker?appendContentBlock(composition,workspaceFileBlock(path,src)):setContentSource(composition,picker,src,undefined,path));
      }
      setPicker(null);
    }catch{if(number===request.current)setError("파일을 연결하지 못했습니다. 파일을 확인한 뒤 다시 선택해 주세요.");}
    finally{if(number===request.current)setSelecting(false);}
  }
  return <form className="flow-content-editor" onSubmit={event=>{event.preventDefault();if(valid&&dirty&&!selecting)onSave()}}>
    <EditorActions busy={busy} cancelDisabled={selecting} disabled={selecting||!dirty||!valid} saveLabel={submitLabel} onCancel={onCancel}/>
    <EditorLayout fieldsClassName="flow-content-editor-fields" headingLevel={4} preview={previewDraft?<ArtifactPreview artifact={{...draft,title:draft.title||"작업물",composition:previewDraft}} renderers={{...contentRenderers,resource:(props:{block:ContentBlock;context?:{headingLevel?:number}})=><FlowResourceBlock {...props} artifactTitle={draft.title}/>}}
      prepareContent={value=>contentPreview(workspaceId,value,imageSources,source=>fileUrl(source.root,source.path,true))} compact/>:<p className="flow-muted">내용을 입력하면 여기에 표시됩니다.</p>}>
    <label className="flow-content-field">작업물 제목<Input value={draft.title} maxLength={160} disabled={busy||selecting} onChange={event=>onChange({...draft,title:event.target.value})}/></label>
    <CompositionEditor composition={composition} onChange={change} onPick={pick} onAddFile={addFile} resolveImageUrl={(src,blockId)=>{const pending=imageSources[imageSlot(blockId)];return pending?fileUrl(pending.root,pending.path,true):flowMediaUrl(workspaceId,src)}} busy={busy||selecting}/>
    {picker&&<section className="flow-content-picker" aria-label="파일 선택">
      <div className="flow-content-picker-head"><h4>{picker.kind==="image"?"이미지 선택":picker.kind==="video"?"영상 선택":picker.kind==="audio"?"오디오 선택":"파일 선택"}</h4><Button type="button" color="primary" variant="ghost" pill={false} size="sm" disabled={selecting} onClick={()=>setPicker(null)}>닫기</Button></div>
      <FlowFilePicker rootId={rootId} accept={picker.kind} selected={new Set()} busy={selecting||busy} onPick={(root,path)=>void chooseFile(root,path)}/>
    </section>}
    {selecting&&<p className="flow-muted" role="status">파일을 확인하는 중입니다.</p>}
    {error&&<p className="flow-error" role="alert">{error}</p>}
    {!valid&&dirty&&<p className="flow-muted" role="status">제목, 내용 또는 연결할 파일을 확인해 주세요.</p>}
    </EditorLayout>
  </form>;
}
