"use client";

import { Activity as ActivityIcon, AlertOctagon, Inbox, MailCheck, MailWarning, Sparkles, Wallet } from "lucide-react";
import Link from "next/link";
import { Meter } from "@/components/common/meters";
import { PageHeader } from "@/components/common/page-header";
import { Panel, PanelHeader } from "@/components/common/panel";
import { StatCard } from "@/components/common/stat-card";
import { CardsSkeleton, ErrorState, RowsSkeleton } from "@/components/common/states";
import { StatusPill } from "@/components/common/status-pill";
import { ActivityFeed } from "@/components/overview/activity-feed";
import { AgentCards } from "@/components/overview/agent-cards";
import { CostChart } from "@/components/overview/cost-chart";
import { PipelineFlow } from "@/components/overview/pipeline-flow";
import { Skeleton } from "@/components/ui/skeleton";
import { pct, usd } from "@/lib/format";
import { useLive } from "@/lib/live";
import { useActivity, useCosts, useOverview } from "@/lib/queries";
import { leadStatus } from "@/lib/status";

const BOUNCE_LIMIT = 0.03;
const COMPLAINT_LIMIT = 0.001;

export default function OverviewPage() {
  const overview = useOverview();
  const costs = useCosts(30);
  const activity = useActivity();
  const live = useLive();
  const o = overview.data;

  return (
    <>
      <PageHeader
        title="Overview"
        description="Where every lead is right now, what the agents are doing, and what it's costing."
        actions={
          <StatusPill tone={live.connected ? "success" : "neutral"} pulse={live.connected}>
            {live.connected ? "Live" : "Polling"}
          </StatusPill>
        }
      />

      {overview.isError ? (
        <ErrorState error={overview.error} onRetry={() => overview.refetch()} />
      ) : (
        <div className="space-y-6">
          {!o ? (
            <CardsSkeleton />
          ) : (
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
              <StatCard index={0} label="New leads today" value={o.leads_today} hint={`${o.leads_7d} in the last 7 days`} icon={Sparkles} />
              <StatCard
                index={1}
                label="Awaiting approval"
                value={o.awaiting_approval}
                hint={o.autonomy_stage === 1 ? "Stage 1: every email needs your approval" : "Auto-sending within limits"}
                icon={MailCheck}
              />
              <StatCard
                index={2}
                label="Replies this week"
                value={o.deliverability.replies_7d}
                hint={`${o.deliverability.positive_replies_7d} positive · ${o.inbox_count} in inbox`}
                icon={Inbox}
              />
              <StatCard
                index={3}
                label="Spend today"
                value={usd(o.budget.spent_today)}
                hint={o.budget.paused ? "Cap reached — paid agents paused" : `of ${usd(o.budget.daily_cap)} daily cap`}
                icon={Wallet}
                footer={<Meter value={o.budget.spent_today} max={o.budget.daily_cap} label="Daily budget used" />}
              />
            </div>
          )}

          <Panel>
            <PanelHeader
              title="Pipeline"
              description="Leads currently at each stage — click a stage to see its leads."
              action={
                o && Object.keys(o.stopped).length > 0 ? (
                  <Link href="/leads?stage=stopped" className="text-xs font-medium text-primary hover:underline">
                    {Object.values(o.stopped).reduce((a, b) => a + b, 0)} stopped
                  </Link>
                ) : null
              }
            />
            <div className="p-5">
              {!o ? <Skeleton className="h-28 w-full rounded-xl" /> : <PipelineFlow stages={o.stages} />}
              {o && Object.keys(o.stopped).length > 0 && (
                <div className="mt-4 flex flex-wrap gap-2">
                  {Object.entries(o.stopped).map(([status, count]) => {
                    const meta = leadStatus(status);
                    return (
                      <Link key={status} href={`/leads?status=${status}`}>
                        <StatusPill tone={meta.tone}>
                          {meta.label} <span className="tabular font-semibold">{count}</span>
                        </StatusPill>
                      </Link>
                    );
                  })}
                </div>
              )}
            </div>
          </Panel>

          <div className="grid gap-6 xl:grid-cols-3">
            <Panel className="xl:col-span-2">
              <PanelHeader title="Agents" description="Status, last run and today's cost per agent." />
              <div className="p-5">{!o ? <CardsSkeleton count={6} className="xl:grid-cols-3" /> : <AgentCards agents={o.agents} />}</div>
            </Panel>

            <Panel className="flex flex-col">
              <PanelHeader title="Live activity" description="Streaming from the agents." action={<ActivityIcon className="size-4 text-muted-foreground" aria-hidden />} />
              <div className="scrollbar-thin max-h-[440px] flex-1 overflow-y-auto p-3">
                {activity.isLoading ? <RowsSkeleton rows={6} /> : <ActivityFeed events={activity.data ?? []} />}
              </div>
            </Panel>
          </div>

          <div className="grid gap-6 lg:grid-cols-2">
            <Panel>
              <PanelHeader
                title="Spend vs caps"
                description="API costs across all providers, last 30 days."
                action={
                  o && (
                    <span className="tabular text-xs text-muted-foreground">
                      {usd(o.budget.spent_month)} / {usd(o.budget.monthly_cap, 0)} this month
                    </span>
                  )
                }
              />
              <div className="space-y-4 p-5">
                {costs.data ? <CostChart data={costs.data.daily} dailyCap={costs.data.budget.daily_cap} /> : <Skeleton className="h-44 w-full" />}
                {o && (
                  <div className="grid grid-cols-2 gap-4">
                    <div className="space-y-1.5">
                      <div className="flex justify-between text-xs">
                        <span className="text-muted-foreground">Daily</span>
                        <span className="tabular font-medium">{usd(o.budget.spent_today)} / {usd(o.budget.daily_cap)}</span>
                      </div>
                      <Meter value={o.budget.spent_today} max={o.budget.daily_cap} label="Daily cap" />
                    </div>
                    <div className="space-y-1.5">
                      <div className="flex justify-between text-xs">
                        <span className="text-muted-foreground">Monthly</span>
                        <span className="tabular font-medium">{usd(o.budget.spent_month)} / {usd(o.budget.monthly_cap, 0)}</span>
                      </div>
                      <Meter value={o.budget.spent_month} max={o.budget.monthly_cap} label="Monthly cap" />
                    </div>
                  </div>
                )}
              </div>
            </Panel>

            <Panel>
              <PanelHeader title="Deliverability" description="Last 30 days. Stage 2 needs bounce < 3% and complaints < 0.1%." />
              <div className="grid grid-cols-2 gap-4 p-5">
                {o ? (
                  <>
                    <DeliverabilityTile
                      label="Bounce rate"
                      value={o.deliverability.bounce_rate}
                      limit={BOUNCE_LIMIT}
                      detail={`${o.deliverability.bounced_30d} of ${o.deliverability.sent_30d} sent`}
                    />
                    <DeliverabilityTile
                      label="Complaint rate"
                      value={o.deliverability.complaint_rate}
                      limit={COMPLAINT_LIMIT}
                      digits={2}
                      detail={`${o.deliverability.complained_30d} complaints`}
                    />
                    <div className="col-span-2 rounded-lg border border-dashed p-3 text-xs text-muted-foreground">
                      {o.deliverability.sent_30d === 0
                        ? "No emails sent yet. Metrics appear once outreach starts (Phase 4)."
                        : `${o.deliverability.sent_30d} emails sent in the last 30 days.`}
                    </div>
                  </>
                ) : (
                  <Skeleton className="col-span-2 h-32" />
                )}
              </div>
            </Panel>
          </div>
        </div>
      )}
    </>
  );
}

function DeliverabilityTile({
  label,
  value,
  limit,
  detail,
  digits = 1,
}: {
  label: string;
  value: number;
  limit: number;
  detail: string;
  digits?: number;
}) {
  const ok = value < limit;
  return (
    <div className="space-y-2 rounded-lg border bg-surface-raised p-4">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-muted-foreground">{label}</span>
        <StatusPill tone={ok ? "success" : "danger"} dot={false}>
          {ok ? <MailCheck className="size-3" aria-hidden /> : <MailWarning className="size-3" aria-hidden />}
          {ok ? "Within limit" : "Over limit"}
        </StatusPill>
      </div>
      <div className="tabular text-2xl font-semibold">{pct(value, digits)}</div>
      <div className="flex items-center gap-1 text-[11px] text-muted-foreground">
        {!ok && <AlertOctagon className="size-3 text-destructive" aria-hidden />}
        {detail} · limit {pct(limit, digits)}
      </div>
    </div>
  );
}
