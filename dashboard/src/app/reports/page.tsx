"use client";

import { BarChart3, RefreshCw } from "lucide-react";
import { useState } from "react";
import { PageHeader } from "@/components/common/page-header";
import { Panel, PanelHeader } from "@/components/common/panel";
import { CardsSkeleton, EmptyState, ErrorState } from "@/components/common/states";
import { BreakdownChart, BreakdownTable, FunnelChart } from "@/components/reports/report-charts";
import { Button } from "@/components/ui/button";
import { dateTime, titleCase } from "@/lib/format";
import { type ReportData, useLiveReport, useRunReport, useSavedReports } from "@/lib/ops";
import { cn } from "@/lib/utils";

const DIMENSIONS: (keyof ReportData["by"])[] = ["source", "service", "industry", "country", "trigger", "template"];
const RANGES = [7, 30, 90];

export default function ReportsPage() {
  const [days, setDays] = useState(7);
  const [dim, setDim] = useState<keyof ReportData["by"]>("source");
  const live = useLiveReport(days);
  const saved = useSavedReports();
  const run = useRunReport();
  const data = live.data;
  const rows = data?.by[dim] ?? [];
  const empty = !data || data.funnel.discovered === 0;

  return (
    <>
      <PageHeader
        title="Weekly report"
        description="Which sources, services, industries, countries and message styles produce replies. The learning loop adjusts weights from these numbers — only with enough data, in small logged steps."
        actions={
          <Button variant="outline" size="sm" onClick={() => run.mutate()} disabled={run.isPending}>
            <RefreshCw className="size-3.5" /> Save report now
          </Button>
        }
      />
      <div className="mb-4 flex flex-wrap items-center gap-2" role="group" aria-label="Report filters">
        {RANGES.map((d) => (
          <Button key={d} size="sm" variant={days === d ? "secondary" : "ghost"} onClick={() => setDays(d)} aria-pressed={days === d}>
            Last {d} days
          </Button>
        ))}
        <span className="mx-1 h-5 w-px bg-border" aria-hidden />
        {DIMENSIONS.map((d) => (
          <Button key={d} size="sm" variant={dim === d ? "secondary" : "ghost"} onClick={() => setDim(d)} aria-pressed={dim === d}>
            {titleCase(d)}
          </Button>
        ))}
      </div>
      {live.isError ? (
        <ErrorState error={live.error} onRetry={() => live.refetch()} />
      ) : live.isLoading ? (
        <CardsSkeleton count={2} />
      ) : empty ? (
        <EmptyState icon={BarChart3} title="No data yet" description="The report fills in as leads are discovered, qualified and contacted." />
      ) : (
        <div className="space-y-6">
          <div className="grid gap-6 xl:grid-cols-2">
            <Panel>
              <PanelHeader title="Funnel" description={`${dateTime(data.period_start)} – ${dateTime(data.period_end)}`} />
              <div className="p-4">
                <FunnelChart funnel={data.funnel} />
                <p className="mt-2 text-xs text-muted-foreground">{data.note} Sandbox sends: {data.funnel.sandbox_sent}.</p>
              </div>
            </Panel>
            <Panel>
              <PanelHeader title={`Leads found by ${dim}`} description="Top 10. Full numbers and reply rates in the table below." />
              <div className="p-4">{rows.length ? <BreakdownChart rows={rows} dimension={dim} /> : <p className="text-sm text-muted-foreground">No data yet</p>}</div>
            </Panel>
          </div>
          <Panel>
            <PanelHeader title={`By ${dim}`} description="Reply rates use real sends only (Mailpit sandbox sends are listed separately)." />
            <BreakdownTable rows={rows} />
          </Panel>
        </div>
      )}
      <Panel className="mt-6">
        <PanelHeader title="Saved weekly reports & adjustments" description="Saved every Monday 06:30 UTC by the scheduler (or with “Save report now”)." />
        <div className="p-4 text-sm">
          {!saved.data?.length ? (
            <p className="text-muted-foreground">No data yet</p>
          ) : (
            <ul className="space-y-2">
              {saved.data.map((r) => (
                <li key={r.id} className="rounded-lg border p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-medium">{dateTime(r.period_start)} – {dateTime(r.period_end)}</span>
                    <span className="tabular text-xs text-muted-foreground">
                      {r.data.funnel.discovered} found · {r.data.funnel.sent} sent · {r.data.funnel.replied} replied
                    </span>
                  </div>
                  <ul className={cn("mt-2 space-y-1 text-xs", !r.adjustments.length && "text-muted-foreground")}>
                    {r.adjustments.length === 0 && <li>No adjustments (not enough data — needs ≥ 20 real sends per source/template).</li>}
                    {r.adjustments.map((a) => (
                      <li key={a.dimension + a.key}>
                        {titleCase(a.dimension)} <b>{a.key}</b>: weight {a.from} → {a.to} (reply rate {(a.reply_rate * 100).toFixed(1)}% vs {(a.baseline * 100).toFixed(1)}%, {a.sent} sent)
                      </li>
                    ))}
                  </ul>
                </li>
              ))}
            </ul>
          )}
        </div>
      </Panel>
    </>
  );
}
