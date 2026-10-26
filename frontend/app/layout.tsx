import type { Metadata } from "next";
import "./globals.css";
import { PRODUCT_NAME } from "@/lib/branding";

export const metadata: Metadata = {
  title: PRODUCT_NAME,
  description: "Restream-платформа для церковных онлайн-трансляций",
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
