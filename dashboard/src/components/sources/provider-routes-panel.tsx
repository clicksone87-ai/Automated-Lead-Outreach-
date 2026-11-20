"use client";

import { Route } from "lucide-react";
import { Meter } from "@/components/common/meters";
import { Panel, PanelHeader } from "@/components/common/panel";
import { Skeleton } from "@/components/ui/skeleton";
import type { FreeTier } from "@/lib/api";
import { useFreeTier } from "@/lib/queries";
import { cn } from "@/lib/utils";

type ProviderRoute = NonNullable<FreeTier["routes"]>[number];

const NAMES: Record<string, string> = {
  serper: "Serper",
  serpapi: "SerpAPI",
  scrapingdog: "ScrapingDog",
  searlo: "Searlo",
  tavily: "Tavily",
  exa: "Exa",
  brave: "Brave",
  gemini: "Gemini",
  groq: "Groq",
};

function Usage({
  label,
  used,
  cap,
}: {
  label: string;
  used: number;
  cap?: number | null;
}) {
  if (cap == null) return null;
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-[11px] text-muted-foreground">
        <span>{label}</span>
        <span className="tabular">
          {Math.round(used).toLocaleString()} /{" "}
          {Math.round(cap).toLocaleString()}
        </span>
      </div>
      <Meter value={used} max={cap || 1} label={`${label} used`} />
    </div>
  );
}

function state(r: ProviderRoute): { text: string; tone: string } {
  if (!r.configured) return { text: "No key", tone: "text-muted-foreground" };
  if (r.exhausted)
    return { text: "Cap reached", tone: "text-warning-soft-foreground" };
  if (r.active) return { text: "Active", tone: "text-success" };
  return { text: "Standby", tone: "text-muted-foreground" };
}

function RouteRow({ r }: { r: ProviderRoute }) {
  const s = state(r);
  return (
    <li
      className={cn(
        "space-y-2 rounded-lg border bg-surface-raised p-3",
        r.active && "border-primary/50",
      )}
    >
      <div className="flex items-baseline justify-between gap-2 text-xs">
        <span className="font-medium">
          <span className="tabular text-muted-foreground">{r.position}.</span>{" "}
          {NAMES[r.provider] ?? r.provider}
        </span>
        <span className={cn("font-medium", s.tone)}>{s.text}</span>
      </div>
      <Usage label="Today" used={r.today_used} cap={r.today_cap} />
      <Usage label="This month" used={r.month_used} cap={r.month_cap} />
      {r.last_error && (
        <p
          className="break-words text-[11px] text-muted-foreground"
          title={r.last_error}
        >
          Last error
          {r.last_error_at
            ? ` (${new Date(r.last_error_at).toLocaleString()})`
            : ""}
          : {r.last_error.slice(0, 140)}
        </p>
      )}
    </li>
  );
}

/** Search router and LLM fallback order: which provider is serving now, usage vs cap, last error. */
export function ProviderRoutesPanel() {
  const { data, isLoading } = useFreeTier();
  const routes = data?.routes ?? [];
  const groups = [
    { key: "search", title: "Web search router" },
    { key: "llm", title: "LLM (Gemini, then Groq for text)" },
  ];

  return (
    <Panel className="mb-6">
      <PanelHeader
        title="Provider fallback"
        description="Providers are tried in this order. When one reaches its free cap or fails, the next one takes over."
        action={<Route className="size-4 text-muted-foreground" aria-hidden />}
      />
      <div className="space-y-5 p-5">
        {isLoading || !data ? (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-20 rounded-lg" />
            ))}
          </div>
        ) : (
          groups.map((g) => {
            const rows = routes.filter((r) => r.group === g.key);
            if (rows.length === 0) return null;
            return (
              <section key={g.key} aria-label={g.title} className="space-y-2">
                <h3 className="text-xs font-semibold text-muted-foreground">
                  {g.title}
                </h3>
                <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                  {rows.map((r) => (
                    <RouteRow key={r.provider} r={r} />
                  ))}
                </ul>
              </section>
            );
          })
        )}
      </div>
    </Panel>
  );
}
