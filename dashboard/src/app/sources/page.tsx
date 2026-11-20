"use client";

import { Radar } from "lucide-react";
import { PageHeader } from "@/components/common/page-header";
import { CardsSkeleton, EmptyState, ErrorState } from "@/components/common/states";
import { StatusPill } from "@/components/common/status-pill";
import { FreeTierPanel } from "@/components/sources/free-tier-panel";
import { PlatformReport } from "@/components/sources/platform-report";
import { ProviderRoutesPanel } from "@/components/sources/provider-routes-panel";
import { SourceCard } from "@/components/sources/source-card";
import { useSources } from "@/lib/queries";

export default function SourcesPage() {
  const { data, isLoading, isError, error, refetch } = useSources();
  const enabled = data?.filter((s) => s.enabled).length ?? 0;
  const live = data?.filter((s) => s.enabled && s.integration?.state === "ready").length ?? 0;
  const off = data?.filter((s) => s.integration && s.integration.state !== "ready").length ?? 0;
  const degraded = data?.filter((s) => s.last_status === "degraded").length ?? 0;

  return (
    <>
      <PageHeader
        title="Sources"
        description="Where leads come from. Each source runs on its own schedule through the safe fetcher; platform rules are enforced in code."
        actions={
          data && (
            <div className="flex gap-2">
              <StatusPill tone="success">{live} live</StatusPill>
              <StatusPill tone="neutral">{enabled} enabled</StatusPill>
              {off > 0 && <StatusPill tone="neutral">{off} off</StatusPill>}
              {degraded > 0 && <StatusPill tone="warning">{degraded} degraded</StatusPill>}
            </div>
          )
        }
      />
      <PlatformReport />
      <FreeTierPanel />
      <ProviderRoutesPanel />
      {isError ? (
        <ErrorState error={error} onRetry={() => refetch()} />
      ) : isLoading || !data ? (
        <CardsSkeleton count={6} className="xl:grid-cols-3" />
      ) : data.length === 0 ? (
        <EmptyState icon={Radar} title="No data yet" description="Run `.\\make.ps1 seed` to register the discovery sources." />
      ) : (
        <div className="grid gap-4 lg:grid-cols-2 2xl:grid-cols-3">
          {data.map((source, i) => (
            <SourceCard key={source.key} source={source} index={i} />
          ))}
        </div>
      )}
    </>
  );
}
