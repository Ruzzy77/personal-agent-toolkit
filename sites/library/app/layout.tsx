import type { Metadata } from 'next';

import '@/src/fonts.css';
import '@/src/styles.css';

export const metadata: Metadata = {
  title: '발간호 라이브러리',
  description: 'Daily, Digest와 Research 발간호를 찾아 읽는 개인 Library',
  robots: { index: false, follow: false },
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ko">
      <body>{children}</body>
    </html>
  );
}
