/**
 * Per-lead static renderer. Compiled once per template version (tsc, see tsconfig.render.json)
 * and run for every preview: content in, finished HTML pages out. No Next.js build per lead.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { Site } from "../components/site";
import { brandVars, DESIGNS, homePage, type Page, type SiteContent } from "../lib/content";

const esc = (s: string) =>
  s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

function document(c: SiteContent, page: Page, body: string): string {
  const style = Object.entries(brandVars(c))
    .map(([k, v]) => `${k}:${v.replace(/[;"<>]/g, "")}`)
    .join(";");
  const title = page.slug ? `${page.title} · ${c.business.name}` : c.seo.title || c.business.name;
  return [
    "<!DOCTYPE html>",
    `<html lang="${esc(c.language || "en")}" style="${esc(style)}">`,
    "<head>",
    '<meta charset="utf-8">',
    '<meta name="viewport" content="width=device-width, initial-scale=1">',
    // Private preview: never indexed (Caddy also sends X-Robots-Tag: noindex).
    '<meta name="robots" content="noindex, nofollow">',
    `<title>${esc(title)}</title>`,
    c.seo.description ? `<meta name="description" content="${esc(c.seo.description)}">` : "",
    '<link rel="stylesheet" href="/styles.css">',
    "</head>",
    `<body>${body}</body>`,
    "</html>",
  ]
    .filter(Boolean)
    .join("\n");
}

/** Every page of the site: path (relative to the site root) → HTML. "/" and the extra pages
 * use the chosen design; /design/<v>/ shows the home page in each of the three designs. */
export function renderSite(c: SiteContent): Record<string, string> {
  const out: Record<string, string> = {};
  const home = homePage(c);
  for (const page of [home, ...c.pages.filter((p) => p.slug && p.slug !== "design")]) {
    const body = renderToStaticMarkup(<Site c={c} page={page} v={c.variant} />);
    out[page.slug ? `${page.slug}/index.html` : "index.html"] = document(c, page, body);
  }
  for (const v of DESIGNS) {
    const body = renderToStaticMarkup(<Site c={c} page={home} v={v} />);
    out[`design/${v}/index.html`] = document(c, home, body);
  }
  return out;
}
