"use client";

import { motion } from "framer-motion";
import { FileSearch } from "lucide-react";
import Link from "next/link";
import { PageHeader } from "@/components/common/page-header";
import { CardsSkeleton, EmptyState, ErrorState } from "@/components/common/states";
import { StatusPill } from "@/components/common/status-pill";
import { fileUrl } from "@/lib/api";
import { agoShort } from "@/lib/format";
import { useAudits } from "@/lib/queries";
import { cn } from "@/lib/utils";

function scoreTone(v: number | null | undefined) {
  if (v == null) return "text-muted-foreground";
  return v >= 90 ? "text-success" : v >= 50 ? "text-warning-soft-foreground" : "text-destructive";
}

export default function AuditsPage() {
  const { data, isLoading, isError, error, refetch } = useAudits(1);
  return (
    <>
      <PageHeader
        title="Audits"
        description="Website & SEO audits. Every finding links to a stored screenshot, page capture or measurement."
        actions={data && <span className="tabular text-sm text-muted-foreground">{data.total} audits</span>}
      />
      {isError ? (
        <ErrorState error={error} onRetry={() => refetch()} />
      ) : isLoading || !data ? (
        <CardsSkeleton count={6} className="xl:grid-cols-3" />
      ) : data.items.length === 0 ? (
        <EmptyState
          icon={FileSearch}
          title="No audits yet"
          description="Audits run automatically for qualified leads that have a website and a verified contact route."
        />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {data.items.map((a, i) => (
            <motion.div key={a.lead_id} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.03 }}>
              <Link
                href={`/audits/${a.lead_id}`}
                className="group flex h-full flex-col overflow-hidden rounded-xl border bg-card shadow-soft transition-all hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-elevated"
              >
                <div className="relative aspect-[16/10] overflow-hidden border-b bg-surface-sunken">
                  {a.desktop_path ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={fileUrl(a.desktop_path) ?? ""} alt="" className="size-full object-cover object-top transition-transform duration-500 group-hover:scale-[1.02]" />
                  ) : (
                    <div className="flex size-full items-center justify-center text-xs text-muted-foreground">No screenshot</div>
                  )}
                </div>
                <div className="flex flex-1 flex-col gap-3 p-4">
                  <div className="min-w-0">
                    <p className="truncate font-medium">{a.business_name ?? a.url}</p>
                    <p className="truncate text-xs text-muted-foreground">{a.url}</p>
                  </div>
                  <div className="grid grid-cols-4 gap-2 text-center">
                    {(["performance", "seo", "security", "design"] as const).map((k) => (
                      <div key={k} className="rounded-lg bg-muted/60 py-1.5">
                        <div className={cn("tabular text-sm font-semibold", scoreTone(a.scores[k]))}>{a.scores[k] == null ? "—" : Math.round(a.scores[k]!)}</div>
                        <div className="text-[10px] uppercase tracking-wide text-muted-foreground">{k === "seo" ? "SEO" : k.slice(0, 5)}</div>
                      </div>
                    ))}
                  </div>
                  <div className="mt-auto flex items-center justify-between gap-2">
                    <div className="flex gap-1">
                      {a.severity_counts.critical ? <StatusPill tone="danger" dot={false}>{a.severity_counts.critical} critical</StatusPill> : null}
                      {a.severity_counts.high ? <StatusPill tone="warning" dot={false}>{a.severity_counts.high} high</StatusPill> : null}
                    </div>
                    <span className="text-[11px] text-muted-foreground">{agoShort(a.created_at)}</span>
                  </div>
                </div>
              </Link>
            </motion.div>
          ))}
        </div>
      )}
    </>
  );
}
