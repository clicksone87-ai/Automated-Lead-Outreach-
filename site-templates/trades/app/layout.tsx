import type { Metadata, Viewport } from "next";
import type { CSSProperties, ReactNode } from "react";
import { content } from "@/lib/content";
import "./globals.css";

export const metadata: Metadata = {
  title: content.seo.title || content.business.name,
  description: content.seo.description,
  // Private preview: never indexed (Caddy also sends X-Robots-Tag: noindex).
  robots: { index: false, follow: false },
};

export const viewport: Viewport = { width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: ReactNode }) {
  const b = content.brand;
  const style = {
    "--brand": b.primary,
    "--accent": b.accent,
    "--ink": b.ink,
    "--surface": b.surface,
    "--font-heading": b.fontHeading,
    "--font-body": b.fontBody,
  } as CSSProperties;
  return (
    <html lang={content.language} style={style}>
      <body>{children}</body>
    </html>
  );
}
