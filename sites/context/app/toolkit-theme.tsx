"use client";
import {createContext,useCallback,useContext,useEffect,useState,type ReactNode} from "react";
import {UIKitRoot} from "@personal-agent/ui-kit/react";
type Theme="light"|"dark";
const ThemeContext=createContext<{theme:Theme;setTheme:(value:Theme)=>void}|null>(null);
export function ToolkitThemeProvider({children}:{children:ReactNode}){
 const [theme,update]=useState<Theme>("light");
 useEffect(()=>{
  let active=true;
  const media=matchMedia("(prefers-color-scheme: dark)");
  const read=()=>{let saved=null;try{saved=localStorage.getItem("toolkit-theme")}catch{}
   const value=saved==="light"||saved==="dark"?saved:media.matches?"dark":"light";
   if(active)update(value);
  };
  queueMicrotask(read);window.addEventListener("storage",read);media.addEventListener("change",read);
  return()=>{active=false;window.removeEventListener("storage",read);media.removeEventListener("change",read)};
 },[]);
 const setTheme=useCallback((value:Theme)=>{update(value);try{localStorage.setItem("toolkit-theme",value)}catch{}},[]);
 return <ThemeContext.Provider value={{theme,setTheme}}><UIKitRoot colorScheme={theme}>{children}</UIKitRoot></ThemeContext.Provider>;
}
export function useToolkitTheme(){const value=useContext(ThemeContext);if(!value)throw new Error("ToolkitThemeProvider is required");return value;}
