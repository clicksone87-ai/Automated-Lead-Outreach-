"use client";

import { AlertTriangle } from "lucide-react";
import { useState } from "react";
import { Panel, PanelHeader } from "@/components/common/panel";
import { ErrorState, RowsSkeleton } from "@/components/common/states";
import { StatusPill } from "@/components/common/status-pill";
import { Button } from "@/components/ui/button";
import type { Schemas } from "@/lib/api";
import { ago } from "@/lib/format";
import { usePlatformReport } from "@/lib/queries";

type Stat = Schemas["PlatformStat"];
const RANGES = [1, 7, 30];

/** Daily leads as thin columns; each column has a hover/focus tooltip and an accessible label. */
function DailyColumns({ daily, label }: { daily: number[]; label: string }) {
  const max = Math.max(1, ...daily);
  const today = new Date();
  return (
    <div className="flex h-8 items-end gap-[2px]" role="img" aria-label={`${label} leads per day: ${daily.join(", ")}`}>
      {daily.map((n, i) => {
        const d = new Date(today);
        d.setDate(today.getDate() - (daily.length - 1 - i));
        const tip = `${d.toLocaleDateString(undefined, { month: "short", day: "numeric" })}: ${n} lead${n === 1 ? "" : "s"}`;
        return (
          <div key={i} className="group relative flex h-full flex-1 items-end" title={tip}>
            <div
              className="w-full rounded-t-[2px] bg-chart-1 transition-opacity group-hover:opacity-80"
              style={{ height: n ? `${Math.max(8, (n / max) * 100)}%` : "2px", opacity: n ? 1 : 0.25 }}
            />
          </div>
        );
      })}
    </div>
  );
}

function Num({ value, label }: { value: number; label: string }) {
  return (
    <div className="min-w-0">
      <p className="text-sm font-semibold tabular-nums">{value.toLocaleString()}</p>
      <p className="truncate text-[11px] text-muted-foreground">{label}</p>
    </div>
  );
}

function Row({ p, max, days, recentHours }: { p: Stat; max: number; days: number; recentHours: number }) {
  const share = max ? (p.leads / max) * 100 : 0;
  return (
    <li className="grid gap-3 px-5 py-4 md:grid-cols-[9rem_1fr_minmax(0,18rem)_7rem] md:items-center">
      <div className="flex items-center gap-2">
        <span className="font-medium">{p.label}</span>
        {!p.enabled && <StatusPill tone="neutral" dot={false}>off</StatusPill>}
        {p.failed_runs > 0 && (
          <span title={`${p.failed_runs} failed run(s)`} className="text-warning-soft-foreground">
            <AlertTriangle className="size-3.5" aria-label={`${p.failed_runs} failed runs`} />
          </span>
        )}
      </div>
      <div className="space-y-1">
        <div className="flex items-baseline justify-between gap-2">
          <span className="text-lg font-semibold tabular-nums">{p.leads.toLocaleString()}</span>
          <span className="text-xs text-muted-foreground">{p.leads_recent} in last {recentHours}h</span>
        </div>
        <div className="h-2 rounded-full bg-muted" aria-hidden>
          <div className="h-2 rounded-full bg-chart-1" style={{ width: `${share}%`, minWidth: p.leads ? 6 : 0 }} />
        </div>
      </div>
      <div className="grid grid-cols-4 gap-2">
        <Num value={p.qualified} label="qualified" />
        <Num value={p.high} label="high intent" />
        <Num value={p.sellers_filtered} label="sellers out" />
        <Num value={p.items_seen} label="posts seen" />
      </div>
      <div className="space-y-1">
        {days > 1 ? <DailyColumns daily={p.daily} label={p.label} /> : <div className="h-8" />}
        <p className="text-[11px] text-muted-foreground">
          {p.last_run_at ? `ran ${ago(p.last_run_at)}` : "never ran"} · {p.runs} runs
        </p>
      </div>
    </li>
  );
}

export function PlatformReport() {
  const [days, setDays] = useState(7);
  const { data, isLoading, isError, error, refetch } = usePlatformReport(days);
  const max = Math.max(0, ...(data?.platforms.map((p) => p.leads) ?? [0]));
  const total = data?.platforms.reduce((a, p) => a + p.leads, 0) ?? 0;

  return (
    <Panel className="mb-6">
      <PanelHeader
        title="Leads per social platform"
        description={data ? `${total.toLocaleString()} social leads in the last ${days === 1 ? "24 hours" : `${days} days`}. Only posts from the last ${data.recent_hours} hours are collected; sellers are scored out.` : "Every social platform side by side."}
        action={
          <div className="flex gap-1" role="group" aria-label="Time range">
            {RANGES.map((d) => (
              <Button key={d} size="sm" variant={days === d ? "secondary" : "ghost"} onClick={() => setDays(d)} aria-pressed={days === d}>
                {d === 1 ? "24h" : `${d}d`}
              </Button>
            ))}
          </div>
        }
      />
      {isError ? (
        <ErrorState error={error} onRetry={() => refetch()} />
      ) : isLoading || !data ? (
        <div className="p-4">
          <RowsSkeleton rows={4} />
        </div>
      ) : (
        <ul className="divide-y">
          {data.platforms.map((p) => (
            <Row key={p.platform} p={p} max={max} days={days} recentHours={data.recent_hours} />
          ))}
        </ul>
      )}
    </Panel>
  );
}
