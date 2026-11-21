"use client";

import { Globe2, MapPinned, Search } from "lucide-react";
import { useState } from "react";
import { PageHeader } from "@/components/common/page-header";
import { Panel, PanelHeader } from "@/components/common/panel";
import { EmptyState, ErrorState } from "@/components/common/states";
import { StatusPill } from "@/components/common/status-pill";
import type { Tone } from "@/lib/status";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import {
  type DiscoveryJob,
  type SweepCityStatus,
  useDiscoveryJob,
  useDiscoveryJobs,
  useDiscoveryOptions,
  useStartSearch,
  useSweepStatus,
} from "@/lib/discovery";

const WEBSITE: Record<string, { label: string; tone: Tone }> = {
  OSM_WEBSITE_MISSING: { label: "No website on OSM", tone: "warning" },
  WEBSITE_RECORDED: { label: "Website recorded", tone: "neutral" },
  WEBSITE_DISCOVERED: { label: "Website discovered", tone: "info" },
  WEBSITE_VERIFIED: { label: "Website verified", tone: "success" },
  WEBSITE_UNCONFIRMED: { label: "Website unconfirmed", tone: "neutral" },
};

const JOB_TONE: Record<DiscoveryJob["status"], Tone> = {
  queued: "neutral",
  running: "info",
  done: "success",
  failed: "danger",
};

function OsmAttribution({ text }: { text: string }) {
  return (
    <p className="text-xs text-muted-foreground">
      Map data{" "}
      <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer" className="underline">
        {text}
      </a>
    </p>
  );
}

const SWEEP_TONE: Record<SweepCityStatus, Tone> = {
  pending: "neutral",
  running: "info",
  done: "success",
  failed: "danger",
};
const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function num(v: number | null | undefined) {
  return typeof v === "number" ? v.toLocaleString() : "—";
}

function SweepAttribution({ geonames, osm }: { geonames: string; osm: string }) {
  return (
    <p className="text-xs text-muted-foreground">
      Cities:{" "}
      <a href="https://www.geonames.org/" target="_blank" rel="noopener noreferrer" className="underline">
        {geonames}
      </a>
      {" · "}Map data{" "}
      <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer" className="underline">
        {osm}
      </a>
    </p>
  );
}

function SweepPanel() {
  const { data, isLoading, isError, error, refetch } = useSweepStatus();
  if (isError) return <ErrorState error={error} onRetry={() => refetch()} />;
  if (isLoading || !data) return <Skeleton className="mb-6 h-40 rounded-lg" />;
  const t = data.totals;
  const started = data.week.filter((w) => w.cities !== null).length;
  return (
    <Panel className="mb-6">
      <PanelHeader
        title="Weekly world sweep"
        description={`Every day one seventh of the countries: the ${data.settings.cities_per_country} largest cities each, businesses within ${data.settings.radius_m / 1000} km on OpenStreetMap.`}
        action={<Globe2 className="size-4 text-muted-foreground" aria-hidden />}
      />
      <div className="space-y-5 p-5">
        {!data.enabled && <p className="text-sm text-muted-foreground">The sweep is switched off (OSM_SWEEP_ENABLED).</p>}
        {data.geonames.cities === 0 && (
          <p className="text-sm text-muted-foreground">The city list is not loaded yet; the first sweep run downloads it.</p>
        )}
        <div className="grid gap-3 sm:grid-cols-4">
          {(["done", "failed", "pending", "running"] as const).map((k) => (
            <div key={k} className="rounded-lg border p-3">
              <p className="text-xs capitalize text-muted-foreground">Cities {k} today</p>
              <p className="tabular text-xl font-semibold">{num(t[k])}</p>
            </div>
          ))}
        </div>
        <div>
          <p className="mb-2 text-xs text-muted-foreground">
            {WEEKDAYS[data.weekday]} {data.day}: {data.today_countries.length} countries
            {data.backlog_pending > 0 && ` · ${data.backlog_pending} cities from earlier days waiting for a retry`}
          </p>
          <div className="flex flex-wrap gap-1.5">
            {data.today_countries.map((c) => (
              <span key={c} className="rounded border px-1.5 py-0.5 font-mono text-xs">
                {c}
              </span>
            ))}
          </div>
        </div>
        {data.cities.length > 0 && (
          <div className="max-h-80 overflow-auto rounded-lg border">
            <table className="w-full text-left text-sm">
              <thead className="sticky top-0 bg-muted/90 text-xs text-muted-foreground">
                <tr>
                  <th className="px-3 py-2 font-medium">City</th>
                  <th className="px-3 py-2 font-medium">Status</th>
                  <th className="px-3 py-2 font-medium">Businesses</th>
                  <th className="px-3 py-2 font-medium">No website</th>
                  <th className="px-3 py-2 font-medium">Leads</th>
                  <th className="px-3 py-2 font-medium">Note</th>
                </tr>
              </thead>
              <tbody>
                {data.cities.map((c) => (
                  <tr key={`${c.country}-${c.city}`} className="border-t">
                    <td className="px-3 py-2">
                      <span className="font-medium">{c.city}</span>{" "}
                      <span className="font-mono text-xs text-muted-foreground">{c.country}</span>
                    </td>
                    <td className="px-3 py-2">
                      <StatusPill tone={SWEEP_TONE[c.status]}>{c.status}</StatusPill>
                    </td>
                    <td className="px-3 py-2 tabular">{num(c.counts.found)}</td>
                    <td className="px-3 py-2 tabular">{num(c.counts.no_website)}</td>
                    <td className="px-3 py-2 tabular">{num(c.counts.leads)}</td>
                    <td className="max-w-xs truncate px-3 py-2 text-xs text-muted-foreground" title={c.error ?? undefined}>
                      {c.error
                        ? `${c.attempts} attempt${c.attempts === 1 ? "" : "s"}: ${c.error}`
                        : c.attempts > 1
                          ? `${c.attempts} attempts`
                          : ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div>
          <p className="mb-2 text-xs text-muted-foreground">
            This week (from {data.week_start}): {started} of {data.week.length} countries started. Businesses = seen on
            OpenStreetMap this week; — = not known yet.
          </p>
          <div className="max-h-72 overflow-auto rounded-lg border">
            <table className="w-full text-left text-sm">
              <thead className="sticky top-0 bg-muted/90 text-xs text-muted-foreground">
                <tr>
                  <th className="px-3 py-2 font-medium">Country</th>
                  <th className="px-3 py-2 font-medium">Day</th>
                  <th className="px-3 py-2 font-medium">Cities done</th>
                  <th className="px-3 py-2 font-medium">Businesses</th>
                </tr>
              </thead>
              <tbody>
                {data.week.map((w) => (
                  <tr key={w.country} className={`border-t ${w.weekday === data.weekday ? "bg-muted/40" : ""}`}>
                    <td className="px-3 py-1.5 font-mono text-xs">{w.country}</td>
                    <td className="px-3 py-1.5 text-xs text-muted-foreground">{WEEKDAYS[w.weekday]}</td>
                    <td className="px-3 py-1.5 tabular">{w.cities === null ? "—" : `${num(w.done)} / ${num(w.cities)}`}</td>
                    <td className="px-3 py-1.5 tabular">{num(w.businesses)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
        <SweepAttribution geonames={data.attribution.geonames} osm={data.attribution.osm} />
      </div>
    </Panel>
  );
}

function SearchForm() {
  const { data: opts } = useDiscoveryOptions();
  const start = useStartSearch();
  const [country, setCountry] = useState("GB");
  const [city, setCity] = useState("");
  const [category, setCategory] = useState("plumber");
  const [max, setMax] = useState(50);
  const code = country.trim().toUpperCase();
  const excluded = opts?.excluded_countries.includes(code) ?? false;
  const route = opts ? (opts.routes[code] ?? opts.default_route) : [];
  return (
    <Panel className="mb-6">
      <PanelHeader
        title="New search"
        description="Businesses in one city and category, from OpenStreetMap and free national registries."
        action={<Search className="size-4 text-muted-foreground" aria-hidden />}
      />
      <form
        className="grid gap-4 p-5 sm:grid-cols-2 lg:grid-cols-5 lg:items-end"
        onSubmit={(e) => {
          e.preventDefault();
          if (!excluded && city.trim()) start.mutate({ country: code, city: city.trim(), category, max_results: max });
        }}
      >
        <div className="space-y-1.5">
          <Label htmlFor="bs-country">Country (ISO code)</Label>
          <Input id="bs-country" value={country} maxLength={2} onChange={(e) => setCountry(e.target.value)} aria-invalid={excluded} />
          {excluded && <p className="text-xs text-destructive">{code} is an excluded market.</p>}
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="bs-city">City</Label>
          <Input id="bs-city" value={city} placeholder="e.g. Leeds" onChange={(e) => setCity(e.target.value)} required />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="bs-category">Category</Label>
          <select
            id="bs-category"
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            className="h-9 w-full rounded-md border bg-background px-3 text-sm"
          >
            {(opts?.categories ?? [category]).map((c) => (
              <option key={c} value={c}>
                {c.replace("_", " ")}
              </option>
            ))}
          </select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="bs-max">Max results</Label>
          <Input id="bs-max" type="number" min={1} max={200} value={max} onChange={(e) => setMax(Number(e.target.value) || 50)} />
        </div>
        <Button type="submit" disabled={start.isPending || excluded || !city.trim()}>
          <Search className="size-4" /> Search
        </Button>
        <p className="text-xs text-muted-foreground sm:col-span-2 lg:col-span-5">
          Sources for {code || "this country"}: {route.join(", ") || "OpenStreetMap"}.
        </p>
      </form>
    </Panel>
  );
}

function JobResults({ jobId }: { jobId: string }) {
  const { data, isLoading, isError, error, refetch } = useDiscoveryJob(jobId);
  if (isError) return <ErrorState error={error} onRetry={() => refetch()} />;
  if (isLoading || !data) return <Skeleton className="h-40 rounded-lg" />;
  const osm = data.results.some((r) => r.is_osm);
  return (
    <div className="space-y-3">
      {data.errors.length > 0 && (
        <ul className="rounded-lg border border-destructive/40 p-3 text-xs text-destructive">
          {data.errors.map((e) => (
            <li key={e}>{e}</li>
          ))}
        </ul>
      )}
      {data.results.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          {data.status === "done" ? "No businesses found." : "Searching…"}
        </p>
      ) : (
        <div className="overflow-x-auto rounded-lg border">
          <table className="w-full text-left text-sm">
            <thead className="bg-muted/50 text-xs text-muted-foreground">
              <tr>
                <th className="px-3 py-2 font-medium">Business</th>
                <th className="px-3 py-2 font-medium">Website</th>
                <th className="px-3 py-2 font-medium">Address</th>
                <th className="px-3 py-2 font-medium">Phone</th>
                <th className="px-3 py-2 font-medium">Source</th>
              </tr>
            </thead>
            <tbody>
              {data.results.map((r) => {
                const w = WEBSITE[r.website_status ?? ""] ?? { label: r.website_status ?? "Unknown", tone: "neutral" as const };
                return (
                  <tr key={r.id} className="border-t">
                    <td className="px-3 py-2 font-medium">{r.name}</td>
                    <td className="px-3 py-2">
                      <StatusPill tone={w.tone}>{w.label}</StatusPill>
                      {r.domain && <span className="ml-2 text-xs text-muted-foreground">{r.domain}</span>}
                    </td>
                    <td className="px-3 py-2 text-muted-foreground">{r.address ?? "—"}</td>
                    <td className="px-3 py-2 tabular text-muted-foreground">{r.phone ?? "—"}</td>
                    <td className="px-3 py-2 text-xs text-muted-foreground">{r.registry_source}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {(osm || data.attribution) && <OsmAttribution text={data.attribution ?? "© OpenStreetMap contributors"} />}
    </div>
  );
}

export default function BusinessSearchPage() {
  const { data: jobs, isLoading, isError, error, refetch } = useDiscoveryJobs();
  const [open, setOpen] = useState<string | null>(null);
  const selected = open ?? jobs?.[0]?.id ?? null;
  return (
    <>
      <PageHeader
        title="Business search"
        description="Find businesses with no website or an outdated one. Excluded markets are never searched or stored."
      />
      <SweepPanel />
      <SearchForm />
      {isError ? (
        <ErrorState error={error} onRetry={() => refetch()} />
      ) : isLoading ? (
        <Skeleton className="h-40 rounded-lg" />
      ) : !jobs?.length ? (
        <EmptyState icon={MapPinned} title="No searches yet" description="Start one above." />
      ) : (
        <div className="grid gap-6 lg:grid-cols-[18rem_1fr]">
          <ul className="space-y-2" aria-label="Searches">
            {jobs.map((j) => (
              <li key={j.id}>
                <button
                  type="button"
                  onClick={() => setOpen(j.id)}
                  className={`w-full rounded-lg border p-3 text-left text-sm hover:bg-muted ${selected === j.id ? "border-primary" : ""}`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-medium">
                      {j.category.replace("_", " ")} · {j.city}, {j.country}
                    </span>
                    <StatusPill tone={JOB_TONE[j.status]}>{j.status}</StatusPill>
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {typeof j.counts.found === "number" ? `${j.counts.found} found` : "waiting"} · {j.sources.join(", ")}
                  </p>
                </button>
              </li>
            ))}
          </ul>
          {selected && <JobResults jobId={selected} />}
        </div>
      )}
    </>
  );
}
