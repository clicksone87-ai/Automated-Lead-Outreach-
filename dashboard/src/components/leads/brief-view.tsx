"use client";

import { ArrowRight, Info, Quote } from "lucide-react";
import Link from "next/link";
import { Gauge } from "@/components/common/meters";
import { StatusPill } from "@/components/common/status-pill";
import { titleCase } from "@/lib/format";
import { AGENT_LABELS, SERVICES } from "@/lib/status";
import { cn } from "@/lib/utils";

type Brief = {
  services?: string[];
  business_type?: string | null;
  location?: string | null;
  recipient_type?: string;
  budget?: string | null;
  timeline?: string | null;
  available_info?: string[];
  evidence_quotes?: string[];
  assumptions?: string[];
  agents_to_run?: string[];
  used_llm?: boolean;
  intent?: { llm_score: number | null; rule_score: number; combined: number; signals: Record<string, number> };
};

const SIGNAL_LABELS: Record<string, string> = {
  budget_mentioned: "Budget mentioned",
  timeline_mentioned: "Timeline mentioned",
  request_phrase: "Request language",
  recent_7d: "Posted this week",
  stale_30d: "Older than 30 days",
  seller_not_buyer: "Looks like a seller",
  diy_question: "DIY question",
  business_identified: "Business identified",
};

export function BriefView({ brief }: { brief: Record<string, unknown> }) {
  const b = brief as Brief;
  const intent = b.intent;
  return (
    <div className="space-y-5">
      {intent && (
        <div className="flex flex-wrap items-center gap-6 rounded-xl border bg-surface-sunken/50 p-4">
          <Gauge value={intent.combined} label="Intent" size={92} />
          <div className="min-w-[220px] max-w-md flex-1 space-y-1.5">
            <p className="text-xs text-muted-foreground">
              {b.used_llm ? `LLM ${intent.llm_score ?? "—"} · rules ${intent.rule_score}` : `Rule-based score ${intent.rule_score} (no LLM cost)`}
            </p>
            <ul className="space-y-1">
              {Object.entries(intent.signals).map(([key, value]) => (
                <li key={key} className="flex items-center justify-between gap-3 text-xs">
                  <span>{key.startsWith("base:") ? `Base: ${titleCase(key.slice(5))}` : SIGNAL_LABELS[key] ?? titleCase(key)}</span>
                  <span className={cn("tabular font-semibold", value < 0 ? "text-destructive" : "text-success")}>
                    {value > 0 ? "+" : ""}
                    {value}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}

      <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3">
        {[
          ["Services", (b.services ?? []).map((s) => SERVICES[s] ?? s).join(", ") || "—"],
          ["Business type", b.business_type ?? "—"],
          ["Location", b.location ?? "—"],
          ["Recipient", titleCase(b.recipient_type ?? "unknown")],
          ["Budget", b.budget ?? "Not stated"],
          ["Timeline", b.timeline ?? "Not stated"],
        ].map(([label, value]) => (
          <div key={label}>
            <dt className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{label}</dt>
            <dd className="mt-0.5 text-sm">{value}</dd>
          </div>
        ))}
      </dl>

      {(b.evidence_quotes?.length ?? 0) > 0 && (
        <div className="space-y-2">
          <p className="text-xs font-medium text-muted-foreground">Verified quotes from the source</p>
          {b.evidence_quotes!.map((q) => (
            <blockquote key={q} className="flex gap-2 rounded-lg border-l-2 border-primary bg-primary-soft/40 px-3 py-2 text-sm">
              <Quote className="mt-0.5 size-3.5 shrink-0 text-primary" aria-hidden /> {q}
            </blockquote>
          ))}
        </div>
      )}

      {(b.assumptions?.length ?? 0) > 0 && (
        <ul className="space-y-1 rounded-lg bg-warning-soft px-3 py-2 text-xs text-warning-soft-foreground">
          {b.assumptions!.map((a) => (
            <li key={a} className="flex gap-1.5"><Info className="mt-0.5 size-3 shrink-0" aria-hidden /> {a}</li>
          ))}
        </ul>
      )}

      {(b.agents_to_run?.length ?? 0) > 0 && (
        <div>
          <p className="mb-2 text-xs font-medium text-muted-foreground">Planned steps</p>
          <div className="flex flex-wrap items-center gap-1.5">
            {b.agents_to_run!.map((a, i) => (
              <span key={a} className="flex items-center gap-1.5">
                <StatusPill tone="accent" dot={false}>{AGENT_LABELS[a] ?? a}</StatusPill>
                {i < b.agents_to_run!.length - 1 && <ArrowRight className="size-3 text-muted-foreground" aria-hidden />}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export function AuditSummaryCard({ audit, leadId }: { audit: Record<string, unknown>; leadId: string }) {
  const scores = (audit.scores ?? {}) as Record<string, number | null>;
  const counts = (audit.severity_counts ?? {}) as Record<string, number>;
  return (
    <div className="space-y-3 p-4">
      <div className="grid grid-cols-3 gap-2">
        {(["performance", "seo", "design"] as const).map((k) => (
          <Gauge key={k} value={scores[k] ?? null} label={k === "seo" ? "SEO" : titleCase(k)} size={64} stroke={6} />
        ))}
      </div>
      <div className="flex flex-wrap gap-1">
        {counts.critical ? <StatusPill tone="danger" dot={false}>{counts.critical} critical</StatusPill> : null}
        {counts.high ? <StatusPill tone="warning" dot={false}>{counts.high} high</StatusPill> : null}
        {counts.medium ? <StatusPill tone="info" dot={false}>{counts.medium} medium</StatusPill> : null}
      </div>
      <Link href={`/audits/${leadId}`} className="inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline">
        Open full audit <ArrowRight className="size-3" />
      </Link>
    </div>
  );
}
