"use client";

import { FlaskConical, Menu, Moon, Search, ShieldCheck, Sun } from "lucide-react";
import Link from "next/link";
import { useTheme } from "next-themes";
import { Meter } from "@/components/common/meters";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { usd } from "@/lib/format";
import { useLive } from "@/lib/live";
import { useHealth, useOverview } from "@/lib/queries";
import { cn } from "@/lib/utils";

const STAGE_COPY: Record<number, { label: string; hint: string }> = {
  1: { label: "Stage 1", hint: "Human approves every email before sending" },
  2: { label: "Stage 2", hint: "Sends automatically within low daily limits" },
  3: { label: "Stage 3", hint: "Full autonomy within budget caps" },
};

function HealthIndicator() {
  const { data: health, isError } = useHealth();
  const live = useLive();
  const ok = !isError && health?.status === "ok";
  const tone = isError ? "bg-destructive" : ok ? "bg-success" : "bg-warning";
  const label = isError
    ? "API unreachable"
    : !health
      ? "Checking…"
      : ok
        ? live.connected
          ? "All systems healthy · live"
          : "Healthy · reconnecting live feed"
        : `Degraded (db ${health.database ? "ok" : "down"}, redis ${health.redis ? "ok" : "down"})`;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <div className="flex h-8 items-center gap-2 rounded-lg border bg-surface-raised px-2.5 text-xs font-medium" role="status" aria-label={label}>
          <span className="relative flex size-2">
            {ok && live.connected && (
              <span className={cn("absolute inline-flex size-full animate-ping rounded-full opacity-60", tone)} />
            )}
            <span className={cn("relative inline-flex size-2 rounded-full", tone)} />
          </span>
          <span className="hidden md:inline">{isError ? "Offline" : ok ? "Healthy" : "Degraded"}</span>
        </div>
      </TooltipTrigger>
      <TooltipContent>
        <div className="space-y-0.5">
          <div>{label}</div>
          {health && health.integrations_missing > 0 && (
            <div className="text-muted-foreground">{health.integrations_missing} integrations need API keys</div>
          )}
          {health?.demo_mode && <div className="text-warning">Test fixtures active</div>}
        </div>
      </TooltipContent>
    </Tooltip>
  );
}

function TestingModeBadge() {
  const { data: health } = useHealth();
  if (!health?.testing_mode) return null;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Link
          href="/sources"
          className="flex h-8 items-center gap-1.5 rounded-lg bg-warning-soft px-2.5 text-xs font-semibold text-warning-soft-foreground ring-1 ring-inset ring-warning/30"
        >
          <FlaskConical className="size-3.5" aria-hidden />
          <span className="hidden sm:inline">Testing mode — free tier</span>
          <span className="sm:hidden">Testing</span>
        </Link>
      </TooltipTrigger>
      <TooltipContent>Live free-tier APIs only. Paid integrations are off and every email goes to Mailpit (nothing is really sent).</TooltipContent>
    </Tooltip>
  );
}

function BudgetIndicator() {
  const { data } = useOverview();
  const budget = data?.budget;
  const blocked = budget !== undefined && budget.monthly_cap === 0;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Link
          href="/settings#budgets"
          className="hidden h-8 min-w-[196px] flex-col justify-center gap-1 rounded-lg border bg-surface-raised px-2.5 lg:flex"
          aria-label="Budget used this month"
        >
          <div className="flex items-center justify-between gap-3 text-[11px] font-medium">
            <span className="text-muted-foreground">Budget (month)</span>
            <span className="tabular">
              {!budget ? "—" : blocked ? "Paid calls blocked" : `${usd(budget.spent_month)} / ${usd(budget.monthly_cap, 0)}`}
            </span>
          </div>
          <Meter value={budget?.spent_month ?? 0} max={budget?.monthly_cap ?? 1} label="Monthly budget used" />
        </Link>
      </TooltipTrigger>
      <TooltipContent>
        {blocked
          ? "Budget is $0: no paid call can run. Free tiers are tracked on the Sources page."
          : budget
          ? `Today ${usd(budget.spent_today)} of ${usd(budget.daily_cap)} daily cap${budget.paused ? " — paid agents paused" : ""}`
          : "Loading budget…"}
      </TooltipContent>
    </Tooltip>
  );
}

function AutonomyBadge() {
  const { data } = useOverview();
  const stage = data?.autonomy_stage ?? 1;
  const copy = STAGE_COPY[stage] ?? STAGE_COPY[1]!;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Link
          href="/settings#autonomy"
          className="flex h-8 items-center gap-1.5 rounded-lg bg-primary-soft px-2.5 text-xs font-semibold text-primary-soft-foreground ring-1 ring-inset ring-primary/20 transition-colors hover:bg-primary-soft/80"
        >
          <ShieldCheck className="size-3.5" aria-hidden />
          <span>Autonomy · {copy.label}</span>
        </Link>
      </TooltipTrigger>
      <TooltipContent>{copy.hint}</TooltipContent>
    </Tooltip>
  );
}

export function Topbar({ onOpenSearch, onOpenMobileNav }: { onOpenSearch: () => void; onOpenMobileNav: () => void }) {
  const { resolvedTheme, setTheme } = useTheme();
  return (
    <header className="sticky top-0 z-30 flex h-14 items-center gap-2 border-b bg-background/80 px-4 backdrop-blur-xl md:px-6">
      <Button variant="ghost" size="icon" className="md:hidden" onClick={onOpenMobileNav} aria-label="Open navigation">
        <Menu className="size-4" />
      </Button>
      <button
        type="button"
        onClick={onOpenSearch}
        className="group flex h-8 w-full max-w-sm items-center gap-2 rounded-lg border bg-surface-raised px-2.5 text-sm text-muted-foreground shadow-xs transition-colors hover:border-border-strong hover:text-foreground"
        aria-label="Search (Ctrl+K)"
      >
        <Search className="size-3.5" aria-hidden />
        <span className="truncate">Search leads, pages, actions…</span>
        <kbd className="ml-auto hidden rounded border bg-background px-1.5 font-mono text-[10px] sm:inline">⌘K</kbd>
      </button>
      <div className="ml-auto flex items-center gap-2">
        <TestingModeBadge />
        <AutonomyBadge />
        <BudgetIndicator />
        <HealthIndicator />
        <Button
          variant="ghost"
          size="icon"
          onClick={() => setTheme(resolvedTheme === "dark" ? "light" : "dark")}
          aria-label="Toggle colour theme"
        >
          <Sun className="size-4 scale-100 rotate-0 transition-all dark:scale-0 dark:-rotate-90" />
          <Moon className="absolute size-4 scale-0 rotate-90 transition-all dark:scale-100 dark:rotate-0" />
        </Button>
      </div>
    </header>
  );
}
