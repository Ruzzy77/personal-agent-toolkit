"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { ArrowLeft, ChevronDown, Moon, Sun } from "lucide-react";
import { ActionMenu, IconLink, Menu, UiButton } from "../ui";
const products = [["/manage", "Corpus"],["/manage/sense", "Sense"],["/manage/library", "Library"],["/manage/design", "Design"]];
export default function Navigation() {
  const pathname=usePathname(),[dark,setDark]=useState(false);
  useEffect(()=>{queueMicrotask(()=>setDark(document.documentElement.dataset.theme==='dark'));},[]);
  const current=products.find(([href])=>href===pathname)?.[1]??'Corpus';
  return <header className="su-appbar site-header"><div className="su-appbar__inner">
    <IconLink href="/" label="본문"><ArrowLeft className="su-icon" aria-hidden="true"/></IconLink>
    <nav className="product-navigation-wide" aria-label="관리할 제품">{products.map(([href,label])=><Link key={href} href={href} aria-current={pathname===href?'page':undefined}>{label}</Link>)}</nav>
    <div className="product-navigation-compact"><Menu><Menu.Trigger><UiButton type="button">{current}<ChevronDown size="1em" aria-hidden="true"/></UiButton></Menu.Trigger><Menu.Content minWidth={160}>{products.map(([href,label])=><Menu.Link key={href} href={href} aria-current={pathname===href?'page':undefined}>{label}</Menu.Link>)}</Menu.Content></Menu></div>
    <ActionMenu><Menu.Item onSelect={()=>{setDark(!dark);document.documentElement.dataset.theme=dark?'light':'dark';}}>{dark?<Sun size="1em" aria-hidden="true"/>:<Moon size="1em" aria-hidden="true"/>}{dark?'라이트 모드':'다크 모드'}</Menu.Item></ActionMenu>
  </div></header>;
}
