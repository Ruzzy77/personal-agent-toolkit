import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "디자인 라이브러리",
  description:
    "형식, 내용과 필요한 기능을 바탕으로 설계 패턴과 선택적 디자인 레시피를 비교하는 참고 라이브러리",
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
