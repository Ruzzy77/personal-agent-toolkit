"use client";

import { useEffect, useState } from "react";
import { TextContentView, HtmlContentView, resolveRelativeImagePath } from "@personal-agent/flow-surface";
import { fileUrl, hostCall } from "../../lib/host-files";
import { fileMediaKind } from "../../lib/file-media";
import { FileMediaPreview } from "../file-media-preview";
import { HostImagePreview } from "../host-image-preview";

type FileInfo={path:string;type:string;mime:string;bytes:number};
const TEXT=/\.(md|markdown|txt|json|jsonc|csv|tsv|ya?ml|toml|ini|py|js|jsx|ts|tsx|css|html?|sh|xml|svg|log|sql)$/i;
const HTML=/\.html?$/i;
const RASTER=/^image\/(png|jpeg|gif|webp|avif)$/;
const MAX_TEXT=2_097_152;

export function FlowHostFile({root,path}:{root:string;path:string}){
  const [info,setInfo]=useState<FileInfo|null>(null);
  const [body,setBody]=useState<string|null>(null);
  const [readFailed,setReadFailed]=useState(false);
  const [error,setError]=useState("");
  useEffect(()=>{
    let active=true;
    void hostCall<FileInfo>("host_files",{root,operation:"stat",path}).then(async value=>{
      if(!active)return;
      if(value.type!=="file"||value.path!==path)throw new Error("파일을 열 수 없습니다.");
      setInfo(value);
      if((TEXT.test(path)||value.mime.startsWith("text/"))&&value.bytes<=MAX_TEXT){
        try{
          const result=await hostCall<{files:Array<{content:string}>}>("host_read",{root,files:[{path}],max_bytes:MAX_TEXT});
          if(active)setBody(result.files[0]?.content??"");
        }catch{if(active)setReadFailed(true)}
      }
    }).catch(()=>{if(active)setError("파일을 열지 못했습니다. 원본에서 다시 확인해 주세요.")});
    return()=>{active=false};
  },[root,path]);
  if(error)return <p className="flow-muted" role="alert">{error}</p>;
  const readable=info&&(TEXT.test(path)||info.mime.startsWith("text/"))&&info.bytes<=MAX_TEXT;
  if(!info||(readable&&body===null&&!readFailed))
    return <p className="flow-muted" role="status">파일을 불러오는 중입니다.</p>;
  const name=path.split("/").at(-1)??path;
  const preview=fileUrl(root,path,true);
  const html=HTML.test(path)||info.mime.split(";")[0].trim()==="text/html";
  if(RASTER.test(info.mime))return <HostImagePreview root={root} path={path} name={name}/>;
  if(fileMediaKind(info.mime))return <FileMediaPreview root={root} path={path} mime={info.mime} label={name}/>;
  if(html){
    if(info.bytes>MAX_TEXT)return <div className="flow-host-unavailable"><p>파일이 커서 화면에 표시할 수 없습니다.</p><a href={fileUrl(root,path)} download>원본 파일 받기</a></div>;
    return <HtmlContentView body={body??undefined} src={body===null?preview:undefined} name={name}/>;
  }
  if(info.mime==="application/pdf")return <iframe className="flow-host-preview" src={preview} sandbox="" title={name+" 미리보기"}/>;
  if(body!==null)return <TextContentView body={body} path={path} title={name} headingLevel={4} className="flow-context-document" resolveImageSrc={src=>{const target=resolveRelativeImagePath(path,src);return target?fileUrl(root,target,true):null}}/>;
  return <div className="flow-host-unavailable"><p>이 형식은 화면에서 바로 볼 수 없습니다.</p><a href={fileUrl(root,path)} download>원본 파일 받기</a></div>;
}
