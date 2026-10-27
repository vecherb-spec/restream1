import type { Metadata } from "next";
import "./globals.css";
import { PRODUCT_NAME } from "@/lib/branding";

export const metadata: Metadata = {
  title: PRODUCT_NAME,
  description: "Один эфир на YouTube, VK, Telegram и другие площадки из OBS или vMix",
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
