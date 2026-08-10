import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Medialive restream",
  description: "Client dashboard for SRS/FFmpeg restreaming",
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
