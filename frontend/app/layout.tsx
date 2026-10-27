import type { Metadata } from "next";
import "./globals.css";
import { PRODUCT_NAME } from "@/lib/branding";

export const metadata: Metadata = {
  title: PRODUCT_NAME,
  description: "Рестриминг из OBS и vMix на YouTube, VK Видео, Rutube, Telegram, Twitch и RTMP-направления",
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
