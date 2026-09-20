import type { Metadata } from 'next';
import './globals.css';
import './workspace.css';
import '../styles/products.css';
import '../styles/context-workspace.css';
import '../styles/toolkit-pages.css';
import '../styles/library-workspace.css';
import { ToolkitShell } from './toolkit-shell';
import { UIProvider } from './ui';

export const metadata: Metadata = {
  metadataBase: new URL("https://personal-agent-toolkit.hiyaq77.workers.dev"),
  title: { default: "Toolkit", template: "%s · Toolkit" },
  robots: { index: false, follow: false },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ko">
      <body className="ui-document"><UIProvider><ToolkitShell>{children}</ToolkitShell></UIProvider></body>
    </html>
  );
}
