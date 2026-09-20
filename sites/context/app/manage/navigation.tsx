"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { ChevronDown } from "lucide-react";
import { Menu, UiButton } from "../ui";
const products = [["/manage", "Corpus"],["/manage/sense", "Sense"],["/manage/library", "Library"],["/manage/design", "디자인 자료"]];
export default function Navigation() {
  const pathname=usePathname();
  const current=products.find(([href])=>href===pathname)?.[1]??"Corpus";
  return <header className="su-appbar site-header"><div className="su-appbar__inner">
    <nav className="product-navigation-wide" aria-label="관리할 제품">{products.map(([href,label])=><Link key={href} href={href} aria-current={pathname===href?"page":undefined}>{label}</Link>)}</nav>
    <div className="product-navigation-compact"><Menu><Menu.Trigger><UiButton type="button">{current}<ChevronDown size="1em" aria-hidden="true"/></UiButton></Menu.Trigger><Menu.Content minWidth={160}>{products.map(([href,label])=><Menu.Link key={href} href={href} aria-current={pathname===href?"page":undefined}>{label}</Menu.Link>)}</Menu.Content></Menu></div>
  </div></header>;
}
