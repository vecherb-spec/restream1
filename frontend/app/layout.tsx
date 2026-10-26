import type { Metadata } from "next";
import "./globals.css";
import { PRODUCT_NAME } from "@/lib/branding";

export const metadata: Metadata = {
  title: PRODUCT_NAME,
  description: "Платформа для одновременной трансляции на YouTube, VK, Rutube, Telegram, Twitch и другие площадки",
  icons: {
    icon: "/favicon.png",
    apple: "/favicon.png",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ru">
      <body>{children}</body>
    </html>
  );
}
