"use client";

import type { ColumnDef } from "@tanstack/react-table";
import { Ban, ExternalLink, Search } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { DataTable } from "@/components/common/data-table";
import { PageHeader } from "@/components/common/page-header";
import { EmptyState, ErrorState, RowsSkeleton } from "@/components/common/states";
import { LocaleCell, ServiceChips } from "@/components/leads/lead-bits";
import { ReasonPicker } from "@/components/leads/reason-picker";
import { Input } from "@/components/ui/input";
import { ago, titleCase } from "@/lib/format";
import { type NotUsefulRow, useNotUseful, useNotUsefulCounts } from "@/lib/not-useful";

const PAGE_SIZE = 25;

function safeHref(url: string): string | null {
  try {
    const parsed = new URL(url);
    return parsed.protocol === "http:" || parsed.protocol === "https:" ? parsed.toString() : null;
  } catch {
    return null;
  }
}

const COLUMNS: ColumnDef<NotUsefulRow, unknown>[] = [
  {
    id: "lead",
    header: "Lead",
    size: 320,
    cell: ({ row }) => {
      const l = row.original;
      const href = safeHref(l.source_url);
      return (
        <div className="min-w-0 max-w-[320px]">
          <Link href={`/leads/${l.id}`} className="block truncate font-medium hover:underline">
            {l.business_name ?? l.title ?? "Untitled"}
          </Link>
          <div className="flex items-center gap-1 truncate text-xs text-muted-foreground">
            {titleCase(l.source)}
            {href && (
              <a href={href} target="_blank" rel="noopener noreferrer nofollow" className="inline-flex items-center hover:text-foreground" aria-label="Open the source">
                <ExternalLink className="size-3" />
              </a>
            )}
          </div>
        </div>
      );
    },
  },
  {
    id: "reason",
    header: "Why not useful",
    cell: ({ row }) => (
      <div className="max-w-[360px]">
        <div className="text-sm font-medium">{row.original.reason_label ?? "Reason not recorded"}</div>
        {row.original.status_reason && (
          <div className="truncate text-xs text-muted-foreground" title={row.original.status_reason}>{row.original.status_reason}</div>
        )}
      </div>
    ),
  },
  { id: "services", header: "Needs", cell: ({ row }) => <ServiceChips services={row.original.service_needed} /> },
  { id: "country", header: "Country", cell: ({ row }) => <LocaleCell country={row.original.country} language={row.original.language} /> },
  { id: "updated_at", header: "Stopped", cell: ({ row }) => <span className="whitespace-nowrap text-xs text-muted-foreground">{ago(row.original.updated_at)}</span> },
];

function NotUsefulView() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const reason = params.get("reason") ?? undefined;
  const page = Number(params.get("page") ?? 1);
  const q = params.get("q") ?? undefined;

  const setParam = (updates: Record<string, string | undefined>) => {
    const next = new URLSearchParams(params.toString());
    Object.entries(updates).forEach(([k, v]) => (v ? next.set(k, v) : next.delete(k)));
    if (!("page" in updates)) next.delete("page");
    router.replace(`${pathname}?${next.toString()}`, { scroll: false });
  };

  const [search, setSearch] = useState(q ?? "");
  useEffect(() => {
    const t = setTimeout(() => {
      if ((q ?? "") !== search) setParam({ q: search || undefined });
    }, 300);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search]);

  const counts = useNotUsefulCounts();
  const { data, isLoading, isError, error, refetch, isFetching } = useNotUseful({ reason, q, page, page_size: PAGE_SIZE });

  return (
    <>
      <PageHeader
        title="Not useful"
        description="Leads that will never be worked, each with the reason. They are left out of every other page and count."
      />
      <div className="mb-4">
        <ReasonPicker reason={reason} counts={counts.isError ? undefined : counts.data} onChange={(r) => setParam({ reason: r })} />
      </div>
      <div className="relative mb-3 w-full sm:w-72">
        <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden />
        <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search by name, title or link" className="h-8 bg-surface-raised pl-8" aria-label="Search not useful leads" />
      </div>
      {isError ? (
        <ErrorState error={error} onRetry={() => refetch()} />
      ) : isLoading || !data ? (
        <RowsSkeleton rows={8} />
      ) : (
        <DataTable
          columns={COLUMNS}
          data={data.items}
          total={data.total}
          page={page}
          pageSize={PAGE_SIZE}
          sorting={[]}
          loading={isFetching}
          getRowId={(r) => r.id}
          onSortingChange={() => undefined}
          onPageChange={(p) => setParam({ page: String(p) })}
          empty={<EmptyState icon={Ban} title="Nothing here" description="No lead has been set aside for this reason." />}
        />
      )}
    </>
  );
}

export default function NotUsefulPage() {
  return (
    <Suspense fallback={<RowsSkeleton rows={8} />}>
      <NotUsefulView />
    </Suspense>
  );
}
