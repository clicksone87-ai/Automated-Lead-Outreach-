"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { API_URL } from "./api";

/** Business search (no-website / outdated-website discovery) endpoints. */

export interface DiscoveryOptions {
  categories: string[];
  routes: Record<string, string[]>;
  default_route: string[];
  excluded_countries: string[];
  website_statuses: string[];
  osm_attribution: string;
}

export interface DiscoveryJob {
  id: string;
  country: string;
  city: string;
  category: string;
  sources: string[];
  max_results: number;
  status: "queued" | "running" | "done" | "failed";
  counts: Record<string, number | Record<string, number>>;
  errors: string[];
  attribution: string | null;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface FoundBusinessRow {
  id: string;
  name: string;
  country: string | null;
  address: string | null;
  phone: string | null;
  domain: string | null;
  website_status: string | null;
  registry_source: string | null;
  registry_number: string | null;
  is_osm: boolean;
}

export interface SearchInput {
  country: string;
  city: string;
  category: string;
  max_results: number;
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const token = process.env.NEXT_PUBLIC_API_TOKEN;
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (body.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* keep statusText */
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

export function useDiscoveryOptions() {
  return useQuery({ queryKey: ["discovery-options"], queryFn: () => call<DiscoveryOptions>("/api/discovery/options") });
}

export function useDiscoveryJobs() {
  return useQuery({
    queryKey: ["discovery-jobs"],
    queryFn: () => call<DiscoveryJob[]>("/api/discovery/jobs"),
    refetchInterval: (q) => (q.state.data?.some((j) => j.status === "queued" || j.status === "running") ? 3_000 : 30_000),
  });
}

export function useDiscoveryJob(id: string | null) {
  return useQuery({
    queryKey: ["discovery-job", id],
    queryFn: () => call<DiscoveryJob & { results: FoundBusinessRow[] }>(`/api/discovery/jobs/${id}`),
    enabled: !!id,
    refetchInterval: (q) => (q.state.data && ["queued", "running"].includes(q.state.data.status) ? 3_000 : false),
  });
}

export function useStartSearch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: SearchInput) =>
      call<DiscoveryJob>("/api/discovery/search", { method: "POST", body: JSON.stringify(input) }),
    onSuccess: () => {
      toast.success("Search started");
      qc.invalidateQueries({ queryKey: ["discovery-jobs"] });
    },
    onError: (e: Error) => toast.error("Search refused", { description: e.message }),
  });
}

/** Weekly OpenStreetMap world sweep (GET /api/discovery/sweep). */

export type SweepCityStatus = "pending" | "running" | "done" | "failed";

export interface SweepCity {
  country: string;
  city: string;
  population: number | null;
  status: SweepCityStatus;
  attempts: number;
  counts: Record<string, number>;
  error: string | null;
  mirror: string | null;
  next_attempt_at: string | null;
  finished_at: string | null;
}

export interface SweepWeekCountry {
  country: string;
  weekday: number;
  /** null = not swept yet this week (shown as unknown) */
  cities: number | null;
  done: number | null;
  failed: number | null;
  businesses: number | null;
}

export interface SweepStatus {
  enabled: boolean;
  day: string;
  weekday: number;
  week_start: string;
  today_countries: string[];
  cities: SweepCity[];
  totals: Record<SweepCityStatus, number>;
  backlog_pending: number;
  week: SweepWeekCountry[];
  geonames: { cities: number; countries: number; refreshed_at: string | null; source_url: string };
  settings: { radius_m: number; cities_per_country: number; daily_lead_cap: number; max_attempts: number };
  attribution: { geonames: string; osm: string };
}

export function useSweepStatus() {
  return useQuery({
    queryKey: ["discovery-sweep"],
    queryFn: () => call<SweepStatus>("/api/discovery/sweep"),
    refetchInterval: 60_000,
  });
}
