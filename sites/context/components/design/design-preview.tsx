"use client";
import { useEffect, useState } from "react";
import { ownerFetch } from "@/lib/owner-client";
import { isolatedDesignPreview } from "@/lib/design-preview";
import { UiButton } from "@/app/ui";

export function DesignPreview({href,title}:{href:string;title:string}) {
  const [html,setHtml]=useState<string|null>(null),[failed,setFailed]=useState(false),[retry,setRetry]=useState(0);
  useEffect(()=>{
    const controller=new AbortController();
    ownerFetch(href,{signal:controller.signal}).then(async response=>{
      if(!response.ok || !response.headers.get("content-type")?.includes("text/html")) throw new Error();
      return response.text();
    }).then(source=>{
      if(!controller.signal.aborted){setHtml(isolatedDesignPreview(source));setFailed(false);}
    }).catch(error=>{if(error.name!=="AbortError")setFailed(true);});
    return()=>controller.abort();
  },[href,retry]);
  if(failed)return <div className="design-preview-state" role="alert"><p>미리보기를 불러오지 못했습니다.</p><UiButton onClick={()=>{setFailed(false);setHtml(null);setRetry(n=>n+1);}}>다시 시도</UiButton></div>;
  if(html===null)return <p className="design-preview-state" role="status">미리보기를 불러오는 중…</p>;
  return <iframe srcDoc={html} title={title} sandbox="" referrerPolicy="no-referrer"/>;
}
