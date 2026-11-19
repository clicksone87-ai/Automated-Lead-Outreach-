/**
 * Typed content contract between the Website Builder agent and this template.
 * The builder writes content.json; anything empty is simply not rendered — the template never
 * shows placeholder facts (no invented reviews, years in business or certifications).
 */

export type Variant = "classic" | "bold" | "minimal";
/** Every preview contains all three designs: "/" uses `content.variant`, "/design/<v>/" all three. */
export const DESIGNS: Variant[] = ["classic", "bold", "minimal"];
export type SectionKey = "hero" | "services" | "about" | "areas" | "contact";

export interface Service {
  title: string;
  description: string;
}

export interface Page {
  /** "" for the home page; otherwise the path segment (same structure as the original site). */
  slug: string;
  title: string;
  sections: SectionKey[];
}

export interface SiteContent {
  variant: Variant;
  language: string;
  mode: "new" | "redesign";
  business: {
    name: string;
    trade: string;
    phone: string;
    email: string;
    address: string;
    city: string;
  };
  brand: {
    primary: string;
    accent: string;
    ink: string;
    surface: string;
    fontHeading: string;
    fontBody: string;
    logoSvg: string;
    /** Same logo with a one-off draw-in animation (generated from the sanitised SVG). */
    logoSvgAnimated?: string;
    /** For dark backgrounds: dark ink turned white (from the logo set). */
    logoSvgReversed?: string;
  };
  seo: { title: string; description: string };
  hero: { headline: string; subheadline: string; cta: string };
  services: Service[];
  about: string;
  areas: string[];
  pages: Page[];
  preview: { agency: string; notice: string };
}

export const HOME_SECTIONS: SectionKey[] = ["hero", "services", "about", "areas", "contact"];

export function pageBySlug(c: SiteContent, slug: string): Page | undefined {
  return c.pages.find((p) => p.slug === slug);
}

export function homePage(c: SiteContent): Page {
  return pageBySlug(c, "") ?? { slug: "", title: "Home", sections: HOME_SECTIONS };
}

/** CSS custom properties for the brand, set on <html>. */
export function brandVars(c: SiteContent): Record<string, string> {
  const b = c.brand;
  return {
    "--brand": b.primary,
    "--accent": b.accent,
    "--ink": b.ink,
    "--surface": b.surface,
    "--font-heading": b.fontHeading,
    "--font-body": b.fontBody,
  };
}
