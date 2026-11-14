"use client";

import { motion } from "framer-motion";
import { AlertCircle, CheckCircle2, KeyRound, Loader2, PauseCircle } from "lucide-react";
import { StatusPill } from "@/components/common/status-pill";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { AgentStatus } from "@/lib/api";
import { agoShort, usd } from "@/lib/format";
import { AGENT_LABELS, AGENT_STATE } from "@/lib/status";
import { cn } from "@/lib/utils";

const STATE_ICON = {
  running: Loader2,
  idle: CheckCircle2,
  failed: AlertCircle,
  needs_api_key: KeyRound,
  paused_budget: PauseCircle,
} as const;

export function AgentCards({ agents }: { agents: AgentStatus[] }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
      {agents.map((agent, i) => {
        const state = AGENT_STATE[agent.state] ?? { label: agent.state, tone: "neutral" as const };
        const Icon = STATE_ICON[agent.state as keyof typeof STATE_ICON] ?? CheckCircle2;
        return (
          <motion.div
            key={agent.agent}
            initial={{ opacity: 0, scale: 0.98 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ delay: i * 0.03, duration: 0.25 }}
            className={cn(
              "flex flex-col gap-2 rounded-xl border bg-surface-raised p-3.5",
              agent.state === "failed" && "border-destructive/40",
              agent.state === "running" && "border-primary/40 shadow-glow",
            )}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="truncate text-sm font-medium">{AGENT_LABELS[agent.agent] ?? agent.agent}</span>
              <StatusPill tone={state.tone} dot={false}>
                <Icon className={cn("size-3", agent.state === "running" && "animate-spin")} aria-hidden />
                {state.label}
              </StatusPill>
            </div>
            <dl className="grid grid-cols-3 gap-2 text-[11px]">
              <div>
                <dt className="text-muted-foreground">Last run</dt>
                <dd className="truncate font-medium">{agoShort(agent.last_run_at)}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">24h runs</dt>
                <dd className="tabular font-medium">
                  {agent.runs_24h}
                  {agent.failures_24h > 0 && (
                    <span className="text-danger-soft-foreground"> · {agent.failures_24h} failed</span>
                  )}
                </dd>
              </div>
              <div>
                <dt className="text-muted-foreground">Cost today</dt>
                <dd className="tabular font-medium">{usd(agent.cost_today_usd)}</dd>
              </div>
            </dl>
            {agent.last_error && (
              <Tooltip>
                <TooltipTrigger asChild>
                  <p className="truncate rounded-md bg-danger-soft px-2 py-1 font-mono text-[11px] text-danger-soft-foreground">
                    {agent.last_error.split("\n").filter(Boolean).at(-1)}
                  </p>
                </TooltipTrigger>
                <TooltipContent className="max-w-md whitespace-pre-wrap font-mono text-[11px]">
                  {agent.last_error.slice(-800)}
                </TooltipContent>
              </Tooltip>
            )}
          </motion.div>
        );
      })}
    </div>
  );
}
