import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Medialive restream",
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
