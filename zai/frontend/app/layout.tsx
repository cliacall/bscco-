import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "bscco · BSC Meme 终端",
  description: "four.meme 自主爬取 · AI 交易 · Cobo 钱包",
};

export const viewport: Viewport = {
  themeColor: "#1c1410",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN" data-theme="dark">
      <body className="overflow-hidden">{children}</body>
    </html>
  );
}
