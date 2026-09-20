"use client";
import Link from "next/link";
import { Button } from "@openai/apps-sdk-ui/components/Button";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, useSyncExternalStore } from "react";
import { BookOpen, FileText, Folder, Image, LogOut, Menu as MenuIcon, Moon, Settings, Sun, UserRound, X } from "lucide-react";
import { IconButton, Menu } from "./ui";
import { clearOwnerIdentity, ownerFetch } from "../lib/owner-client";

const navigation = [
  { href: "/", label: "작업공간", icon: Folder },
  { href: "/journal", label: "Journal", icon: FileText },
  { href: "/library", label: "Library", icon: BookOpen },
  { href: "/design", label: "디자인 자료", icon: Image },
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
  const [dark, setDark] = useState(false), [error, setError] = useState(""), [menuOpen, setMenuOpen] = useState(false);
  useEffect(() => {
    let active = true;
    const observer = new MutationObserver(() => setDark(document.documentElement.dataset.theme === "dark"));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    queueMicrotask(() => {
      if (!active) return;
      let saved = null;
      try { saved = localStorage.getItem("toolkit-theme"); } catch {}
      const value = saved ?? (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
      document.documentElement.dataset.theme = value; setDark(value === "dark");
    });
    return () => { active = false; observer.disconnect(); };
  }, []);
  function theme() {
    const value = dark ? "light" : "dark";
    setDark(!dark); document.documentElement.dataset.theme = value;
    try { localStorage.setItem("toolkit-theme", value); } catch {}
  }
  async function logout() {
    try {
      const response = await ownerFetch("/auth/logout", { method: "POST" });
      if (!response.ok) throw new Error();
      clearOwnerIdentity(); router.replace("/signed-out"); router.refresh();
    } catch { setError("로그아웃하지 못했습니다. 다시 시도해 주세요."); }
  }
  if (pathname === "/signed-out") return children;
  return <div className="toolkit-layout">
    <a className="skip" href="#toolkit-content">본문으로</a>
    <header className="toolkit-sidebar" onKeyDown={event => { if (event.key === "Escape" && menuOpen) { setMenuOpen(false); document.querySelector<HTMLButtonElement>(".toolkit-mobile-toggle button")?.focus(); } }}>
      <div className="toolkit-brand-row">
        <Link className="toolkit-brand" href="/" onClick={() => setMenuOpen(false)}>Toolkit</Link>
        <span className="toolkit-mobile-toggle"><IconButton label={menuOpen ? "메뉴 닫기" : "메뉴 열기"} aria-expanded={menuOpen} aria-controls="toolkit-menu" onClick={() => setMenuOpen(!menuOpen)}>{menuOpen ? <X size="1em" /> : <MenuIcon size="1em" />}</IconButton></span>
      </div>
      <div id="toolkit-menu" className={"toolkit-menu" + (menuOpen ? " is-open" : "")} onKeyDown={event => { if (event.key === "Escape") { setMenuOpen(false); document.querySelector<HTMLButtonElement>(".toolkit-mobile-toggle button")?.focus(); } }}>
        <nav className="toolkit-navigation" aria-label="주요 메뉴">
          {navigation.map(({ href, label, icon: Icon }) => <Link href={href} key={href} onClick={() => setMenuOpen(false)} aria-current={(href === "/" ? pathname === "/" || pathname.startsWith("/context") : pathname.startsWith(href) || href === "/library" && pathname.startsWith("/editions") || href === "/settings" && pathname.startsWith("/manage")) ? "page" : undefined}><Icon size={20} aria-hidden="true" /><span>{label}</span></Link>)}
        </nav>
        <div className="toolkit-account">
          <button className="toolkit-nav-action" onClick={theme}>{dark ? <Sun size={20} aria-hidden="true" /> : <Moon size={20} aria-hidden="true" />}<span>{dark ? "라이트 모드" : "다크 모드"}</span></button>
          <Menu><Menu.Trigger><Button color="primary" variant="ghost" size="md" pill={false} className="toolkit-account-trigger"><UserRound size="1em" aria-hidden="true" />계정</Button></Menu.Trigger><Menu.Content align="start"><Menu.Item onSelect={() => void logout()}><LogOut size="1em" aria-hidden="true" />로그아웃</Menu.Item></Menu.Content></Menu>
        </div>
      </div>
    </header>
    <div id="toolkit-content" className="toolkit-content" tabIndex={-1} inert={mobile && menuOpen}>{error && <p role="alert" className="su-workspace">{error}</p>}{children}</div>
  </div>;
}
