"use client";

import { Ban, Plus } from "lucide-react";
import { useState } from "react";
import { PageHeader } from "@/components/common/page-header";
import { Panel, PanelHeader } from "@/components/common/panel";
import { CardsSkeleton, EmptyState, ErrorState } from "@/components/common/states";
import { StatusPill } from "@/components/common/status-pill";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { dateTime, titleCase } from "@/lib/format";
import { useAddSuppression, useSuppressions } from "@/lib/outreach";

export default function SuppressionsPage() {
  const { data, isLoading, isError, error, refetch } = useSuppressions();
  const add = useAddSuppression();
  const [value, setValue] = useState("");
  const isEmail = value.includes("@");
  return (
    <>
      <PageHeader
        title="Suppression list"
        description="Permanent: unsubscribes, declines, bounces and complaints. Nobody on this list is ever contacted again, through any source or agent."
        actions={data && <StatusPill tone="neutral">{data.length} entries</StatusPill>}
      />
      <Panel className="mb-6">
        <PanelHeader title="Add manually" description="An email address or a whole domain." />
        <form
          className="flex gap-2 p-4"
          onSubmit={(e) => {
            e.preventDefault();
            const v = value.trim().toLowerCase();
            if (!v) return;
            add.mutate(isEmail ? { email: v, reason: "manual" } : { domain: v, reason: "manual" }, { onSuccess: () => setValue("") });
          }}
        >
          <Input value={value} onChange={(e) => setValue(e.target.value)} placeholder="name@company.com or company.com" aria-label="Email or domain to suppress" />
          <Button type="submit" disabled={add.isPending || !value.trim()}>
            <Plus className="size-4" /> Suppress
          </Button>
        </form>
      </Panel>
      {isError ? (
        <ErrorState error={error} onRetry={() => refetch()} />
      ) : isLoading || !data ? (
        <CardsSkeleton count={1} />
      ) : data.length === 0 ? (
        <EmptyState icon={Ban} title="No data yet" description="Unsubscribes and declines are added automatically and kept forever." />
      ) : (
        <Panel>
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-muted-foreground">
              <tr>
                <th className="px-4 py-2 font-medium">Email / domain</th>
                <th className="px-4 py-2 font-medium">Reason</th>
                <th className="px-4 py-2 font-medium">Added</th>
              </tr>
            </thead>
            <tbody>
              {data.map((r) => (
                <tr key={r.id} className="border-t">
                  <td className="px-4 py-2 font-mono text-xs">{r.email ?? r.domain ?? (r.business_id ? `business ${r.business_id.slice(0, 8)}` : "—")}</td>
                  <td className="px-4 py-2"><StatusPill tone="neutral">{titleCase(r.reason)}</StatusPill></td>
                  <td className="px-4 py-2 text-muted-foreground">{dateTime(r.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      )}
    </>
  );
}
