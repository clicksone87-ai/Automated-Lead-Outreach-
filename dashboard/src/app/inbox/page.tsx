"use client";

import { Inbox } from "lucide-react";
import Link from "next/link";
import { PageHeader } from "@/components/common/page-header";
import { Panel, PanelHeader } from "@/components/common/panel";
import { CardsSkeleton, EmptyState, ErrorState } from "@/components/common/states";
import { StatusPill } from "@/components/common/status-pill";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { dateTime, titleCase } from "@/lib/format";
import { type Deal, useInbox, usePatchDeal } from "@/lib/outreach";
import { useSettings } from "@/lib/queries";

const STEPS: { key: keyof Pick<Deal, "contract_status" | "payment_status" | "delivery_status">; label: string; options: string[] }[] = [
  { key: "contract_status", label: "Contract", options: ["none", "sent", "signed", "declined"] },
  { key: "payment_status", label: "Payment", options: ["none", "invoiced", "paid", "refunded"] },
  { key: "delivery_status", label: "Delivery", options: ["none", "in_progress", "delivered"] },
];

function PricingSheet() {
  const { data } = useSettings();
  if (!data) return null;
  return (
    <Panel>
      <PanelHeader title="Pricing & packages" description="Quote from this sheet. Contracts, payments and delivery are always handled by you." />
      <table className="w-full text-sm">
        <thead className="text-left text-xs text-muted-foreground">
          <tr>
            <th className="px-4 py-2 font-medium">Package</th>
            <th className="px-4 py-2 text-right font-medium">USD</th>
            <th className="px-4 py-2 text-right font-medium">EUR</th>
            <th className="px-4 py-2 text-right font-medium">GBP</th>
          </tr>
        </thead>
        <tbody>
          {data.pricing.map((p) => (
            <tr key={String(p.id)} className="border-t">
              <td className="px-4 py-2">
                <div className="font-medium">{String(p.package)}</div>
                <div className="text-xs text-muted-foreground">{titleCase(String(p.service))}</div>
              </td>
              <td className="tabular px-4 py-2 text-right">{String(p.price_usd)}</td>
              <td className="tabular px-4 py-2 text-right">{String(p.price_eur)}</td>
              <td className="tabular px-4 py-2 text-right">{String(p.price_gbp)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  );
}

export default function InboxPage() {
  const { data, isLoading, isError, error, refetch } = useInbox();
  const patch = usePatchDeal();
  return (
    <>
      <PageHeader title="Inbox" description="Positive replies and questions, with the lead's full history. Quote → contract → payment → delivery are manual steps." />
      {isError ? (
        <ErrorState error={error} onRetry={() => refetch()} />
      ) : isLoading || !data ? (
        <CardsSkeleton count={2} />
      ) : (
        <div className="grid gap-6 xl:grid-cols-[1fr_380px]">
          <div className="space-y-4">
            {data.length === 0 ? (
              <EmptyState icon={Inbox} title="No data yet" description="When someone replies positively or asks a question, the conversation lands here and follow-ups stop." />
            ) : (
              data.map((item) => (
                <Panel key={item.lead_id}>
                  <PanelHeader
                    title={item.business_name ?? "Unnamed business"}
                    description={`${item.country ?? "—"} · ${item.language ?? "—"}`}
                    action={<Link href={`/leads?lead=${item.lead_id}`} className="text-xs font-medium text-primary hover:underline">Full history</Link>}
                  />
                  <div className="space-y-3 p-4">
                    {item.replies.map((r) => (
                      <article key={r.id} className="rounded-lg border p-3">
                        <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
                          <span className="font-medium">{r.from_email}</span>
                          <span className="flex items-center gap-2 text-muted-foreground">
                            <StatusPill tone={r.classification === "positive" ? "success" : "info"}>{titleCase(r.classification ?? "other")}</StatusPill>
                            {dateTime(r.received_at)}
                          </span>
                        </div>
                        <p className="mt-2 whitespace-pre-line text-sm">{r.text}</p>
                      </article>
                    ))}
                    <div className="grid gap-3 sm:grid-cols-3">
                      {STEPS.map((step) => (
                        <label key={step.key} className="space-y-1 text-xs font-medium">
                          {step.label}
                          <Select
                            value={item.deal?.[step.key] ?? "none"}
                            onValueChange={(v) => patch.mutate({ leadId: item.lead_id, patch: { [step.key]: v } })}
                          >
                            <SelectTrigger size="sm" className="h-8 w-full" aria-label={step.label}>
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              {step.options.map((o) => (
                                <SelectItem key={o} value={o}>{titleCase(o)}</SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                        </label>
                      ))}
                    </div>
                  </div>
                </Panel>
              ))
            )}
          </div>
          <PricingSheet />
        </div>
      )}
    </>
  );
}
