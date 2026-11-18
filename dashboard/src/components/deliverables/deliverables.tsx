"use client";

import { ExternalLink, FileText, LayoutGrid, Monitor, Smartphone } from "lucide-react";
import { useState } from "react";
import { StatusPill } from "@/components/common/status-pill";
import { Button } from "@/components/ui/button";
import { fileUrl } from "@/lib/api";
import { titleCase } from "@/lib/format";
import { cn } from "@/lib/utils";

type Tone = "success" | "danger" | "warning" | "neutral" | "info";

export function qaTone(status: unknown): Tone {
  return status === "passed" ? "success" : status === "failed" ? "danger" : status === "needs_review" ? "warning" : "neutral";
}

export function qaLabel(status: unknown): string {
  return status === "pending" || !status ? "QA pending" : `QA ${titleCase(String(status))}`;
}

export interface LogoItem {
  id?: unknown;
  variant?: unknown;
  png_path?: unknown;
  svg_path?: unknown;
  qa_status?: unknown;
  concept_name?: unknown;
  style?: unknown;
  typography?: unknown;
  similarity?: unknown;
  assets?: unknown;
}

type LogoView = "full" | "reversed" | "mono" | "mark" | "animated";
const LOGO_VIEWS: { key: LogoView; label: string }[] = [
  { key: "full", label: "Colour" },
  { key: "reversed", label: "Reversed" },
  { key: "mono", label: "Mono" },
  { key: "mark", label: "Mark" },
  { key: "animated", label: "Animated" },
];

interface LogoAssets {
  set?: Record<string, { svg?: string; png?: string }>;
  mark?: Record<string, { svg?: string; png?: Record<string, string> }>;
  animated?: string;
  mark_animated?: string;
}

/** What to show and offer for download for one view of a logo set. */
function logoView(lg: LogoItem, view: LogoView): { src: string | null; svg: string | null; png: string | null; dark: boolean; square: boolean } {
  const a = (lg.assets ?? {}) as LogoAssets;
  if (view === "mark") {
    const m = a.mark?.full;
    return { src: fileUrl(m?.png?.["512"] ?? null), svg: fileUrl(m?.svg ?? null), png: fileUrl(m?.png?.["512"] ?? null), dark: false, square: true };
  }
  if (view === "animated") {
    // An SVG with CSS keyframes only (generated in code from the sanitised logo); <img> never runs scripts.
    return { src: fileUrl(a.animated ?? null), svg: fileUrl(a.animated ?? null), png: null, dark: false, square: false };
  }
  const v = view === "full" ? { svg: lg.svg_path as string, png: lg.png_path as string } : a.set?.[view];
  return { src: fileUrl(v?.png ?? null), svg: fileUrl(v?.svg ?? null), png: fileUrl(v?.png ?? null), dark: view === "reversed", square: false };
}

function LogoCard({ lg }: { lg: LogoItem }) {
  const [view, setView] = useState<LogoView>("full");
  const [replay, setReplay] = useState(0);
  const typo = (lg.typography ?? {}) as Record<string, unknown>;
  const sim = (lg.similarity ?? {}) as Record<string, unknown>;
  const assets = (lg.assets ?? {}) as LogoAssets;
  const name = String(lg.concept_name ?? typo.concept_name ?? `Concept ${String(lg.variant)}`);
  const available = LOGO_VIEWS.filter((v) =>
    v.key === "full" ? true : v.key === "mark" ? Boolean(assets.mark?.full) : v.key === "animated" ? Boolean(assets.animated) : Boolean(assets.set?.[v.key]),
  );
  const shown = logoView(lg, view);
  return (
    <figure className="overflow-hidden rounded-xl border bg-card">
      <div
        className={cn(
          "relative flex aspect-[2/1] items-center justify-center p-3",
          shown.dark ? "bg-[#0f172a]" : "bg-[repeating-conic-gradient(var(--color-muted)_0_25%,transparent_0_50%)] bg-[length:16px_16px]",
        )}
      >
        {shown.src ? (
          // eslint-disable-next-line @next/next/no-img-element -- API-served artefact
          <img key={`${view}-${replay}`} src={shown.src} alt={`${name} logo, ${view}`} className={cn("max-h-full max-w-full object-contain", shown.square && "h-full")} />
        ) : (
          <span className="text-xs text-muted-foreground">Not available</span>
        )}
        {view === "animated" && shown.src && (
          <button type="button" onClick={() => setReplay((n) => n + 1)} className="absolute bottom-2 right-2 rounded-md bg-background/90 px-2 py-1 text-[11px] font-medium shadow-sm hover:bg-background">
            Replay
          </button>
        )}
      </div>
      {available.length > 1 && (
        <div className="flex flex-wrap gap-1 border-t px-2 py-1.5" role="group" aria-label={`${name} versions`}>
          {available.map((v) => (
            <button
              key={v.key}
              type="button"
              onClick={() => setView(v.key)}
              aria-pressed={view === v.key}
              className={cn("rounded-md px-2 py-0.5 text-[11px] font-medium", view === v.key ? "bg-secondary text-secondary-foreground" : "text-muted-foreground hover:bg-muted")}
            >
              {v.label}
            </button>
          ))}
        </div>
      )}
      <figcaption className="space-y-1.5 border-t p-3 text-xs">
        <div className="flex items-center justify-between gap-2">
          <span className="truncate font-medium" title={name}>{name}</span>
          <StatusPill tone={qaTone(lg.qa_status)}>{qaLabel(lg.qa_status)}</StatusPill>
        </div>
        <div className="flex flex-wrap gap-x-3 gap-y-1 text-muted-foreground">
          <span>{titleCase(String(lg.style ?? typo.style ?? ""))}</span>
          <span>wording: {String(sim.text_check ?? "—")}</span>
          <span>
            similarity:{" "}
            {Number(sim.checked_against ?? 0) === 0
              ? "no competitor logos to compare"
              : sim.too_similar
                ? "too close to a competitor"
                : `clear (${Number(sim.checked_against)} checked)`}
          </span>
        </div>
        {(shown.svg || shown.png) && (
          <div className="flex gap-3 pt-0.5">
            {shown.svg && <a href={shown.svg} download className="font-medium text-primary hover:underline">SVG</a>}
            {shown.png && <a href={shown.png} download className="font-medium text-primary hover:underline">PNG</a>}
          </div>
        )}
      </figcaption>
    </figure>
  );
}

/** Logo sets: each concept with its colour, reversed, mono, square mark and animated versions. */
export function LogoGrid({ logos, compact = false }: { logos: LogoItem[]; compact?: boolean }) {
  return (
    <div className={cn("grid gap-3", compact ? "grid-cols-1" : "sm:grid-cols-2 xl:grid-cols-3")}>
      {logos.map((lg) => (
        <LogoCard key={String(lg.id ?? lg.variant)} lg={lg} />
      ))}
    </div>
  );
}

export interface PreviewItem {
  preview_url?: unknown;
  screenshots?: unknown;
  qa_status?: unknown;
  mode?: unknown;
  variant?: unknown;
  lighthouse?: unknown;
  test_results?: unknown;
  pdf_path?: unknown;
  content?: unknown;
}

/** Website preview: desktop/mobile screenshots, QA, Lighthouse, links to the private preview. */
export function PreviewCard({ preview }: { preview: PreviewItem }) {
  const [view, setView] = useState<"desktop" | "mobile" | "designs">("desktop");
  const shots = (preview.screenshots ?? {}) as Record<string, unknown>;
  const designs = Object.entries((shots.designs ?? {}) as Record<string, string>);
  const tests = (preview.test_results ?? {}) as Record<string, unknown>;
  const lighthouse = (preview.lighthouse ?? tests.lighthouse ?? {}) as Record<string, number>;
  const img = view === "designs" ? null : fileUrl((shots[view] as string | null) ?? null);
  const base = typeof preview.preview_url === "string" ? preview.preview_url.replace(/\/?$/, "/") : "";
  const pdf = fileUrl(preview.pdf_path as string | null);
  const variant = String(preview.variant ?? ((preview.content ?? {}) as Record<string, unknown>).variant ?? "");
  return (
    <div className="overflow-hidden rounded-xl border bg-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b px-3 py-2">
        <div className="flex items-center gap-2 text-xs">
          <StatusPill tone={qaTone(preview.qa_status)}>{qaLabel(preview.qa_status)}</StatusPill>
          <span className="text-muted-foreground">{titleCase(String(preview.mode ?? ""))} · {titleCase(variant)}</span>
        </div>
        <div className="flex items-center gap-1" role="group" aria-label="Screenshot size">
          <Button size="sm" variant={view === "desktop" ? "secondary" : "ghost"} onClick={() => setView("desktop")} aria-pressed={view === "desktop"}>
            <Monitor className="size-3.5" /> Desktop
          </Button>
          <Button size="sm" variant={view === "mobile" ? "secondary" : "ghost"} onClick={() => setView("mobile")} aria-pressed={view === "mobile"}>
            <Smartphone className="size-3.5" /> Mobile
          </Button>
          {designs.length > 0 && (
            <Button size="sm" variant={view === "designs" ? "secondary" : "ghost"} onClick={() => setView("designs")} aria-pressed={view === "designs"}>
              <LayoutGrid className="size-3.5" /> {designs.length} designs
            </Button>
          )}
        </div>
      </div>
      <div className={cn("flex justify-center bg-surface-sunken p-3", view === "mobile" ? "max-h-[520px]" : "max-h-[420px]")}>
        {view === "designs" ? (
          <div className="grid w-full gap-3 sm:grid-cols-3">
            {designs.map(([name, path], i) => (
              <a
                key={name}
                href={base ? `${base}design/${name}/` : undefined}
                target="_blank"
                rel="noopener noreferrer"
                className="group block overflow-hidden rounded-md border bg-card shadow-soft"
              >
                {/* eslint-disable-next-line @next/next/no-img-element -- API-served screenshot */}
                <img src={fileUrl(path) ?? ""} alt={`Design ${i + 1}: ${titleCase(name)}`} className="aspect-[4/3] w-full object-cover object-top transition-transform group-hover:scale-[1.02]" />
                <span className="flex items-center justify-between px-2 py-1.5 text-xs">
                  <span className="font-medium">Design {i + 1} · {titleCase(name)}</span>
                  {name === variant && <span className="rounded bg-primary-soft px-1.5 text-[10px] font-semibold text-primary-soft-foreground">Main</span>}
                </span>
              </a>
            ))}
          </div>
        ) : img ? (
          // eslint-disable-next-line @next/next/no-img-element -- API-served screenshot
          <img src={img} alt={`Website preview, ${view}`} className={cn("rounded-md border object-cover object-top shadow-soft", view === "mobile" ? "w-[240px]" : "w-full")} />
        ) : (
          <p className="py-10 text-sm text-muted-foreground">No data yet</p>
        )}
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2 border-t px-3 py-2 text-xs">
        <div className="flex flex-wrap gap-2">
          {Object.keys(lighthouse).length === 0 ? (
            <span className="text-muted-foreground">Lighthouse: no data yet</span>
          ) : (
            Object.entries(lighthouse).map(([k, v]) => (
              <span key={k} className="tabular rounded-md bg-muted px-1.5 py-0.5">
                {titleCase(k)} <b>{Math.round(v)}</b>
              </span>
            ))
          )}
        </div>
        <div className="flex gap-2">
          {pdf && (
            <a href={pdf} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 font-medium text-primary hover:underline">
              <FileText className="size-3.5" /> PDF
            </a>
          )}
          {typeof preview.preview_url === "string" && preview.preview_url && (
            <a href={preview.preview_url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 font-medium text-primary hover:underline">
              Open preview <ExternalLink className="size-3.5" />
            </a>
          )}
        </div>
      </div>
    </div>
  );
}
