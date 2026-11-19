import type { ReactNode } from "react";
import { DESIGNS, type Page, type SectionKey, type SiteContent, type Variant } from "../lib/content";
import { labels } from "../lib/i18n";
import { TradeIcon } from "../lib/trades";

/*
 * Three distinct designs from one content contract (V2: "three designs per trade with motion").
 *   classic — clean and trustworthy: light, split hero with a floating logo card, lifted cards
 *   bold    — dark and confident: full-bleed hero with a drifting brand glow, services marquee
 *   minimal — editorial: big type, lots of air, numbered services, underline motion
 * All motion is CSS (globals.css) and switches off under prefers-reduced-motion.
 */

/*
 * The content arrives as a prop (`c`), so the same components serve `next dev` and the
 * per-lead static renderer (render/render.tsx), which compiles them once per template version.
 */

function designsFor(content: SiteContent) {
  const t = labels(content.language);
  const b = content.business;
  const tel = b.phone.replace(/[^\d+]/g, "");
  const ctaHref = tel ? `tel:${tel}` : "#contact";


  function Logo({ animated = false, onDark = false, className = "" }: { animated?: boolean; onDark?: boolean; className?: string }) {
    const br = content.brand;
    // On dark backgrounds the reversed version keeps dark lettering readable.
    const svg = (onDark && br.logoSvgReversed) || (animated && br.logoSvgAnimated) || br.logoSvg;
    if (svg) {
      // Sanitised by the Branding agent (allowlist SVG, no scripts/external refs); the animated
      // version only adds a scoped <style> block generated in code.
      return (
        <span
          className={`logo inline-flex h-11 items-center ${className}`}
          role="img"
          aria-label={b.name}
          dangerouslySetInnerHTML={{ __html: svg }}
        />
      );
    }
    return <span className={`text-xl font-bold ${className}`}>{b.name}</span>;
  }

  function Arrow() {
    return (
      <svg viewBox="0 0 24 24" className="arrow size-4" fill="none" stroke="currentColor" strokeWidth={2} aria-hidden>
        <path d="M5 12h14M13 6l6 6-6 6" />
      </svg>
    );
  }

  // ── chrome ─────────────────────────────────────────────────────────────

  function PreviewRibbon({ v }: { v: Variant }) {
    return (
      <div className="flex flex-wrap items-center justify-center gap-x-4 gap-y-1 bg-ink px-4 py-1.5 text-center text-xs font-medium text-white" role="note">
        <span>
          {content.preview.notice}
          {content.preview.agency ? ` · ${content.preview.agency}` : ""}
        </span>
        <nav aria-label={t.design} className="flex items-center gap-1">
          <span className="opacity-70">{t.design}:</span>
          {DESIGNS.map((d, i) => (
            <a
              key={d}
              href={d === content.variant ? "/" : `/design/${d}/`}
              aria-current={d === v ? "page" : undefined}
              className={`rounded-full px-2 py-0.5 ${d === v ? "bg-white text-ink" : "hover:bg-white/15"}`}
            >
              {i + 1}
            </a>
          ))}
        </nav>
      </div>
    );
  }

  function Nav({ className = "" }: { className?: string }) {
    const nav = content.pages.filter((p) => p.slug);
    if (!nav.length) return null;
    return (
      <nav aria-label={t.menu} className={`hidden items-center gap-6 text-sm font-medium md:flex ${className}`}>
        {nav.map((p) => (
          <a key={p.slug} href={`/${p.slug}/`} className="link-grow opacity-80 hover:opacity-100">
            {p.title}
          </a>
        ))}
      </nav>
    );
  }

  function Header({ v }: { v: Variant }) {
    const call = tel && (
      <a href={`tel:${tel}`} className={`btn shrink-0 rounded-full px-4 py-2 text-sm font-semibold ${
        v === "bold" ? "bg-accent text-ink" : v === "minimal" ? "border border-ink/15 hover:border-ink" : "bg-brand text-white shadow-sm"
      }`}>
        {t.call}<span className="hidden sm:inline"> {b.phone}</span>
      </a>
    );
    return (
      <header className={
        v === "bold" ? "sticky top-0 z-20 bg-ink/90 text-white backdrop-blur"
          : v === "minimal" ? "bg-white"
            : "sticky top-0 z-20 border-b border-black/5 bg-white/85 backdrop-blur"
      }>
        <div className={`mx-auto flex items-center justify-between gap-4 px-5 ${v === "minimal" ? "max-w-5xl py-6" : "max-w-6xl py-3"}`}>
          <a href="/" className="shrink-0"><Logo onDark={v === "bold"} /></a>
          <Nav />
          {call}
        </div>
      </header>
    );
  }

  function Footer({ v }: { v: Variant }) {
    return (
      <footer className={v === "bold" ? "bg-ink text-white/60" : "border-t border-black/5 bg-white text-ink/60"}>
        <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-3 px-5 py-8 text-sm sm:flex-row">
          <span>© {new Date().getFullYear()} {b.name}. {t.rights}</span>
          <span>{content.preview.notice}</span>
        </div>
      </footer>
    );
  }

  // ── hero ───────────────────────────────────────────────────────────────

  function Eyebrow({ light = false }: { light?: boolean }) {
    if (!b.trade && !b.city) return null;
    return (
      <p className={`enter inline-flex items-center gap-2 rounded-full px-3 py-1 text-sm font-semibold ${
        light ? "bg-white/10 text-white" : "bg-brand/10 text-brand"
      }`}>
        <TradeIcon trade={b.trade} className="size-4" />
        {[b.trade, b.city].filter(Boolean).join(" · ")}
      </p>
    );
  }

  function HeroClassic() {
    const h = content.hero;
    return (
      <section className="hero-classic relative overflow-hidden bg-surface">
        <div className="mx-auto grid max-w-6xl items-center gap-12 px-5 py-20 md:grid-cols-[1.3fr_1fr] md:py-28">
          <div>
            <Eyebrow />
            <h1 className="enter enter-2 mt-5 text-4xl font-bold leading-[1.08] sm:text-6xl">{h.headline}</h1>
            {h.subheadline && <p className="enter enter-3 mt-6 max-w-xl text-lg text-ink/70">{h.subheadline}</p>}
            <div className="enter enter-4 mt-9 flex flex-wrap gap-3">
              <a href={ctaHref} className="btn btn-shine inline-flex items-center gap-2 rounded-full bg-brand px-6 py-3 font-semibold text-white shadow-lg shadow-brand/20">
                {h.cta || t.contact} <Arrow />
              </a>
              {content.services.length > 0 && (
                <a href="#services" className="btn inline-flex items-center rounded-full border border-ink/10 bg-white px-6 py-3 font-semibold">
                  {t.services}
                </a>
              )}
            </div>
          </div>
          <div className="enter enter-3 relative hidden md:block">
            <div className="ring-glow absolute -inset-6 rounded-[2.5rem]" aria-hidden />
            <div className="float relative flex aspect-square items-center justify-center rounded-[2rem] bg-white p-10 shadow-2xl shadow-ink/10">
              <Logo animated className="!h-auto w-full [&_svg]:w-full" />
            </div>
          </div>
        </div>
      </section>
    );
  }

  function HeroBold() {
    const h = content.hero;
    return (
      <section className="relative overflow-hidden bg-ink text-white">
        <div className="blob blob-a" aria-hidden />
        <div className="blob blob-b" aria-hidden />
        <div className="relative mx-auto max-w-6xl px-5 pb-24 pt-20 md:pb-32 md:pt-28">
          <Eyebrow light />
          <h1 className="enter enter-2 mt-6 max-w-4xl text-5xl font-extrabold leading-[1.02] tracking-tight sm:text-7xl">
            <span className="highlight">{h.headline}</span>
          </h1>
          {h.subheadline && <p className="enter enter-3 mt-7 max-w-2xl text-lg text-white/75 sm:text-xl">{h.subheadline}</p>}
          <div className="enter enter-4 mt-10 flex flex-wrap items-center gap-5">
            <a href={ctaHref} className="btn btn-shine inline-flex items-center gap-2 rounded-full bg-accent px-7 py-4 text-lg font-bold text-ink">
              {h.cta || t.contact} <Arrow />
            </a>
            {tel && <span className="text-white/70">{b.phone}</span>}
          </div>
        </div>
      </section>
    );
  }

  function HeroMinimal() {
    const h = content.hero;
    return (
      <section className="mx-auto max-w-5xl px-5 pb-20 pt-16 md:pb-28 md:pt-24">
        <p className="enter text-xs font-semibold uppercase tracking-[0.25em] text-ink/50">
          {[b.trade, b.city].filter(Boolean).join(" — ")}
        </p>
        <h1 className="enter enter-2 mt-6 text-5xl font-light leading-[1.05] tracking-tight sm:text-7xl">{h.headline}</h1>
        <div className="enter enter-3 mt-10 grid gap-8 border-t border-ink/10 pt-8 md:grid-cols-[1fr_auto] md:items-end">
          {h.subheadline ? <p className="max-w-xl text-lg text-ink/65">{h.subheadline}</p> : <span />}
          <a href={ctaHref} className="link-grow inline-flex items-center gap-2 text-lg font-semibold text-brand">
            {h.cta || t.contact} <Arrow />
          </a>
        </div>
      </section>
    );
  }

  function Hero({ v }: { v: Variant }) {
    if (!content.hero.headline) return null;
    return v === "bold" ? <HeroBold /> : v === "minimal" ? <HeroMinimal /> : <HeroClassic />;
  }

  // ── sections ───────────────────────────────────────────────────────────

  function Services({ v }: { v: Variant }) {
    const items = content.services;
    if (!items.length) return null;
    if (v === "minimal") {
      return (
        <section id="services" className="mx-auto max-w-5xl px-5 py-20">
          <h2 className="reveal text-xs font-semibold uppercase tracking-[0.25em] text-ink/50">{t.services}</h2>
          <ol className="mt-8 divide-y divide-ink/10 border-y border-ink/10">
            {items.map((s, i) => (
              <li key={s.title} className="reveal row-slide grid gap-2 py-7 md:grid-cols-[5rem_1fr_1.4fr] md:gap-8">
                <span className="font-mono text-sm text-ink/40">{String(i + 1).padStart(2, "0")}</span>
                <h3 className="text-2xl font-light">{s.title}</h3>
                <p className="text-ink/65">{s.description}</p>
              </li>
            ))}
          </ol>
        </section>
      );
    }
    if (v === "bold") {
      return (
        <section id="services" className="bg-ink pb-24 text-white">
          <div className="marquee overflow-hidden border-y border-white/10 bg-accent py-4 text-ink" aria-hidden>
            <div className="marquee-track flex gap-10 whitespace-nowrap text-xl font-bold uppercase tracking-wide">
              {[...items, ...items, ...items, ...items].map((s, i) => (
                <span key={i} className="inline-flex items-center gap-10">{s.title}<span>✦</span></span>
              ))}
            </div>
          </div>
          <div className="mx-auto max-w-6xl px-5 pt-20">
            <h2 className="reveal text-4xl font-extrabold">{t.services}</h2>
            <div className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {items.map((s, i) => (
                <article key={s.title} className="reveal glow-card rounded-3xl border border-white/10 bg-white/[0.04] p-7">
                  <span className="text-sm font-bold text-accent">{String(i + 1).padStart(2, "0")}</span>
                  <h3 className="mt-4 text-xl font-bold">{s.title}</h3>
                  <p className="mt-2 text-white/65">{s.description}</p>
                </article>
              ))}
            </div>
          </div>
        </section>
      );
    }
    return (
      <section id="services" className="mx-auto max-w-6xl px-5 py-24">
        <h2 className="reveal text-3xl font-bold sm:text-4xl">{t.services}</h2>
        <div className="mt-12 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {items.map((s) => (
            <article key={s.title} className="reveal lift rounded-2xl border border-black/5 bg-white p-7 shadow-sm">
              <span className="inline-flex size-11 items-center justify-center rounded-xl bg-brand/10 text-brand">
                <TradeIcon trade={b.trade} />
              </span>
              <h3 className="mt-5 text-lg font-semibold">{s.title}</h3>
              <p className="mt-2 text-ink/70">{s.description}</p>
            </article>
          ))}
        </div>
      </section>
    );
  }

  function About({ v }: { v: Variant }) {
    if (!content.about) return null;
    if (v === "minimal") {
      return (
        <section className="mx-auto grid max-w-5xl gap-8 px-5 py-20 md:grid-cols-[14rem_1fr]">
          <h2 className="reveal text-xs font-semibold uppercase tracking-[0.25em] text-ink/50 md:sticky md:top-10 md:self-start">{t.about}</h2>
          <p className="reveal whitespace-pre-line text-2xl font-light leading-relaxed text-ink/80">{content.about}</p>
        </section>
      );
    }
    if (v === "bold") {
      return (
        <section className="bg-brand text-white">
          <div className="mx-auto max-w-4xl px-5 py-24">
            <h2 className="reveal text-sm font-bold uppercase tracking-[0.2em] text-white/70">{t.about}</h2>
            <p className="reveal mt-6 whitespace-pre-line text-2xl font-semibold leading-snug sm:text-3xl">{content.about}</p>
          </div>
        </section>
      );
    }
    return (
      <section className="bg-surface">
        <div className="mx-auto grid max-w-6xl gap-10 px-5 py-24 md:grid-cols-[1fr_2fr]">
          <div className="reveal">
            <div className="h-1.5 w-12 rounded-full bg-accent" aria-hidden />
            <h2 className="mt-5 text-3xl font-bold sm:text-4xl">{t.about}</h2>
          </div>
          <p className="reveal whitespace-pre-line text-lg leading-relaxed text-ink/80">{content.about}</p>
        </div>
      </section>
    );
  }

  function Areas({ v }: { v: Variant }) {
    const areas = content.areas;
    if (!areas.length) return null;
    if (v === "minimal") {
      return (
        <section className="mx-auto max-w-5xl px-5 pb-20">
          <h2 className="reveal text-xs font-semibold uppercase tracking-[0.25em] text-ink/50">{t.areas}</h2>
          <p className="reveal mt-5 text-2xl font-light text-ink/80">{areas.join(" · ")}</p>
        </section>
      );
    }
    return (
      <section className={v === "bold" ? "bg-ink text-white" : ""}>
        <div className="mx-auto max-w-6xl px-5 py-16">
          <h2 className="reveal text-2xl font-bold">{t.areas}</h2>
          <ul className="mt-6 flex flex-wrap gap-2">
            {areas.map((a) => (
              <li key={a} className={`reveal chip rounded-full px-4 py-1.5 text-sm font-medium ${
                v === "bold" ? "border border-white/15 text-white/85" : "bg-brand/10 text-brand"
              }`}>
                {a}
              </li>
            ))}
          </ul>
        </div>
      </section>
    );
  }

  function Contact({ v }: { v: Variant }) {
    const dark = v === "bold";
    const field = `mt-1 w-full rounded-xl border px-4 py-3 ${dark ? "border-white/15 bg-white/5 text-white" : "border-black/10 bg-white text-ink"}`;
    return (
      <section id="contact" className={dark ? "bg-ink text-white" : v === "minimal" ? "border-t border-ink/10" : "bg-surface"}>
        <div className={`mx-auto grid gap-12 px-5 py-24 md:grid-cols-2 ${v === "minimal" ? "max-w-5xl" : "max-w-6xl"}`}>
          <div className="reveal">
            <h2 className={v === "minimal" ? "text-4xl font-light" : "text-3xl font-bold sm:text-4xl"}>{t.contact}</h2>
            <dl className="mt-8 space-y-3 text-lg">
              {b.phone && (
                <div>
                  <dt className="sr-only">Phone</dt>
                  <dd><a className="link-grow text-2xl font-semibold" href={`tel:${tel}`}>{b.phone}</a></dd>
                </div>
              )}
              {b.email && (
                <div>
                  <dt className="sr-only">Email</dt>
                  <dd><a className="link-grow" href={`mailto:${b.email}`}>{b.email}</a></dd>
                </div>
              )}
              {b.address && (
                <div>
                  <dt className="sr-only">Address</dt>
                  <dd className="opacity-75">{b.address}</dd>
                </div>
              )}
            </dl>
          </div>
          <form className={`reveal space-y-3 ${v === "classic" ? "rounded-3xl bg-white p-7 shadow-xl shadow-ink/5" : ""}`} aria-describedby="form-note">
            <label className="block text-sm font-medium">
              {t.name}
              <input disabled className={field} />
            </label>
            <label className="block text-sm font-medium">
              {t.email}
              <input disabled type="email" className={field} />
            </label>
            <label className="block text-sm font-medium">
              {t.message}
              <textarea disabled rows={4} className={field} />
            </label>
            <button type="button" disabled className={`rounded-full px-6 py-3 font-semibold opacity-70 ${dark ? "bg-accent text-ink" : "bg-brand text-white"}`}>
              {t.send}
            </button>
            <p id="form-note" className="text-sm opacity-70">{t.formDisabled}</p>
          </form>
        </div>
      </section>
    );
  }

  const SECTIONS: Record<SectionKey, (p: { v: Variant }) => ReactNode> = {
    hero: Hero,
    services: Services,
    about: About,
    areas: Areas,
    contact: Contact,
  };

  function PageBody({ page, v }: { page: Page; v: Variant }) {
    return (
      <main>
        {page.slug && (
          <div className={`mx-auto px-5 pt-14 ${v === "minimal" ? "max-w-5xl" : "max-w-6xl"}`}>
            <h1 className={`enter ${v === "minimal" ? "text-5xl font-light" : "text-4xl font-bold"}`}>{page.title}</h1>
          </div>
        )}
        {page.sections.map((key) => {
          const Section = SECTIONS[key];
          return Section ? <Section key={key} v={v} /> : null;
        })}
      </main>
    );
  }

  /** One full page in one design. */
  function SiteIn({ page, v }: { page: Page; v: Variant }) {
    return (
      <div className={`design-${v}`}>
        <PreviewRibbon v={v} />
        <Header v={v} />
        <PageBody page={page} v={v} />
        <Footer v={v} />
      </div>
    );
  }

  return SiteIn;
}

/** One full page in one design. */
export function Site({ c, page, v }: { c: SiteContent; page: Page; v: Variant }) {
  const SiteIn = designsFor(c);
  return <SiteIn page={page} v={v} />;
}
