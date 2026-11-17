"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { API_URL } from "./api";

/** Weekly reports (learning loop) and monitoring endpoints. */

export interface DimRow {
  key: string;
  discovered: number;
  qualified: number;
  sent: number;
  sandbox_sent: number;
  replied: number;
  positive: number;
  won: number;
  reply_rate: number;
  positive_rate: number;
}

export interface ReportData {
  period_start: string;
  period_end: string;
  funnel: Omit<DimRow, "key" | "reply_rate" | "positive_rate">;
  by: Record<"source" | "service" | "industry" | "country" | "trigger" | "template", DimRow[]>;
  note: string;
}

export interface Adjustment {
  dimension: string;
  key: string;
  from: number;
  to: number;
  reply_rate: number;
  baseline: number;
  sent: number;
}

export interface SavedReport {
  id: string;
  period_start: string;
  period_end: string;
  data: ReportData;
  adjustments: Adjustment[];
  created_at: string;
}

export interface Monitoring {
  status: "ok" | "attention";
  heartbeats: { worker?: { last: string | null; alive: boolean }; scheduler?: { last: string | null; alive: boolean }; queue_depth?: number; error?: string };
  agent_failures_24h: Record<string, number>;
  recent_failures: { agent: string; lead_id: string | null; at: string | null; error: string }[];
  sources_unhealthy: { key: string; status: string; error: string }[];
  quotas_over_80pct: { scope: string; used: number; cap: number; unit: string }[];
  cache: Record<string, { entries: number; hits: number }>;
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const token = process.env.NEXT_PUBLIC_API_TOKEN;
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
  });
  if (!res.ok) throw new Error(res.statusText);
  return (await res.json()) as T;
}

export function useLiveReport(days = 7) {
  return useQuery({ queryKey: ["report-live", days], queryFn: () => call<ReportData>(`/api/reports/live?days=${days}`), refetchInterval: 60_000 });
}

export function useSavedReports() {
  return useQuery({ queryKey: ["reports"], queryFn: () => call<SavedReport[]>("/api/reports") });
}

export function useMonitoring() {
  return useQuery({ queryKey: ["monitoring"], queryFn: () => call<Monitoring>("/api/monitoring"), refetchInterval: 30_000 });
}

export function useRunReport() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => call<{ id: string; adjustments: Adjustment[] }>("/api/reports/run", { method: "POST" }),
    onSuccess: (r) => {
      toast.success(r.adjustments.length ? `Report saved — ${r.adjustments.length} adjustment(s)` : "Report saved — no adjustments (not enough data yet)");
      qc.invalidateQueries({ queryKey: ["reports"] });
    },
    onError: (e: Error) => toast.error(e.message),
  });
}
