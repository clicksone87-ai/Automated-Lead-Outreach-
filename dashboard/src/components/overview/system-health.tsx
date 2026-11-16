"use client";

import { Activity, AlertTriangle, CheckCircle2 } from "lucide-react";
import Link from "next/link";
import { Panel, PanelHeader } from "@/components/common/panel";
import { StatusPill } from "@/components/common/status-pill";
import { agoShort, titleCase } from "@/lib/format";
import { useMonitoring } from "@/lib/ops";

/** Monitoring (§5.03): heartbeats, agent failures, unhealthy sources, quotas near their cap. */
export function SystemHealth() {
  const { data } = useMonitoring();
  if (!data) return null;
  const hb = data.heartbeats;
  const failures = Object.entries(data.agent_failures_24h);
  return (
    <Panel>
      <PanelHeader
        title="System health"
        description="Worker and scheduler heartbeats, agent errors (24 h), source health and free-tier quotas."
        action={
          data.status === "ok" ? (
            <StatusPill tone="success"><CheckCircle2 className="size-3" aria-hidden /> All good</StatusPill>
          ) : (
            <StatusPill tone="warning"><AlertTriangle className="size-3" aria-hidden /> Needs attention</StatusPill>
          )
        }
      />
      <div className="grid gap-4 p-5 text-xs sm:grid-cols-2">
        <div className="space-y-1.5">
          {(["worker", "scheduler"] as const).map((k) => (
            <div key={k} className="flex items-center justify-between">
              <span className="flex items-center gap-1.5"><Activity className="size-3.5 text-muted-foreground" aria-hidden />{titleCase(k)}</span>
              <StatusPill tone={hb[k]?.alive ? "success" : "danger"}>{hb[k]?.alive ? `alive · ${agoShort(hb[k]?.last ?? null)}` : "no heartbeat"}</StatusPill>
            </div>
          ))}
          <div className="flex items-center justify-between text-muted-foreground">
            <span>Jobs queued</span>
            <span className="tabular">{hb.queue_depth ?? "—"}</span>
          </div>
        </div>
        <div className="space-y-1.5">
          <div className="font-medium">Agent errors (24 h)</div>
          {failures.length === 0 ? <div className="text-muted-foreground">None</div> : failures.map(([agent, n]) => (
            <div key={agent} className="flex justify-between"><span>{titleCase(agent)}</span><span className="tabular">{n}</span></div>
          ))}
          {data.sources_unhealthy.length > 0 && (
            <Link href="/sources" className="block text-warning hover:underline">
              {data.sources_unhealthy.length} source(s): {data.sources_unhealthy.map((s) => `${s.key} (${s.status})`).join(", ")}
            </Link>
          )}
          {data.quotas_over_80pct.map((q) => (
            <div key={q.scope} className="text-warning">Quota {q.scope}: {q.used} / {q.cap} {q.unit}</div>
          ))}
        </div>
      </div>
    </Panel>
  );
}
