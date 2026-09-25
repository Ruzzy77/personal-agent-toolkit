"use client";

import { useState } from "react";
import { Button } from "@openai/apps-sdk-ui/components/Button";
import { ImageDialog } from "@personal-agent/flow-surface";
import { fileUrl } from "../lib/host-files";
import "./host-image-preview.css";

export function HostImagePreview({root,path,name}: {root:string;path:string;name:string}) {
  const [open,setOpen]=useState(false);
  const [failedSrc,setFailedSrc]=useState("");
  const src=fileUrl(root,path,true);
  return <div className="host-image-preview">
    {failedSrc===src
      ? <p role="alert">이미지를 표시하지 못했습니다. 원본 파일을 내려받아 확인해 주세요.</p>
      : <img className="host-image-preview-image" src={src} alt={name} onError={()=>setFailedSrc(src)}/>}
    {failedSrc!==src&&<div className="host-image-preview-actions"><Button type="button" color="primary" variant="ghost" pill={false} size="sm" onClick={()=>setOpen(true)}>확대</Button></div>}
    <ImageDialog image={open&&failedSrc!==src?{src,alt:name,title:name}:null} onClose={()=>setOpen(false)}/>
  </div>;
}
