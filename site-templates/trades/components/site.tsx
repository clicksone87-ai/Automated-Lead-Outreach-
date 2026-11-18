import type { ReactNode } from "react";
import type { Page, SectionKey, SiteContent } from "../lib/content";
import { labels } from "../lib/i18n";

/** Every component takes the content as a prop, so the same code serves `next dev` and the
 * per-lead static renderer (render/render.tsx). */
type Props = { c: SiteContent };

const telOf = (c: SiteContent) => c.business.phone.replace(/[^\d+]/g, "");

function Logo({ c, className = "" }: Props & { className?: string }) {
  if (c.brand.logoSvg) {
    // Sanitised by the Branding agent (allowlist SVG, no scripts/external refs).
    return (
      <span
        className={`logo inline-flex h-11 items-center ${className}`}
        aria-label={c.business.name}
        dangerouslySetInnerHTML={{ __html: c.brand.logoSvg }}
      />
    );
  }
  return <span className={`text-xl font-bold ${className}`}>{c.business.name}</span>;
}

export function PreviewRibbon({ c }: Props) {
  return (
    <div className="bg-ink px-4 py-1.5 text-center text-xs font-medium text-white" role="note">
      {c.preview.notice}
      {c.preview.agency ? ` · ${c.preview.agency}` : ""}
    </div>
  );
}

export function Header({ c }: Props) {
  const t = labels(c.language);
  const v = c.variant;
  const tel = telOf(c);
  const nav = c.pages.filter((p) => p.slug);
  return (
    <header className={v === "bold" ? "bg-brand text-white" : "border-b border-black/5 bg-white"}>
      <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-5 py-3">
        <a href="/" className="shrink-0">
          <Logo c={c} />
        </a>
        <nav aria-label={t.menu} className="hidden items-center gap-6 text-sm font-medium md:flex">
          {nav.map((p) => (
            <a key={p.slug} href={`/${p.slug}/`} className="opacity-80 hover:opacity-100">
              {p.title}
            </a>
          ))}
        </nav>
        {tel && (
          <a
            href={`tel:${tel}`}
            className={`rounded-full px-4 py-2 text-sm font-semibold shadow-sm ${
              v === "bold" ? "bg-white text-brand" : "bg-brand text-white"
            }`}
          >
            {t.call} {c.business.phone}
          </a>
        )}
      </div>
    </header>
  );
}

function Hero({ c }: Props) {
  const t = labels(c.language);
  const v = c.variant;
  const tel = telOf(c);
  const h = c.hero;
  if (!h.headline) return null;
  const cta = (
    <a
      href={tel ? `tel:${tel}` : "#contact"}
      className="inline-flex items-center rounded-full bg-accent px-6 py-3 font-semibold text-ink shadow-md transition hover:brightness-95"
    >
      {h.cta || t.contact}
    </a>
  );
  if (v === "minimal") {
    return (
      <section className="mx-auto max-w-4xl px-5 py-24 text-center">
        <h1 className="text-4xl font-semibold sm:text-6xl">{h.headline}</h1>
        {h.subheadline && <p className="mx-auto mt-6 max-w-2xl text-lg text-ink/70">{h.subheadline}</p>}
        <div className="mt-10">{cta}</div>
      </section>
    );
  }
  return (
    <section className={v === "bold" ? "bg-brand text-white" : "bg-surface"}>
      <div className="mx-auto grid max-w-6xl items-center gap-10 px-5 py-20 md:grid-cols-[1.4fr_1fr] md:py-28">
        <div>
          {c.business.city && (
            <p className={`mb-4 text-sm font-semibold uppercase tracking-widest ${v === "bold" ? "text-white/80" : "text-brand"}`}>
              {c.business.trade} · {c.business.city}
            </p>
          )}
          <h1 className="text-4xl font-bold leading-tight sm:text-5xl">{h.headline}</h1>
          {h.subheadline && (
            <p className={`mt-5 max-w-xl text-lg ${v === "bold" ? "text-white/85" : "text-ink/70"}`}>{h.subheadline}</p>
          )}
          <div className="mt-8 flex flex-wrap gap-3">{cta}</div>
        </div>
        <div className={`hidden aspect-square items-center justify-center rounded-3xl p-10 md:flex ${v === "bold" ? "bg-white/10" : "bg-white shadow-xl"}`}>
          <Logo c={c} className="!h-auto w-full [&_svg]:w-full" />
        </div>
      </div>
    </section>
  );
}

function Services({ c }: Props) {
  const t = labels(c.language);
  const v = c.variant;
  if (!c.services.length) return null;
  return (
    <section id="services" className="mx-auto max-w-6xl px-5 py-20">
      <h2 className="text-3xl font-bold">{t.services}</h2>
      <div className={`mt-10 grid gap-5 ${v === "minimal" ? "md:grid-cols-2" : "sm:grid-cols-2 lg:grid-cols-3"}`}>
        {c.services.map((s) => (
          <article
            key={s.title}
            className={
              v === "minimal"
                ? "border-t border-ink/10 pt-5"
                : "rounded-2xl border border-black/5 bg-white p-6 shadow-sm transition hover:shadow-md"
            }
          >
            {v !== "minimal" && <div className="mb-4 h-1.5 w-10 rounded-full bg-accent" aria-hidden />}
            <h3 className="text-lg font-semibold">{s.title}</h3>
            <p className="mt-2 text-ink/70">{s.description}</p>
          </article>
        ))}
      </div>
    </section>
  );
}

function About({ c }: Props) {
  const t = labels(c.language);
  if (!c.about) return null;
  return (
    <section className={c.variant === "classic" ? "bg-surface" : ""}>
      <div className="mx-auto max-w-3xl px-5 py-20">
        <h2 className="text-3xl font-bold">{t.about}</h2>
        <p className="mt-6 whitespace-pre-line text-lg leading-relaxed text-ink/80">{c.about}</p>
      </div>
    </section>
  );
}

function Areas({ c }: Props) {
  const t = labels(c.language);
  if (!c.areas.length) return null;
  return (
    <section className="mx-auto max-w-6xl px-5 py-16">
      <h2 className="text-2xl font-bold">{t.areas}</h2>
      <ul className="mt-6 flex flex-wrap gap-2">
        {c.areas.map((a) => (
          <li key={a} className="rounded-full bg-brand/10 px-4 py-1.5 text-sm font-medium text-brand">
            {a}
          </li>
        ))}
      </ul>
    </section>
  );
}

function Contact({ c }: Props) {
  const t = labels(c.language);
  const tel = telOf(c);
  const b = c.business;
  return (
    <section id="contact" className={c.variant === "bold" ? "bg-ink text-white" : "bg-surface"}>
      <div className="mx-auto grid max-w-6xl gap-10 px-5 py-20 md:grid-cols-2">
        <div>
          <h2 className="text-3xl font-bold">{t.contact}</h2>
          <dl className="mt-6 space-y-3 text-lg">
            {b.phone && (
              <div>
                <dt className="sr-only">Phone</dt>
                <dd><a className="font-semibold underline-offset-4 hover:underline" href={`tel:${tel}`}>{b.phone}</a></dd>
              </div>
            )}
            {b.email && (
              <div>
                <dt className="sr-only">Email</dt>
                <dd><a className="underline-offset-4 hover:underline" href={`mailto:${b.email}`}>{b.email}</a></dd>
              </div>
            )}
            {b.address && (
              <div>
                <dt className="sr-only">Address</dt>
                <dd className="opacity-80">{b.address}</dd>
              </div>
            )}
          </dl>
        </div>
        <form className="space-y-3" aria-describedby="form-note">
          <label className="block text-sm font-medium">
            {t.name}
            <input disabled className="mt-1 w-full rounded-xl border border-black/10 bg-white px-4 py-3 text-ink" />
          </label>
          <label className="block text-sm font-medium">
            {t.email}
            <input disabled type="email" className="mt-1 w-full rounded-xl border border-black/10 bg-white px-4 py-3 text-ink" />
          </label>
          <label className="block text-sm font-medium">
            {t.message}
            <textarea disabled rows={4} className="mt-1 w-full rounded-xl border border-black/10 bg-white px-4 py-3 text-ink" />
          </label>
          <button type="button" disabled className="rounded-full bg-brand px-6 py-3 font-semibold text-white opacity-70">
            {t.send}
          </button>
          <p id="form-note" className="text-sm opacity-70">{t.formDisabled}</p>
        </form>
      </div>
    </section>
  );
}

export function Footer({ c }: Props) {
  const t = labels(c.language);
  return (
    <footer className="border-t border-black/5 bg-white">
      <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-3 px-5 py-8 text-sm text-ink/60 sm:flex-row">
        <span>© {new Date().getFullYear()} {c.business.name}. {t.rights}</span>
        <span>{c.preview.notice}</span>
      </div>
    </footer>
  );
}

const SECTIONS: Record<SectionKey, (p: Props) => ReactNode> = {
  hero: Hero,
  services: Services,
  about: About,
  areas: Areas,
  contact: Contact,
};

export function PageBody({ c, page }: Props & { page: Page }) {
  return (
    <main>
      {page.slug && (
        <div className="mx-auto max-w-6xl px-5 pt-14">
          <h1 className="text-4xl font-bold">{page.title}</h1>
        </div>
      )}
      {page.sections.map((key) => {
        const Section = SECTIONS[key];
        return Section ? <Section key={key} c={c} /> : null;
      })}
    </main>
  );
}

/** The whole <body> of one page: ribbon, header, page sections and footer. */
export function SiteBody({ c, page }: Props & { page: Page }) {
  return (
    <>
      <PreviewRibbon c={c} />
      <Header c={c} />
      <PageBody c={c} page={page} />
      <Footer c={c} />
    </>
  );
}
