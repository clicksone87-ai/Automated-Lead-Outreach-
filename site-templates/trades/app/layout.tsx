import type { Metadata, Viewport } from "next";
import type { CSSProperties, ReactNode } from "react";
import { brandVars } from "@/lib/content";
import { sample } from "@/lib/sample";
import "./globals.css";

// Development preview of the template with content.json (`npm run dev`). Real previews are
// rendered per lead by render/render.tsx, which writes its own <head>.
export const metadata: Metadata = {
  title: sample.seo.title || sample.business.name || "Template preview",
  robots: { index: false, follow: false },
};

export const viewport: Viewport = { width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang={sample.language} style={brandVars(sample) as CSSProperties}>
      <body>{children}</body>
    </html>
  );
}
