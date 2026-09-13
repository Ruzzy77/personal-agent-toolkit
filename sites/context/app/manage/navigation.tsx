"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
const products = [
  ["/manage", "Corpus"],
  ["/manage/sense", "Sense"],
  ["/manage/library", "Library"],
  ["/manage/design", "Design"],
];
export default function Navigation() {
  const pathname = usePathname();
  return (
    <header className="site-header">
      <div className="site-header-inner">
        <Link className="workspace-return" href="/">
          Workspace로 돌아가기
        </Link>
        <span className="site-title">관리</span>
        <nav aria-label="관리할 제품">
          {products.map(([href, label]) => (
            <Link key={href} href={href} aria-current={pathname === href ? "page" : undefined}>
              {label}
            </Link>
          ))}
        </nav>
      </div>
    </header>
  );
}
