"use client";

import type { ColumnDef, SortingState } from "@tanstack/react-table";
import { ExternalLink, Search, Users, X } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import { DataTable } from "@/components/common/data-table";
import { PageHeader } from "@/components/common/page-header";
import { EmptyState, ErrorState, RowsSkeleton } from "@/components/common/states";
import { DemoBadge, IntentBar, LeadStatusPill, LocaleCell, ServiceChips } from "@/components/leads/lead-bits";
import { LeadDetailView } from "@/components/leads/lead-detail";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import type { LeadRow } from "@/lib/api";
import { ago, titleCase } from "@/lib/format";
import { useLeads, useSources, type LeadFilters } from "@/lib/queries";
import { SERVICES, TRIGGERS } from "@/lib/status";

const STAGES: Record<string, string> = {
  discovery: "Discovery", requirements: "Requirements", contact: "Contact", audit: "Audit",
  branding: "Branding", builder: "Builder", qa: "QA", proposal: "Proposal", outreach: "Outreach",
  stopped: "Stopped",
};
const COUNTRIES = ["US", "GB", "IE", "DE", "FR", "ES"];
const LANGUAGES = ["en", "de", "fr", "es"];
const SCORES = ["40", "60", "75"];
const ALL = "__all";

const columns: ColumnDef<LeadRow, unknown>[] = [
  {
    id: "lead",
    header: "Lead",
    enableSorting: false,
    size: 340,
    cell: ({ row }) => {
      const l = row.original;
      return (
        <div className="min-w-0 max-w-[340px]">
          <div className="flex items-center gap-1.5">
            <span className="truncate font-medium">{l.business_name ?? l.title ?? "Untitled"}</span>
            {l.is_demo && <DemoBadge />}
          </div>
          <div className="truncate text-xs text-muted-foreground">
            {TRIGGERS[l.trigger_type] ?? l.trigger_type}
            {l.business_name && l.title ? ` · ${l.title}` : ""}
          </div>
        </div>
      );
    },
  },
  { id: "status", accessorKey: "status", header: "Status", cell: ({ row }) => <LeadStatusPill status={row.original.status} /> },
  { id: "intent_score", accessorKey: "intent_score", header: "Intent", cell: ({ row }) => <IntentBar score={row.original.intent_score} /> },
  { id: "services", header: "Services", enableSorting: false, cell: ({ row }) => <ServiceChips services={row.original.service_needed} /> },
  { id: "country", accessorKey: "country", header: "Locale", cell: ({ row }) => <LocaleCell country={row.original.country} language={row.original.language} /> },
  { id: "source", header: "Source", enableSorting: false, cell: ({ row }) => <span className="text-xs">{titleCase(row.original.source)}</span> },
  { id: "created_at", accessorKey: "created_at", header: "Found", cell: ({ row }) => <span className="whitespace-nowrap text-xs text-muted-foreground">{ago(row.original.created_at)}</span> },
];

function FilterSelect({
  label, value, options, onChange,
}: {
  label: string; value: string | undefined; options: Record<string, string>; onChange: (v: string | undefined) => void;
}) {
  return (
    <Select value={value ?? ALL} onValueChange={(v) => onChange(v === ALL ? undefined : v)}>
      <SelectTrigger size="sm" className="h-8 min-w-[128px] bg-surface-raised" aria-label={label}>
        <SelectValue placeholder={label} />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={ALL}>All {label.toLowerCase()}</SelectItem>
        {Object.entries(options).map(([k, v]) => (
          <SelectItem key={k} value={k}>{v}</SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

function LeadsView() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const { data: sources } = useSources();

  const get = (k: string) => params.get(k) ?? undefined;
  const filters: LeadFilters = {
    page: Number(get("page") ?? 1),
    page_size: 25,
    sort: get("sort") ?? "-created_at",
    stage: get("stage"),
    status: get("status") ? [get("status")!] : undefined,
    source: get("source") ? [get("source")!] : undefined,
    country: get("country") ? [get("country")!] : undefined,
    language: get("language") ? [get("language")!] : undefined,
    service: get("service") ? [get("service")!] : undefined,
    trigger: get("trigger") ? [get("trigger")!] : undefined,
    min_score: get("min_score") ? Number(get("min_score")) : undefined,
    q: get("q"),
  };
  const selected = get("lead") ?? null;

  const setParam = (updates: Record<string, string | undefined>, resetPage = true) => {
    const next = new URLSearchParams(params.toString());
    Object.entries(updates).forEach(([k, v]) => (v ? next.set(k, v) : next.delete(k)));
    if (resetPage && !("page" in updates)) next.delete("page");
    router.replace(`${pathname}?${next.toString()}`, { scroll: false });
  };

  const [search, setSearch] = useState(filters.q ?? "");
  useEffect(() => {
    const t = setTimeout(() => {
      if ((filters.q ?? "") !== search) setParam({ q: search || undefined });
    }, 300);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search]);

  const { data, isLoading, isError, error, refetch, isFetching } = useLeads(filters);
  const sorting: SortingState = [{ id: filters.sort.replace(/^-/, ""), desc: filters.sort.startsWith("-") }];
  const sourceOptions = useMemo(
    () => Object.fromEntries((sources ?? []).map((s) => [s.key, s.label])),
    [sources],
  );
  const activeFilters = ["stage", "status", "source", "country", "language", "service", "trigger", "min_score", "q"].filter((k) => params.get(k));

  return (
    <>
      <PageHeader
        title="Leads"
        description="Every lead across all sources, with its evidence, brief, contact route, drafts and emails."
        actions={data && <span className="tabular text-sm text-muted-foreground">{data.total} leads</span>}
      />

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <div className="relative w-full sm:w-64">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden />
          <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search name, domain, text…" className="h-8 bg-surface-raised pl-8" aria-label="Search leads" />
        </div>
        <FilterSelect label="Stage" value={filters.stage} options={STAGES} onChange={(v) => setParam({ stage: v, status: undefined })} />
        <FilterSelect label="Source" value={filters.source?.[0]} options={sourceOptions} onChange={(v) => setParam({ source: v })} />
        <FilterSelect label="Country" value={filters.country?.[0]} options={Object.fromEntries(COUNTRIES.map((c) => [c, c]))} onChange={(v) => setParam({ country: v })} />
        <FilterSelect label="Language" value={filters.language?.[0]} options={Object.fromEntries(LANGUAGES.map((l) => [l, l.toUpperCase()]))} onChange={(v) => setParam({ language: v })} />
        <FilterSelect label="Service" value={filters.service?.[0]} options={SERVICES} onChange={(v) => setParam({ service: v })} />
        <FilterSelect label="Trigger" value={filters.trigger?.[0]} options={TRIGGERS} onChange={(v) => setParam({ trigger: v })} />
        <FilterSelect label="Intent" value={filters.min_score?.toString()} options={Object.fromEntries(SCORES.map((s) => [s, `≥ ${s}`]))} onChange={(v) => setParam({ min_score: v })} />
        {activeFilters.length > 0 && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setSearch("");
              router.replace(pathname, { scroll: false });
            }}
          >
            <X className="size-3.5" /> Clear ({activeFilters.length})
          </Button>
        )}
      </div>

      {isError ? (
        <ErrorState error={error} onRetry={() => refetch()} />
      ) : isLoading || !data ? (
        <RowsSkeleton rows={8} />
      ) : (
        <DataTable
          columns={columns}
          data={data.items}
          total={data.total}
          page={filters.page}
          pageSize={filters.page_size}
          sorting={sorting}
          loading={isFetching}
          getRowId={(r) => r.id}
          selectedId={selected}
          onSortingChange={(s) => {
            const first = s[0];
            if (first) setParam({ sort: `${first.desc ? "-" : ""}${first.id}` });
          }}
          onPageChange={(p) => setParam({ page: String(p) }, false)}
          onRowClick={(row) => setParam({ lead: row.id }, false)}
          empty={
            <EmptyState
              icon={Users}
              title={activeFilters.length ? "No leads match these filters" : "No leads yet"}
              description={
                activeFilters.length
                  ? "Try removing a filter."
                  : "Enable a source on the Sources page, or run a cycle, and leads will appear here in real time."
              }
              action={!activeFilters.length && <Button asChild size="sm"><Link href="/sources">Open sources</Link></Button>}
            />
          }
        />
      )}

      <Sheet open={Boolean(selected)} onOpenChange={(open) => !open && setParam({ lead: undefined }, false)}>
        <SheetContent className="w-full overflow-y-auto sm:max-w-2xl">
          <SheetHeader className="flex-row items-center justify-between gap-2 space-y-0 border-b pb-3">
            <div>
              <SheetTitle>Lead details</SheetTitle>
              <SheetDescription className="sr-only">Full history of the selected lead</SheetDescription>
            </div>
            {selected && (
              <Button asChild variant="ghost" size="sm" className="mr-8">
                <Link href={`/leads/${selected}`}>
                  Open page <ExternalLink className="size-3.5" />
                </Link>
              </Button>
            )}
          </SheetHeader>
          <div className="px-4 pb-6">{selected && <LeadDetailView id={selected} compact />}</div>
        </SheetContent>
      </Sheet>
    </>
  );
}

export default function LeadsPage() {
  return (
    <Suspense fallback={<RowsSkeleton rows={8} />}>
      <LeadsView />
    </Suspense>
  );
}
