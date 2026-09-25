"use client";

import { MediaPlayer } from "@personal-agent/flow-surface";
import { fileMediaKind } from "../lib/file-media";
import { fileUrl } from "../lib/host-files";

export function FileMediaPreview({root,path,mime,label}:{
  root:string;path:string;mime:string;label:string;
}) {
  const kind=fileMediaKind(mime);
  return kind?<MediaPlayer kind={kind} src={fileUrl(root,path,true)} label={label}/>:null;
}
