"use client";

import type { ColumnDef, SortingState } from "@tanstack/react-table";
import { ChevronDown, ExternalLink, RotateCcw, Search, SlidersHorizontal, Users } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import { DataTable } from "@/components/common/data-table";
import { PageHeader } from "@/components/common/page-header";
import { EmptyState, ErrorState, RowsSkeleton } from "@/components/common/states";
import { DemoBadge, IntentBar, LeadStatusPill, LocaleCell, ServiceChips } from "@/components/leads/lead-bits";
import { LeadDetailView } from "@/components/leads/lead-detail";
import { InterestPicker, SourcePicker } from "@/components/leads/lead-tabs";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import type { LeadRow } from "@/lib/api";
import { ALL_FAMILIES, FAMILY_INFO, familySources, GOOD_LEADS, TIERS, type Family, type Tier } from "@/lib/families";
import { ago, titleCase, countryName, languageName } from "@/lib/format";
import { cn } from "@/lib/utils";
import { useLeads, useSources, useSettings, type LeadFilters } from "@/lib/queries";
import { SERVICES, TRIGGERS } from "@/lib/status";
import { LANGUAGES } from "@/lib/markets";

const STAGES: Record<string, string> = {
  discovery: "Discovery", requirements: "Requirements", contact: "Contact", audit: "Audit",
  branding: "Branding", builder: "Builder", qa: "QA", proposal: "Proposal", outreach: "Outreach",
  stopped: "Stopped",
};
//const COUNTRIES = ["US", "GB", "IE", "DE", "FR", "ES"];
//const LANGUAGES = ["en", "de", "fr", "es"];
const ALL = "__all";

// "Reddit (via Apify)" -> "Reddit": the part in brackets is technical detail.
const shortSource = (label: string) => label.replace(/\s*\([^)]*\)\s*$/, "");

function makeColumns(sourceNames: Record<string, string>): ColumnDef<LeadRow, unknown>[] {
  return [
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
    {
      id: "intent_score",
      accessorKey: "intent_score",
      header: "Interest",
      cell: ({ row }) =>
        row.original.intent_score == null ? (
          <span className="text-xs text-muted-foreground">Not scored yet</span>
        ) : (
          <IntentBar score={row.original.intent_score} />
        ),
    },
    { id: "services", header: "Needs", enableSorting: false, cell: ({ row }) => <ServiceChips services={row.original.service_needed} /> },
    { id: "status", accessorKey: "status", header: "Progress", cell: ({ row }) => <LeadStatusPill status={row.original.status} /> },
    { id: "country", accessorKey: "country", header: "Country", cell: ({ row }) => <LocaleCell country={row.original.country} language={row.original.language} /> },
    {
      id: "source",
      header: "Found on",
      enableSorting: false,
      cell: ({ row }) => <span className="text-xs">{sourceNames[row.original.source] ?? titleCase(row.original.source)}</span>,
    },
    { id: "created_at", accessorKey: "created_at", header: "Found", cell: ({ row }) => <span className="whitespace-nowrap text-xs text-muted-foreground">{ago(row.original.created_at)}</span> },
  ];
}

const ADVANCED = ["stage", "status", "source", "country", "language", "service", "trigger"] as const;

function FilterSelect({
  label, allLabel, value, options, onChange,
}: {
  label: string; allLabel: string; value: string | undefined; options: Record<string, string>; onChange: (v: string | undefined) => void;
}) {
  return (
    <div className="grid gap-1">
      <span className="text-xs text-muted-foreground">{label}</span>
      <Select value={value ?? ALL} onValueChange={(v) => onChange(v === ALL ? undefined : v)}>
        <SelectTrigger size="sm" className="h-8 min-w-[150px] bg-surface-raised" aria-label={label}>
          <SelectValue placeholder={allLabel} />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ALL}>{allLabel}</SelectItem>
          {Object.entries(options).map(([k, v]) => (
            <SelectItem key={k} value={k}>{v}</SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

function LeadsView() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const { data: sources } = useSources();
  const { data: settings } = useSettings();
  const countryCodes = Object.keys(settings?.country_rules ?? {});
  const get = (k: string) => params.get(k) ?? undefined;

  // Source-family tab and intent-tier tab live in the URL like every other filter.
  // Unknown values (e.g. a hand-edited URL) are ignored instead of crashing the page.
  const familyParam = get("family");
  const family = familyParam && familyParam in FAMILY_INFO ? (familyParam as Family) : undefined;
  const tierParam = get("tier");
  const tier = tierParam && tierParam in TIERS ? (tierParam as Tier) : undefined;
  // No tier chosen = good leads (60+) plus leads not scored yet. Low (<60) is never mixed in.
  const range = tier ? TIERS[tier] : GOOD_LEADS;

  const filters: LeadFilters = {
    page: Number(get("page") ?? 1),
    page_size: 25,
    sort: get("sort") ?? "-created_at",
    stage: get("stage"),
    status: get("status") ? [get("status")!] : undefined,
    // A single source picked in the dropdown wins; otherwise the family tab sends all its sources.
    source: get("source") ? [get("source")!] : family ? familySources(family) : undefined,
    country: get("country") ? [get("country")!] : undefined,
    language: get("language") ? [get("language")!] : undefined,
    service: get("service") ? [get("service")!] : undefined,
    trigger: get("trigger") ? [get("trigger")!] : undefined,
    min_score: range.min,
    max_score: range.max,
    unscored: range.unscored,
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
  const sourceOptions = useMemo(() => {
    // Inside a family tab, the "Found on" dropdown only offers that family's sources.
    const allowed = family ? new Set(familySources(family)) : null;
    return Object.fromEntries(
      (sources ?? []).filter((s) => !allowed || allowed.has(s.key)).map((s) => [s.key, shortSource(s.label)]),
    );
  }, [sources, family]);
  const sourceNames = useMemo(
    () => Object.fromEntries((sources ?? []).map((s) => [s.key, shortSource(s.label)])),
    [sources],
  );
  const columns = useMemo(() => makeColumns(sourceNames), [sourceNames]);

  const advancedCount = ADVANCED.filter((k) => params.get(k)).length;
  const anythingChanged = advancedCount > 0 || Boolean(family || tier || get("q"));
  const [showMore, setShowMore] = useState(advancedCount > 0);

  // One plain sentence that says exactly what is on screen.
  const tierInfo = tier ? TIERS[tier] : GOOD_LEADS;
  const familyInfo = family ? FAMILY_INFO[family] : ALL_FAMILIES;
  const total = data?.total;
  const summary =
    total === undefined
      ? "Loading leads…"
      : `Showing ${total} ${total === 1 ? tierInfo.one : tierInfo.many} ${familyInfo.phrase}` +
        (advancedCount || get("q") ? ", narrowed by your filters." : ".");

  const startOver = () => {
    setSearch("");
    setShowMore(false);
    router.replace(pathname, { scroll: false });
  };

  return (
    <>
      <PageHeader
        title="Leads"
        description="People and businesses who may need a website, logo or SEO. Choose where they were found, then how interested they are."
      />

      <div className="mb-5 space-y-3">
        <SourcePicker family={family} onChange={(f) => setParam({ family: f, source: undefined })} />
        <InterestPicker tier={tier} onChange={(t) => setParam({ tier: t })} />
      </div>

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="relative w-full sm:w-72">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden />
          <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search by name, website or words in the post" className="h-8 bg-surface-raised pl-8" aria-label="Search leads" />
        </div>
        <Button
          variant="outline"
          size="sm"
          aria-expanded={showMore}
          aria-controls="more-filters"
          onClick={() => setShowMore((v) => !v)}
        >
          <SlidersHorizontal className="size-3.5" />
          More filters{advancedCount > 0 ? ` (${advancedCount})` : ""}
          <ChevronDown className={cn("size-3.5 transition-transform", showMore && "rotate-180")} />
        </Button>
        {anythingChanged && (
          <Button variant="ghost" size="sm" onClick={startOver}>
            <RotateCcw className="size-3.5" /> Start over
          </Button>
        )}
      </div>

      {showMore && (
        <div id="more-filters" className="mb-3 flex flex-wrap gap-3 rounded-lg border bg-surface-raised/50 p-3">
          <FilterSelect label="What they need" allLabel="Anything" value={filters.service?.[0]} options={SERVICES} onChange={(v) => setParam({ service: v })} />
          <FilterSelect label="Country" allLabel="All countries" value={filters.country?.[0]} options={Object.fromEntries(countryCodes.map((c) => [c, countryName(c)]))} onChange={(v) => setParam({ country: v })} />
          <FilterSelect label="Language" allLabel="All languages" value={filters.language?.[0]} options={Object.fromEntries(LANGUAGES.map((l) => [l, languageName(l)]))} onChange={(v) => setParam({ language: v })} />
          <FilterSelect label="Found on" allLabel="Any site or list" value={get("source")} options={sourceOptions} onChange={(v) => setParam({ source: v })} />
          <FilterSelect label="Why we picked them" allLabel="Any reason" value={filters.trigger?.[0]} options={TRIGGERS} onChange={(v) => setParam({ trigger: v })} />
          <FilterSelect label="Progress" allLabel="Any step" value={filters.stage} options={STAGES} onChange={(v) => setParam({ stage: v, status: undefined })} />
        </div>
      )}

      <div className="mb-3">
        <p className="text-sm font-medium" aria-live="polite">{summary}</p>
        <p className="text-xs text-muted-foreground">{tierInfo.hint}</p>
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
            advancedCount > 0 || get("q") ? (
              <EmptyState
                icon={Users}
                title="No leads match these filters"
                description="Remove a filter, or press Start over to see everything again."
              />
            ) : tier === "low" ? (
              <EmptyState
                icon={Users}
                title="Nothing here right now"
                description="Leads that score below 60 will be listed here, away from the good ones."
              />
            ) : tier === "unscored" ? (
              <EmptyState
                icon={Users}
                title="Every lead has a score"
                description="New leads wait here for a moment until the system scores them."
              />
            ) : (
              <EmptyState
                icon={Users}
                title={tier ? `No ${tierInfo.many} yet` : "No good leads yet"}
                description="When a source finds someone who clearly wants a website, logo or SEO, they appear here. Make sure your sources are turned on."
                action={<Button asChild size="sm"><Link href="/sources">Check sources</Link></Button>}
              />
            )
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
