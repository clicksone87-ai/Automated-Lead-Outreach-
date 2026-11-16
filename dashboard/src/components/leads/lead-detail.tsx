"use client";

import {
  Bot,
  CheckCircle2,
  Clock,
  ExternalLink,
  FileText,
  Globe,
  Image as ImageIcon,
  Mail,
  MessageSquare,
  Palette,
  RotateCw,
  Shield,
  ShieldAlert,
  XCircle,
} from "lucide-react";
import { Panel, PanelHeader } from "@/components/common/panel";
import { EmptyState, ErrorState, RowsSkeleton } from "@/components/common/states";
import { LogoGrid, PreviewCard } from "@/components/deliverables/deliverables";
import { StatusPill } from "@/components/common/status-pill";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { fileUrl, type LeadDetail } from "@/lib/api";
import { ago, countryName, dateTime, languageName, titleCase } from "@/lib/format";
import { useLead, useRerunLead } from "@/lib/queries";
import { AGENT_LABELS, TRIGGERS } from "@/lib/status";
import { cn } from "@/lib/utils";
import { AuditSummaryCard, BriefView } from "./brief-view";
import { DemoBadge, IntentBar, LeadStatusPill, ServiceChips } from "./lead-bits";

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 truncate text-sm">{children}</dd>
    </div>
  );
}

function SafeLink({ href, children }: { href: string; children: React.ReactNode }) {
  const ok = /^https?:\/\//i.test(href);
  return ok ? (
    <a href={href} target="_blank" rel="noopener noreferrer nofollow" className="inline-flex items-center gap-1 text-primary hover:underline">
      {children} <ExternalLink className="size-3" aria-hidden />
    </a>
  ) : (
    <span>{children}</span>
  );
}

function Timeline({ detail }: { detail: LeadDetail }) {
  type Item = { at: string; icon: typeof Bot; tone: string; title: string; body?: string };
  const items: Item[] = [
    ...detail.agent_runs.map((r) => ({
      at: r.started_at ?? r.finished_at ?? "",
      icon: r.status === "failed" ? XCircle : r.status === "succeeded" ? CheckCircle2 : Bot,
      tone:
        r.status === "failed"
          ? "bg-danger-soft text-danger-soft-foreground"
          : r.status === "succeeded"
            ? "bg-success-soft text-success-soft-foreground"
            : "bg-muted text-muted-foreground",
      title: `${AGENT_LABELS[r.agent] ?? r.agent} — ${r.status}${r.attempts > 1 ? ` (attempt ${r.attempts})` : ""}`,
      body: (r.output?.reason as string | undefined) ?? r.error?.split("\n").filter(Boolean).at(-1),
    })),
    ...detail.policy_decisions.map((d) => ({
      at: d.created_at,
      icon: d.allowed ? Shield : ShieldAlert,
      tone: d.allowed ? "bg-info-soft text-info-soft-foreground" : "bg-warning-soft text-warning-soft-foreground",
      title: `Policy · ${titleCase(d.check)} — ${d.allowed ? "allowed" : "blocked"}`,
      body: d.reason,
    })),
    {
      at: detail.lead.created_at,
      icon: Globe,
      tone: "bg-primary-soft text-primary-soft-foreground",
      title: `Discovered via ${titleCase(detail.lead.source)}`,
      body: TRIGGERS[detail.lead.trigger_type],
    },
  ].sort((a, b) => (a.at < b.at ? 1 : -1));

  return (
    <ol className="relative space-y-4 before:absolute before:left-3 before:top-1 before:bottom-1 before:w-px before:bg-border">
      {items.map((item, i) => (
        <li key={i} className="relative flex gap-3 pl-0">
          <span className={cn("relative z-10 flex size-6 shrink-0 items-center justify-center rounded-full ring-4 ring-card", item.tone)}>
            <item.icon className="size-3.5" aria-hidden />
          </span>
          <div className="min-w-0 flex-1 pb-1">
            <div className="flex flex-wrap items-baseline justify-between gap-x-3">
              <p className="text-sm font-medium">{item.title}</p>
              <time className="text-[11px] text-muted-foreground" dateTime={item.at}>
                {dateTime(item.at)}
              </time>
            </div>
            {item.body && <p className="mt-0.5 break-words text-xs text-muted-foreground">{item.body}</p>}
          </div>
        </li>
      ))}
    </ol>
  );
}

function NotYet({ icon, title, description }: { icon: typeof Bot; title: string; description: string }) {
  return <EmptyState icon={icon} title={title} description={description} className="py-10" />;
}

export function LeadDetailView({ id, compact = false }: { id: string; compact?: boolean }) {
  const { data, isLoading, isError, error, refetch } = useLead(id);
  const rerun = useRerunLead();

  if (isLoading) return <RowsSkeleton rows={8} />;
  if (isError || !data) return <ErrorState error={error} onRetry={() => refetch()} />;
  const { lead, business } = data;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 space-y-1.5">
          <div className="flex flex-wrap items-center gap-2">
            <LeadStatusPill status={lead.status} />
            {lead.is_demo && <DemoBadge />}
            <span className="text-xs text-muted-foreground">found {ago(lead.created_at)}</span>
          </div>
          <h2 className={cn("font-semibold tracking-tight", compact ? "text-lg" : "text-2xl")}>
            {business?.name ?? lead.title ?? "Untitled lead"}
          </h2>
          {business?.name && lead.title && <p className="text-sm text-muted-foreground">{lead.title}</p>}
          {lead.status_reason && (
            <p className="rounded-md bg-muted px-2 py-1 text-xs text-muted-foreground">{lead.status_reason}</p>
          )}
        </div>
        <Button variant="outline" size="sm" onClick={() => rerun.mutate(lead.id)} disabled={rerun.isPending}>
          <RotateCw className={cn("size-3.5", rerun.isPending && "animate-spin")} /> Re-run pipeline
        </Button>
      </div>

      <dl className="grid grid-cols-2 gap-4 rounded-xl border bg-surface-sunken/50 p-4 sm:grid-cols-4">
        <Fact label="Country">{countryName(lead.country)}</Fact>
        <Fact label="Language">{languageName(lead.language)}</Fact>
        <Fact label="Time zone">{lead.timezone ?? "Unknown"}</Fact>
        <Fact label="Intent">
          <IntentBar score={lead.intent_score} />
        </Fact>
        <Fact label="Trigger">{TRIGGERS[lead.trigger_type] ?? lead.trigger_type}</Fact>
        <Fact label="Services">
          <ServiceChips services={lead.service_needed} />
        </Fact>
        <Fact label="Website">
          {business?.domain ? <SafeLink href={`https://${business.domain}`}>{business.domain}</SafeLink> : business?.has_website === false ? "None found" : "—"}
        </Fact>
        <Fact label="Source">
          <SafeLink href={lead.source_url}>{titleCase(lead.source)}</SafeLink>
        </Fact>
      </dl>

      <Tabs defaultValue="timeline">
        <TabsList className="flex w-full flex-wrap justify-start">
          <TabsTrigger value="timeline">Timeline</TabsTrigger>
          <TabsTrigger value="evidence">Evidence ({data.evidence.length})</TabsTrigger>
          <TabsTrigger value="brief">Brief</TabsTrigger>
          <TabsTrigger value="contact">Contact ({data.contacts.length})</TabsTrigger>
          <TabsTrigger value="drafts">Drafts</TabsTrigger>
          <TabsTrigger value="emails">Emails ({data.messages.length})</TabsTrigger>
        </TabsList>

        <TabsContent value="timeline" className="pt-4">
          <Timeline detail={data} />
        </TabsContent>

        <TabsContent value="evidence" className="space-y-3 pt-4">
          {data.raw_text && (
            <Panel as="div" className="p-4">
              <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Original text</p>
              <p className="whitespace-pre-wrap text-sm leading-relaxed">{data.raw_text}</p>
            </Panel>
          )}
          {data.evidence.map((e) => (
            <Panel key={e.id} as="div" className="space-y-2 p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <StatusPill tone="info" dot={false}>
                  <FileText className="size-3" aria-hidden /> {titleCase(e.kind)}
                </StatusPill>
                <span className="text-[11px] text-muted-foreground">captured {dateTime(e.captured_at)}</span>
              </div>
              {e.url && <SafeLink href={e.url}><span className="break-all text-xs">{e.url}</span></SafeLink>}
              {e.excerpt && <p className="line-clamp-4 text-xs text-muted-foreground">{e.excerpt}</p>}
              {e.file_path && e.kind === "screenshot" && (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={fileUrl(e.file_path) ?? ""} alt="Evidence screenshot" className="max-h-64 rounded-lg border" />
              )}
            </Panel>
          ))}
        </TabsContent>

        <TabsContent value="brief" className="pt-4">
          {data.brief ? (
            <BriefView brief={data.brief} />
          ) : (
            <NotYet icon={FileText} title="No data yet" description="The Requirements agent writes a structured brief once it qualifies this lead." />
          )}
        </TabsContent>

        <TabsContent value="contact" className="space-y-2 pt-4">
          {data.contacts.length ? (
            data.contacts.map((c) => (
              <div key={c.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border p-3">
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium">{c.value}</p>
                  <p className="text-xs text-muted-foreground">
                    {titleCase(c.type)} · source <SafeLink href={c.source_url}>page</SafeLink>
                  </p>
                </div>
                <StatusPill tone={c.verification_status === "valid" ? "success" : c.verification_status === "invalid" ? "danger" : "neutral"}>
                  {titleCase(c.verification_status)}
                </StatusPill>
              </div>
            ))
          ) : (
            <NotYet icon={Mail} title="No data yet" description="Contact Enrichment finds a verified route (with its source page) before any drafts are made." />
          )}
        </TabsContent>

        <TabsContent value="drafts" className="grid gap-4 pt-4">
          <Panel as="div">
            <PanelHeader title="Audit" />
            {data.audit ? (
              <AuditSummaryCard audit={data.audit} leadId={lead.id} />
            ) : (
              <NotYet icon={Globe} title="No data yet" description="Runs for qualified leads with a website and a verified contact." />
            )}
          </Panel>
          <Panel as="div">
            <PanelHeader title="Logo concepts" />
            {data.logos.length ? (
              <div className="p-4">
                <LogoGrid logos={data.logos} />
              </div>
            ) : (
              <NotYet icon={Palette} title="No data yet" description="Created only after qualification and a verified contact route." />
            )}
          </Panel>
          <Panel as="div">
            <PanelHeader title="Website preview" />
            {data.preview ? (
              <div className="p-4">
                <PreviewCard preview={data.preview} />
              </div>
            ) : (
              <NotYet icon={ImageIcon} title="No data yet" description="Built from a template after branding, then quality-checked." />
            )}
          </Panel>
        </TabsContent>

        <TabsContent value="emails" className="pt-4">
          {data.messages.length ? (
            <ul className="space-y-2">
              {data.messages.map((m) => (
                <li key={String(m.id)} className="flex items-center justify-between rounded-lg border p-3 text-sm">
                  <span>{String(m.to_email)}</span>
                  <StatusPill tone="info">{titleCase(String(m.delivery_status))}</StatusPill>
                </li>
              ))}
            </ul>
          ) : (
            <NotYet icon={MessageSquare} title="No data yet" description="Proposals appear here after drafting and (at Stage 1) your approval." />
          )}
        </TabsContent>
      </Tabs>

      <p className="flex items-center gap-1 text-[11px] text-muted-foreground">
        <Clock className="size-3" aria-hidden /> Updated {ago(lead.updated_at)} · ID <span className="font-mono">{lead.id}</span>
      </p>
    </div>
  );
}
