"use client";
import Link from "next/link";
import {useToolkitTheme} from "./toolkit-theme";
import { Button } from "@openai/apps-sdk-ui/components/Button";
import { usePathname, useRouter } from "next/navigation";
import { useState, useSyncExternalStore } from "react";
import { BookOpen, FileText, Folder, LayoutDashboard, Image, LogOut, Menu as MenuIcon, Moon, Settings, Sun, UserRound, X } from "lucide-react";
import { IconButton, Menu } from "./ui";
import { clearOwnerIdentity, ownerFetch } from "../lib/owner-client";

const navigation = [
  { href: "/flow", label: "작업공간", icon: LayoutDashboard },
  { href: "/flow?screen=library", label: "라이브러리", icon: BookOpen },
  { href: "/flow?screen=files", label: "파일", icon: Folder },
  { href: "/settings", label: "설정", icon: Settings },
];

const subscribeMobile = (notify: () => void) => {
  const media = matchMedia('(max-width:760px)');
  media.addEventListener('change', notify);
  return () => media.removeEventListener('change', notify);
};
export function ToolkitShell({ children }: { children: React.ReactNode }) {
  const router = useRouter(), pathname = usePathname();
  const mobile = useSyncExternalStore(subscribeMobile, () => matchMedia('(max-width:760px)').matches, () => false);
  const {theme:currentTheme,setTheme}=useToolkitTheme();
  const dark=currentTheme==="dark";
  const [error, setError] = useState(""), [menuOpen, setMenuOpen] = useState(false);
  function theme() {setTheme(dark ? "light" : "dark");}
  async function logout() {
    try {
      const response = await ownerFetch("/auth/logout", { method: "POST" });
      if (!response.ok) throw new Error();
      clearOwnerIdentity(); router.replace("/signed-out"); router.refresh();
    } catch { setError("로그아웃하지 못했습니다. 다시 시도해 주세요."); }
  }
  if (pathname === "/signed-out" || pathname === "/flow") return children;
  return <div className="toolkit-layout">
    <a className="skip" href="#toolkit-content">본문으로</a>
    <header className="toolkit-sidebar" onKeyDown={event => { if (event.key === "Escape" && menuOpen) { setMenuOpen(false); document.querySelector<HTMLButtonElement>(".toolkit-mobile-toggle button")?.focus(); } }}>
      <div className="toolkit-brand-row">
        <Link className="toolkit-brand" href="/flow" onClick={() => setMenuOpen(false)}>Toolkit</Link>
        <span className="toolkit-mobile-toggle"><IconButton label={menuOpen ? "메뉴 닫기" : "메뉴 열기"} aria-expanded={menuOpen} aria-controls="toolkit-menu" onClick={() => setMenuOpen(!menuOpen)}>{menuOpen ? <X size="1em" /> : <MenuIcon size="1em" />}</IconButton></span>
      </div>
      <div id="toolkit-menu" className={"toolkit-menu" + (menuOpen ? " is-open" : "")} onKeyDown={event => { if (event.key === "Escape") { setMenuOpen(false); document.querySelector<HTMLButtonElement>(".toolkit-mobile-toggle button")?.focus(); } }}>
        <nav className="toolkit-navigation" aria-label="주요 메뉴">
          {navigation.map(({ href, label, icon: Icon }) => <Link href={href} key={href} onClick={() => setMenuOpen(false)} aria-current={(href === "/files" ? pathname === "/files" || pathname.startsWith("/context") : pathname.startsWith(href) || href === "/library" && pathname.startsWith("/editions") || href === "/settings" && pathname.startsWith("/manage")) ? "page" : undefined}><Icon size={20} aria-hidden="true" /><span>{label}</span></Link>)}
        </nav>
        <div className="toolkit-account">
          <button className="toolkit-nav-action" onClick={theme}>{dark ? <Sun size={20} aria-hidden="true" /> : <Moon size={20} aria-hidden="true" />}<span>{dark ? "밝게" : "어둡게"}</span></button>
          <Menu><Menu.Trigger><Button color="primary" variant="ghost" size="md" pill={false} className="toolkit-account-trigger"><UserRound size="1em" aria-hidden="true" />계정</Button></Menu.Trigger><Menu.Content align="start"><Menu.Item onSelect={() => void logout()}><LogOut size="1em" aria-hidden="true" />로그아웃</Menu.Item></Menu.Content></Menu>
        </div>
      </div>
    </header>
    <div id="toolkit-content" className="toolkit-content" tabIndex={-1} inert={mobile && menuOpen}>{error && <p role="alert" className="su-workspace">{error}</p>}{children}</div>
  </div>;
}
