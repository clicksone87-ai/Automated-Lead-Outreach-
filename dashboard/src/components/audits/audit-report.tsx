"use client";

import { AnimatePresence, motion } from "framer-motion";
import { ChevronDown, Download, ExternalLink, FileText, Monitor, Smartphone } from "lucide-react";
import { useState } from "react";
import { Gauge } from "@/components/common/meters";
import { Panel, PanelHeader } from "@/components/common/panel";
import { StatusPill } from "@/components/common/status-pill";
import { Button } from "@/components/ui/button";
import { fileUrl, type AuditDetail } from "@/lib/api";
import { dateTime, titleCase } from "@/lib/format";
import type { Tone } from "@/lib/status";
import { cn } from "@/lib/utils";

const SCORE_LABELS: Record<string, string> = {
  performance: "Performance",
  accessibility: "Accessibility",
  "best-practices": "Best practices",
  seo: "SEO",
  security: "Security",
  design: "Design",
};
const SEVERITY: { key: string; label: string; tone: Tone }[] = [
  { key: "critical", label: "Critical", tone: "danger" },
  { key: "high", label: "High", tone: "warning" },
  { key: "medium", label: "Medium", tone: "info" },
  { key: "low", label: "Low", tone: "neutral" },
];

type Finding = {
  id: string;
  category: string;
  severity: string;
  title: string;
  detail: string;
  evidence_id: string;
  metric?: Record<string, unknown>;
};

function EvidenceLink({ audit, id }: { audit: AuditDetail; id: string }) {
  const ref = audit.evidence[id];
  if (!ref) return <span className="text-[11px] text-destructive">evidence missing</span>;
  const href = ref.file_path ? fileUrl(ref.file_path) : ref.url;
  const label = ref.kind === "screenshot" ? "Screenshot" : ref.kind === "metric" ? "Measurement" : "Page capture";
  return href && /^https?:\/\//.test(href) ? (
    <a href={href} target="_blank" rel="noopener noreferrer nofollow" className="inline-flex items-center gap-1 text-[11px] font-medium text-primary hover:underline">
      <FileText className="size-3" aria-hidden /> {label}
    </a>
  ) : (
    <span className="inline-flex items-center gap-1 text-[11px] text-muted-foreground">
      <FileText className="size-3" aria-hidden /> {label}
    </span>
  );
}

function SeverityGroup({ audit, severity, findings }: { audit: AuditDetail; severity: (typeof SEVERITY)[number]; findings: Finding[] }) {
  const [open, setOpen] = useState(severity.key !== "low");
  if (!findings.length) return null;
  return (
    <div className="rounded-xl border">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left"
        aria-expanded={open}
      >
        <span className="flex items-center gap-2">
          <StatusPill tone={severity.tone}>{severity.label}</StatusPill>
          <span className="text-sm font-medium">{findings.length} finding{findings.length === 1 ? "" : "s"}</span>
        </span>
        <ChevronDown className={cn("size-4 text-muted-foreground transition-transform", open && "rotate-180")} aria-hidden />
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.ul
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="divide-y overflow-hidden border-t"
          >
            {findings.map((f) => (
              <li key={f.id} className="flex flex-col gap-1 px-4 py-3 sm:flex-row sm:items-start sm:justify-between sm:gap-6">
                <div className="min-w-0">
                  <p className="text-sm font-medium">{f.title}</p>
                  <p className="text-xs text-muted-foreground">{f.detail}</p>
                </div>
                <div className="flex shrink-0 items-center gap-3">
                  <span className="rounded-md bg-muted px-1.5 py-0.5 text-[11px] font-medium text-muted-foreground">
                    {f.category === "seo" ? "SEO" : titleCase(f.category)}
                  </span>
                  <EvidenceLink audit={audit} id={f.evidence_id} />
                </div>
              </li>
            ))}
          </motion.ul>
        )}
      </AnimatePresence>
    </div>
  );
}

export function AuditReport({ audit }: { audit: AuditDetail }) {
  const [view, setView] = useState<"desktop" | "mobile">("desktop");
  const findings = audit.findings as unknown as Finding[];
  const shot = view === "desktop" ? audit.desktop_path : audit.mobile_path;
  const competitors = audit.competitor_comparison as Record<string, unknown>[];

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h2 className="truncate text-2xl font-semibold tracking-tight">{audit.business_name ?? "Website audit"}</h2>
          <a href={audit.url} target="_blank" rel="noopener noreferrer nofollow" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-primary">
            {audit.url} <ExternalLink className="size-3" aria-hidden />
          </a>
          <p className="text-xs text-muted-foreground">Audited {dateTime(audit.created_at)}</p>
        </div>
        {audit.report_pdf_path && (
          <Button asChild variant="outline" size="sm">
            <a href={fileUrl(audit.report_pdf_path) ?? "#"} target="_blank" rel="noopener noreferrer">
              <Download className="size-3.5" /> Client PDF
            </a>
          </Button>
        )}
      </div>

      <Panel className="p-5">
        <div className="grid grid-cols-3 gap-4 sm:grid-cols-6">
          {Object.keys(SCORE_LABELS).map((key) => (
            <Gauge key={key} value={audit.scores[key] ?? null} label={SCORE_LABELS[key]} size={84} />
          ))}
        </div>
        {audit.before_after_summary && (
          <p className="mt-5 rounded-lg bg-primary-soft/60 px-4 py-3 text-sm leading-relaxed text-primary-soft-foreground">
            {audit.before_after_summary}
          </p>
        )}
      </Panel>

      <div className="grid gap-6 xl:grid-cols-[1fr_380px]">
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold">Findings</h3>
            <div className="flex gap-1.5">
              {SEVERITY.map((s) => (
                <StatusPill key={s.key} tone={s.tone} dot={false}>
                  {s.label} <span className="tabular font-semibold">{audit.severity_counts[s.key] ?? 0}</span>
                </StatusPill>
              ))}
            </div>
          </div>
          {findings.length === 0 ? (
            <p className="rounded-xl border border-dashed p-6 text-center text-sm text-muted-foreground">No findings — this site is in good shape.</p>
          ) : (
            SEVERITY.map((s) => (
              <SeverityGroup key={s.key} audit={audit} severity={s} findings={findings.filter((f) => f.severity === s.key)} />
            ))
          )}
        </div>

        <div className="space-y-6">
          <Panel>
            <PanelHeader
              title="How it looks today"
              action={
                <div className="flex rounded-lg border p-0.5" role="tablist" aria-label="Screenshot view">
                  {(["desktop", "mobile"] as const).map((v) => (
                    <button
                      key={v}
                      type="button"
                      role="tab"
                      aria-selected={view === v}
                      onClick={() => setView(v)}
                      className={cn("flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium", view === v ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground")}
                    >
                      {v === "desktop" ? <Monitor className="size-3" /> : <Smartphone className="size-3" />} {titleCase(v)}
                    </button>
                  ))}
                </div>
              }
            />
            <div className="flex justify-center bg-surface-sunken/60 p-4">
              {shot ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={fileUrl(shot) ?? ""}
                  alt={`${titleCase(view)} screenshot of ${audit.url}`}
                  className={cn("rounded-lg border bg-white shadow-soft", view === "mobile" ? "max-h-[560px]" : "w-full")}
                />
              ) : (
                <p className="py-10 text-sm text-muted-foreground">No screenshot captured.</p>
              )}
            </div>
          </Panel>

          {competitors.length > 0 && (
            <Panel>
              <PanelHeader title="Local competitors" description="Quick checks on the top local results." />
              <table className="w-full text-xs">
                <thead className="text-muted-foreground">
                  <tr className="border-b">
                    <th className="px-4 py-2 text-left font-medium">Site</th>
                    {["HTTPS", "Mobile", "CTA", "Schema"].map((h) => (
                      <th key={h} className="px-2 py-2 text-center font-medium">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {competitors.map((c) => (
                    <tr key={String(c.domain)} className="border-b last:border-0">
                      <td className="truncate px-4 py-2 font-medium">{String(c.domain)}</td>
                      {(["https", "mobile_ready", "has_cta", "has_structured_data"] as const).map((k) => (
                        <td key={k} className="px-2 py-2 text-center">{c[k] ? "✓" : "—"}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </Panel>
          )}
        </div>
      </div>
    </div>
  );
}
