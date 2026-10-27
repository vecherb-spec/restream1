import type { Metadata } from "next";
import "./globals.css";
import { PRODUCT_NAME } from "@/lib/branding";

export const metadata: Metadata = {
  title: PRODUCT_NAME,
  description:
    "Отправьте один поток из OBS или vMix, а мы одновременно выведем его на YouTube, VK, Rutube, Telegram и другие площадки.",
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
