"use client";

import { Radar } from "lucide-react";
import { PageHeader } from "@/components/common/page-header";
import { CardsSkeleton, EmptyState, ErrorState } from "@/components/common/states";
import { StatusPill } from "@/components/common/status-pill";
import { SourceCard } from "@/components/sources/source-card";
import { useSources } from "@/lib/queries";

export default function SourcesPage() {
  const { data, isLoading, isError, error, refetch } = useSources();
  const enabled = data?.filter((s) => s.enabled).length ?? 0;
  const needKeys = data?.filter((s) => s.integration?.state === "needs_api_key").length ?? 0;

  return (
    <>
      <PageHeader
        title="Sources"
        description="Where leads come from. Each source runs on its own schedule through the safe fetcher; platform rules are enforced in code."
        actions={
          data && (
            <div className="flex gap-2">
              <StatusPill tone="success">{enabled} enabled</StatusPill>
              {needKeys > 0 && <StatusPill tone="warning">{needKeys} need API keys</StatusPill>}
            </div>
          )
        }
      />
      {isError ? (
        <ErrorState error={error} onRetry={() => refetch()} />
      ) : isLoading || !data ? (
        <CardsSkeleton count={6} className="xl:grid-cols-3" />
      ) : data.length === 0 ? (
        <EmptyState icon={Radar} title="No sources registered" description="Run `.\\make.ps1 seed` to register the discovery sources." />
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
