"use client";

import { CheckCircle2, Clock3, TriangleAlert } from "lucide-react";
import { Panel, PanelHeader } from "@/components/common/panel";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { Schemas } from "@/lib/api";
import { useTimings } from "@/lib/queries";
import { AGENT_LABELS } from "@/lib/status";
import { cn } from "@/lib/utils";

/** 42 s · 3 min 05 s · 1 h 12 min */
export function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  const s = Math.round(seconds);
  if (s < 60) return `${s} s`;
  if (s < 3600) {
    const m = Math.floor(s / 60);
    const rest = s % 60;
    return rest ? `${m} min ${String(rest).padStart(2, "0")} s` : `${m} min`;
  }
  const h = Math.floor(s / 3600);
  return `${h} h ${Math.round((s % 3600) / 60)} min`;
}

const LANE_NAMES: Record<string, { label: string; hint: string }> = {
  fast: { label: "Fast lane", hint: "Every lead: prefilter, batch scoring, contact, light audit, draft." },
  heavy: { label: "Heavy lane", hint: "Top leads: full audit, logos, website preview." },
};

function LaneTile({ lane }: { lane: Schemas["LaneTiming"] }) {
  const meta = LANE_NAMES[lane.lane] ?? { label: lane.lane, hint: "" };
  const empty = lane.leads === 0;
  const ok = lane.within_target;
  return (
    <div className="rounded-lg border p-4">
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs font-medium text-muted-foreground">{meta.label}</p>
        {empty ? (
          <span className="inline-flex items-center gap-1 text-[11px] text-muted-foreground">
            <Clock3 className="size-3" aria-hidden /> No runs yet
          </span>
        ) : ok ? (
          <span className="inline-flex items-center gap-1 text-[11px] font-medium text-success">
            <CheckCircle2 className="size-3" aria-hidden /> Within target
          </span>
        ) : (
          <span className="inline-flex items-center gap-1 text-[11px] font-medium text-warning">
            <TriangleAlert className="size-3" aria-hidden /> Over target
          </span>
        )}
      </div>
      <p className="mt-2 text-2xl font-semibold tabular">{duration(lane.p50_s)}</p>
      <p className="mt-1 text-xs text-muted-foreground">
        median · p90 {duration(lane.p90_s)} · target {duration(lane.target_s)} · {lane.leads} lead{lane.leads === 1 ? "" : "s"}
      </p>
      <p className="mt-2 text-[11px] text-muted-foreground">{meta.hint}</p>
    </div>
  );
}

function StepBars({ steps }: { steps: Schemas["StepTiming"][] }) {
  const max = Math.max(1, ...steps.map((s) => s.p90_s));
  return (
    <table className="w-full text-xs">
      <caption className="sr-only">Median and 90th percentile time per pipeline step</caption>
      <thead>
        <tr className="text-left text-muted-foreground">
          <th scope="col" className="pb-2 font-medium">Step</th>
          <th scope="col" className="w-[45%] pb-2 font-medium">
            <span className="sr-only">Median time bar</span>
          </th>
          <th scope="col" className="pb-2 text-right font-medium">Median</th>
          <th scope="col" className="pb-2 text-right font-medium">p90</th>
          <th scope="col" className="pb-2 text-right font-medium">Runs</th>
        </tr>
      </thead>
      <tbody>
        {steps.map((s) => (
          <tr key={s.agent} className="border-t border-border/60">
            <th scope="row" className="py-1.5 pr-3 text-left font-medium">{AGENT_LABELS[s.agent] ?? s.agent}</th>
            <td className="py-1.5 pr-3">
              <Tooltip>
                <TooltipTrigger asChild>
                  <div className="relative h-4 cursor-default" aria-hidden>
                    <div className="absolute inset-y-[5px] left-0 right-0 rounded-full bg-muted" />
                    <div
                      className="absolute inset-y-[5px] left-0 rounded-full bg-primary/30"
                      style={{ width: `${Math.max(1, (s.p90_s / max) * 100)}%` }}
                    />
                    <div
                      className="absolute inset-y-[5px] left-0 rounded-full bg-primary"
                      style={{ width: `${Math.max(1, (s.p50_s / max) * 100)}%` }}
                    />
                  </div>
                </TooltipTrigger>
                <TooltipContent>
                  {AGENT_LABELS[s.agent] ?? s.agent}: median {duration(s.p50_s)}, p90 {duration(s.p90_s)}, slowest{" "}
                  {duration(s.max_s)} ({s.runs} runs)
                </TooltipContent>
              </Tooltip>
            </td>
            <td className="py-1.5 text-right tabular">{duration(s.p50_s)}</td>
            <td className="py-1.5 text-right tabular text-muted-foreground">{duration(s.p90_s)}</td>
            <td className="py-1.5 text-right tabular text-muted-foreground">{s.runs}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function LaneSpeed() {
  const timings = useTimings(7);
  const t = timings.data;
  return (
    <Panel>
      <PanelHeader
        title="Speed"
        description="How long leads take, last 7 days. Dark bar: median; light bar: 90th percentile."
        action={
          t && t.to_proposal_leads > 0 ? (
            <span className="tabular text-xs text-muted-foreground">
              first step → draft: {duration(t.to_proposal_p50_s)} median
            </span>
          ) : null
        }
      />
      <div className="space-y-5 p-5">
        {!t ? (
          <Skeleton className="h-40 w-full" />
        ) : (
          <>
            <div className={cn("grid gap-4 sm:grid-cols-2")}>
              {t.lanes.map((lane) => (
                <LaneTile key={lane.lane} lane={lane} />
              ))}
            </div>
            {t.steps.length ? (
              <StepBars steps={t.steps} />
            ) : (
              <p className="text-xs text-muted-foreground">No finished steps in the last 7 days.</p>
            )}
          </>
        )}
      </div>
    </Panel>
  );
}
