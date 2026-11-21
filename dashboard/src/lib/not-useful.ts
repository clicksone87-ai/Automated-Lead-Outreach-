"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { call } from "./outreach";

/** The "Not useful" tab: leads that will never be worked, each with its reason. */

export interface NotUsefulRow {
  id: string;
  title: string | null;
  business_name: string | null;
  source: string;
  source_url: string;
  country: string | null;
  language: string | null;
  service_needed: string[];
  intent_score: number | null;
  reason: string | null;
  reason_label: string | null;
  status_reason: string | null;
  created_at: string;
  updated_at: string;
}

export interface NotUsefulPage {
  items: NotUsefulRow[];
  total: number;
  page: number;
  page_size: number;
}

export interface ReasonCount {
  reason: string;
  label: string;
  count: number;
}

export interface NotUsefulCounts {
  total: number;
  reasons: ReasonCount[];
}

export interface NotUsefulFilters {
  reason?: string;
  q?: string;
  page: number;
  page_size: number;
}

export function useNotUsefulCounts() {
  return useQuery({
    queryKey: ["not-useful", "counts"],
    queryFn: () => call<NotUsefulCounts>("/api/not-useful/counts"),
    refetchInterval: 30_000,
  });
}

export function useNotUseful(filters: NotUsefulFilters) {
  const params = new URLSearchParams({ page: String(filters.page), page_size: String(filters.page_size) });
  if (filters.reason) params.set("reason", filters.reason);
  if (filters.q) params.set("q", filters.q);
  return useQuery({
    queryKey: ["not-useful", "list", filters],
    queryFn: () => call<NotUsefulPage>(`/api/not-useful?${params.toString()}`),
    placeholderData: keepPreviousData,
    refetchInterval: 30_000,
  });
}
