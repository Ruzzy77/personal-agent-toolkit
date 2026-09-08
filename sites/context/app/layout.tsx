import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  metadataBase: new URL('https://resolver-control-plane.ruzzy.chatgpt.site'),
  title: 'Workspace',
  description: 'Sense · Corpus',
  openGraph: {
    title: 'Workspace',
    description: 'Sense · Corpus',
    images: ['https://resolver-control-plane.ruzzy.chatgpt.site/og.png'],
  },
  twitter: {
    card: 'summary_large_image',
    title: 'Workspace',
    description: 'Sense · Corpus',
    images: ['https://resolver-control-plane.ruzzy.chatgpt.site/og.png'],
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ko">
      <body>{children}</body>
    </html>
  );
}
