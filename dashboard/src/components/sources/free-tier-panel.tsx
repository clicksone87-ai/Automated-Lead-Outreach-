"use client";

import { ExternalLink, Gauge } from "lucide-react";
import { Meter } from "@/components/common/meters";
import { Panel, PanelHeader } from "@/components/common/panel";
import { Skeleton } from "@/components/ui/skeleton";
import type { FreeTier } from "@/lib/api";
import { useFreeTier } from "@/lib/queries";

type Quota = FreeTier["quotas"][number];

const LABELS: Record<string, string> = {
  gemini: "Gemini requests today",
  serper: "Serper credits used",
  exa: "Exa free credit used",
  apify: "Apify this month",
  pagespeed: "PageSpeed requests today",
  serpapi: "SerpAPI this month",
  scrapingdog: "ScrapingDog this month",
  searlo: "Searlo this month",
  tavily: "Tavily this month",
  groq: "Groq requests today",
  zerobounce: "ZeroBounce this month",
  gmail: "Gmail emails today",
  "exa:monthly": "Exa requests this month",
  "apify:social_reddit": "Reddit (Apify share)",
  "apify:social_facebook": "Facebook (Apify share)",
  "apify:social_x": "X (Apify share)",
  "apify:social_instagram": "Instagram (Apify share)",
  "serper:daily": "Serper credits today (daily ceiling)",
  "gemini:scoring": "Gemini today: batch scoring",
  "gemini:fast": "Gemini today: fast-lane drafts",
  "gemini:heavy": "Gemini today: heavy lane (logos, previews)",
  "gemini:other": "Gemini today: replies and other",
};

function fmt(q: Quota, v: number) {
  return q.unit.startsWith("USD") ? `$${v.toFixed(v < 1 ? 3 : 2)}` : Math.round(v).toLocaleString();
}

function QuotaTile({ q, live }: { q: Quota; live?: string | null }) {
  return (
    <div className="space-y-1.5 rounded-lg border bg-surface-raised p-3">
      <div className="flex items-baseline justify-between gap-2 text-xs">
        <span className="font-medium">{LABELS[q.scope] ?? q.scope}</span>
        <span className="tabular text-muted-foreground">
          {fmt(q, q.used)} / {fmt(q, q.cap)}
        </span>
      </div>
      <Meter value={q.used} max={q.cap || 1} label={`${LABELS[q.scope] ?? q.scope} used`} />
      {live && <p className="text-[11px] text-muted-foreground">{live}</p>}
    </div>
  );
}

/** Free-tier usage vs each provider's own cap, plus live balances read from the providers. */
export function FreeTierPanel() {
  const { data, isLoading } = useFreeTier();
  const main = data?.quotas.filter((q) => !q.scope.includes(":")) ?? [];
  const daily = data?.quotas.filter((q) => q.scope === "serper:daily" || q.scope === "exa:monthly" || q.scope.startsWith("gemini:")) ?? [];
  const shares = data?.quotas.filter((q) => q.scope.startsWith("apify:")) ?? [];
  const liveFor = (scope: string): string | null => {
    if (!data) return null;
    if (scope === "serper" && data.serper_credits_live != null)
      return `Live balance: ${data.serper_credits_live.toLocaleString()} credits left`;
    if (scope === "apify" && data.apify_used_usd_live != null)
      return `Live: $${data.apify_used_usd_live.toFixed(3)} of $${(data.apify_limit_usd_live ?? 0).toFixed(0)} used this cycle`;
    return null;
  };

  return (
    <Panel className="mb-6">
      <PanelHeader
        title="Free-tier usage"
        description="Testing mode runs on free tiers only. Each provider is capped at its own free allowance; paid calls are blocked (budget $0)."
        action={
          data?.mailpit_messages != null ? (
            <a
              href={data.mailpit_url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline"
            >
              Mailpit · {data.mailpit_messages} emails <ExternalLink className="size-3" />
            </a>
          ) : (
            <Gauge className="size-4 text-muted-foreground" aria-hidden />
          )
        }
      />
      <div className="space-y-4 p-5">
        {isLoading || !data ? (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-16 rounded-lg" />
            ))}
          </div>
        ) : (
          <>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
              {main.map((q) => (
                <QuotaTile key={q.scope} q={q} live={liveFor(q.scope)} />
              ))}
            </div>
            {daily.length > 0 && (
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
                {daily.map((q) => (
                  <QuotaTile key={q.scope} q={q} />
                ))}
              </div>
            )}
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              {shares.map((q) => (
                <QuotaTile key={q.scope} q={q} />
              ))}
            </div>
          </>
        )}
      </div>
    </Panel>
  );
}
