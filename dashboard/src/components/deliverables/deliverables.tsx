"use client";

import { ExternalLink, FileText, Monitor, Smartphone } from "lucide-react";
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
}

/** Logo concepts as rendered PNGs (the SVG source is downloadable, never inlined from storage). */
export function LogoGrid({ logos, compact = false }: { logos: LogoItem[]; compact?: boolean }) {
  return (
    <div className={cn("grid gap-3", compact ? "grid-cols-1" : "sm:grid-cols-2 xl:grid-cols-3")}>
      {logos.map((lg) => {
        const typo = (lg.typography ?? {}) as Record<string, unknown>;
        const sim = (lg.similarity ?? {}) as Record<string, unknown>;
        const name = String(lg.concept_name ?? typo.concept_name ?? `Concept ${String(lg.variant)}`);
        const png = fileUrl(lg.png_path as string | null);
        return (
          <figure key={String(lg.id ?? lg.variant)} className="overflow-hidden rounded-xl border bg-card">
            <div className="flex aspect-[2/1] items-center justify-center bg-[repeating-conic-gradient(var(--color-muted)_0_25%,transparent_0_50%)] bg-[length:16px_16px] p-3">
              {/* eslint-disable-next-line @next/next/no-img-element -- API-served artefact */}
              {png ? <img src={png} alt={`${name} logo concept`} className="max-h-full max-w-full object-contain" /> : null}
            </div>
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
            </figcaption>
          </figure>
        );
      })}
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
  const [view, setView] = useState<"desktop" | "mobile">("desktop");
  const shots = (preview.screenshots ?? {}) as Record<string, string | null>;
  const tests = (preview.test_results ?? {}) as Record<string, unknown>;
  const lighthouse = (preview.lighthouse ?? tests.lighthouse ?? {}) as Record<string, number>;
  const img = fileUrl(shots[view] ?? null);
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
        </div>
      </div>
      <div className={cn("flex justify-center bg-surface-sunken p-3", view === "mobile" ? "max-h-[520px]" : "max-h-[420px]")}>
        {img ? (
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
