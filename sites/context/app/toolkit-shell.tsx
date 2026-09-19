"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { LogOut, Moon, Sun } from "lucide-react";
import { IconButton } from "./ui";
import { clearOwnerIdentity, ownerFetch } from "../lib/owner-client";
const navigation=[["/","작업공간"],["/journal","Journal"],["/library","Library"],["/design","디자인 자료"],["/settings","설정"]];
export function ToolkitShell({children}:{children:React.ReactNode}){
  const router=useRouter(),pathname=usePathname(),[dark,setDark]=useState(false),[error,setError]=useState("");
  useEffect(()=>{
    let active=true;
    const observer=new MutationObserver(()=>setDark(document.documentElement.dataset.theme==="dark"));
    observer.observe(document.documentElement,{attributes:true,attributeFilter:["data-theme"]});
    queueMicrotask(()=>{if(!active)return;let saved=null;try{saved=localStorage.getItem("toolkit-theme");}catch{}const value=saved??(matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light");document.documentElement.dataset.theme=value;setDark(value==="dark");});
    return()=>{active=false;observer.disconnect();};
  },[]);
  function theme(){const value=dark?"light":"dark";setDark(!dark);document.documentElement.dataset.theme=value;try{localStorage.setItem("toolkit-theme",value);}catch{}}
  async function logout(){try{const response=await ownerFetch("/auth/logout",{method:"POST"});if(!response.ok)throw new Error();clearOwnerIdentity();router.replace("/signed-out");router.refresh();}catch{setError("로그아웃하지 못했습니다. 다시 시도해 주세요.");}}
  if(pathname==="/signed-out")return children;
  return <><a className="skip" href="#toolkit-content">본문으로</a><header className="su-appbar toolkit-appbar"><div className="su-appbar__inner"><Link className="toolkit-brand" href="/">Toolkit</Link><nav className="toolkit-navigation" aria-label="주요 메뉴">{navigation.map(([href,label])=><Link href={href} key={href} aria-current={(href==="/"?(pathname==="/"||pathname.startsWith("/context")):pathname.startsWith(href)||href==="/settings"&&pathname.startsWith("/manage"))?"page":undefined}>{label}</Link>)}</nav><div className="su-row toolkit-account"><IconButton label={dark?"라이트 모드":"다크 모드"} onClick={theme}>{dark?<Sun size="1em"/>:<Moon size="1em"/>}</IconButton><IconButton label="로그아웃" onClick={()=>void logout()}><LogOut size="1em"/></IconButton></div></div></header>{error&&<p role="alert" className="su-workspace">{error}</p>}<div id="toolkit-content" tabIndex={-1}>{children}</div></>;
}
