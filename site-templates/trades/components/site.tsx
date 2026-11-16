import type { ReactNode } from "react";
import { content, type Page, type SectionKey, type Variant } from "@/lib/content";
import { labels } from "@/lib/i18n";

const t = labels(content.language);
const v: Variant = content.variant;
const tel = content.business.phone.replace(/[^\d+]/g, "");

function Logo({ className = "" }: { className?: string }) {
  if (content.brand.logoSvg) {
    // Sanitised by the Branding agent (allowlist SVG, no scripts/external refs).
    return (
      <span
        className={`logo inline-flex h-11 items-center ${className}`}
        aria-label={content.business.name}
        dangerouslySetInnerHTML={{ __html: content.brand.logoSvg }}
      />
    );
  }
  return <span className={`text-xl font-bold ${className}`}>{content.business.name}</span>;
}

export function PreviewRibbon() {
  return (
    <div className="bg-ink px-4 py-1.5 text-center text-xs font-medium text-white" role="note">
      {content.preview.notice}
      {content.preview.agency ? ` · ${content.preview.agency}` : ""}
    </div>
  );
}

export function Header() {
  const nav = content.pages.filter((p) => p.slug);
  return (
    <header className={v === "bold" ? "bg-brand text-white" : "border-b border-black/5 bg-white"}>
      <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-5 py-3">
        <a href="/" className="shrink-0">
          <Logo />
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
            {t.call} {content.business.phone}
          </a>
        )}
      </div>
    </header>
  );
}

function Hero() {
  const h = content.hero;
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
          {content.business.city && (
            <p className={`mb-4 text-sm font-semibold uppercase tracking-widest ${v === "bold" ? "text-white/80" : "text-brand"}`}>
              {content.business.trade} · {content.business.city}
            </p>
          )}
          <h1 className="text-4xl font-bold leading-tight sm:text-5xl">{h.headline}</h1>
          {h.subheadline && (
            <p className={`mt-5 max-w-xl text-lg ${v === "bold" ? "text-white/85" : "text-ink/70"}`}>{h.subheadline}</p>
          )}
          <div className="mt-8 flex flex-wrap gap-3">{cta}</div>
        </div>
        <div className={`hidden aspect-square items-center justify-center rounded-3xl p-10 md:flex ${v === "bold" ? "bg-white/10" : "bg-white shadow-xl"}`}>
          <Logo className="!h-auto w-full [&_svg]:w-full" />
        </div>
      </div>
    </section>
  );
}

function Services() {
  if (!content.services.length) return null;
  return (
    <section id="services" className="mx-auto max-w-6xl px-5 py-20">
      <h2 className="text-3xl font-bold">{t.services}</h2>
      <div className={`mt-10 grid gap-5 ${v === "minimal" ? "md:grid-cols-2" : "sm:grid-cols-2 lg:grid-cols-3"}`}>
        {content.services.map((s) => (
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

function About() {
  if (!content.about) return null;
  return (
    <section className={v === "classic" ? "bg-surface" : ""}>
      <div className="mx-auto max-w-3xl px-5 py-20">
        <h2 className="text-3xl font-bold">{t.about}</h2>
        <p className="mt-6 whitespace-pre-line text-lg leading-relaxed text-ink/80">{content.about}</p>
      </div>
    </section>
  );
}

function Areas() {
  if (!content.areas.length) return null;
  return (
    <section className="mx-auto max-w-6xl px-5 py-16">
      <h2 className="text-2xl font-bold">{t.areas}</h2>
      <ul className="mt-6 flex flex-wrap gap-2">
        {content.areas.map((a) => (
          <li key={a} className="rounded-full bg-brand/10 px-4 py-1.5 text-sm font-medium text-brand">
            {a}
          </li>
        ))}
      </ul>
    </section>
  );
}

function Contact() {
  const b = content.business;
  return (
    <section id="contact" className={v === "bold" ? "bg-ink text-white" : "bg-surface"}>
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
        <form className="space-y-3" aria-describedby="form-note" onSubmit={undefined}>
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

export function Footer() {
  return (
    <footer className="border-t border-black/5 bg-white">
      <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-3 px-5 py-8 text-sm text-ink/60 sm:flex-row">
        <span>© {new Date().getFullYear()} {content.business.name}. {t.rights}</span>
        <span>{content.preview.notice}</span>
      </div>
    </footer>
  );
}

const SECTIONS: Record<SectionKey, () => ReactNode> = {
  hero: Hero,
  services: Services,
  about: About,
  areas: Areas,
  contact: Contact,
};

export function PageBody({ page }: { page: Page }) {
  return (
    <main>
      {page.slug && (
        <div className="mx-auto max-w-6xl px-5 pt-14">
          <h1 className="text-4xl font-bold">{page.title}</h1>
        </div>
      )}
      {page.sections.map((key) => {
        const Section = SECTIONS[key];
        return Section ? <Section key={key} /> : null;
      })}
    </main>
  );
}
