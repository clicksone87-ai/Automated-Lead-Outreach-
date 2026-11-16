"use client";

import { Palette } from "lucide-react";
import Link from "next/link";
import { PageHeader } from "@/components/common/page-header";
import { Panel, PanelHeader } from "@/components/common/panel";
import { CardsSkeleton, EmptyState, ErrorState } from "@/components/common/states";
import { StatusPill } from "@/components/common/status-pill";
import { LogoGrid, PreviewCard } from "@/components/deliverables/deliverables";
import { useGallery } from "@/lib/queries";
import { leadStatus } from "@/lib/status";

export default function PreviewsPage() {
  const { data, isLoading, isError, error, refetch } = useGallery();
  return (
    <>
      <PageHeader
        title="Previews & logos"
        description="Logo concepts and private website previews, with their quality-check results. Previews are never published on a client's domain."
      />
      {isError ? (
        <ErrorState error={error} onRetry={() => refetch()} />
      ) : isLoading || !data ? (
        <CardsSkeleton count={2} />
      ) : data.length === 0 ? (
        <EmptyState
          icon={Palette}
          title="No data yet"
          description="Logo concepts and previews appear here once a qualified lead with a verified contact route reaches Branding."
        />
      ) : (
        <div className="space-y-6">
          {data.map((item) => {
            const meta = leadStatus(item.status);
            return (
              <Panel key={item.lead_id}>
                <PanelHeader
                  title={item.business_name ?? "Unnamed business"}
                  description={`Language: ${item.language ?? "—"}`}
                  action={
                    <div className="flex items-center gap-2">
                      <StatusPill tone={meta.tone}>{meta.label}</StatusPill>
                      <Link href={`/leads?lead=${item.lead_id}`} className="text-xs font-medium text-primary hover:underline">
                        Open lead
                      </Link>
                    </div>
                  }
                />
                <div className="grid gap-5 p-5 xl:grid-cols-[1fr_1.1fr]">
                  <div>
                    <p className="mb-2 text-xs font-medium text-muted-foreground">Logo concepts</p>
                    {item.logos.length ? <LogoGrid logos={item.logos} compact /> : <p className="text-sm text-muted-foreground">No data yet</p>}
                  </div>
                  <div>
                    <p className="mb-2 text-xs font-medium text-muted-foreground">Website preview</p>
                    {item.preview ? <PreviewCard preview={item.preview} /> : <p className="text-sm text-muted-foreground">No data yet</p>}
                  </div>
                </div>
              </Panel>
            );
          })}
        </div>
      )}
    </>
  );
}
