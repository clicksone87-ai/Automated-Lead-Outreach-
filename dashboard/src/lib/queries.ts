"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, unwrap } from "./api";
import type { Schemas } from "./api";

export const qk = {
  health: ["health"] as const,
  overview: ["overview"] as const,
  leads: (params: LeadFilters) => ["leads", params] as const,
  lead: (id: string) => ["lead", id] as const,
  sources: ["sources"] as const,
  settings: ["settings"] as const,
  costs: (days: number) => ["costs", days] as const,
  activity: ["activity"] as const,
  search: (q: string) => ["search", q] as const,
  audits: (page: number) => ["audits", page] as const,
  audit: (leadId: string) => ["audit", leadId] as const,
  freeTier: ["free-tier"] as const,
  gallery: ["gallery"] as const,
};

export interface LeadFilters {
  page: number;
  page_size: number;
  sort: string;
  status?: string[];
  stage?: string;
  source?: string[];
  country?: string[];
  language?: string[];
  service?: string[];
  trigger?: string[];
  min_score?: number;
  max_score?: number;
  unscored?: "include" | "only";
  q?: string;
}

const LIVE = { refetchInterval: 15_000 };

export function useHealth() {
  return useQuery({
    queryKey: qk.health,
    queryFn: () => unwrap(api.GET("/api/health")),
    refetchInterval: 10_000,
    retry: false,
  });
}

export function useOverview() {
  return useQuery({ queryKey: qk.overview, queryFn: () => unwrap(api.GET("/api/overview")), ...LIVE });
}

export function useLeads(filters: LeadFilters) {
  return useQuery({
    queryKey: qk.leads(filters),
    queryFn: () => unwrap(api.GET("/api/leads", { params: { query: filters } })),
    placeholderData: keepPreviousData,
    ...LIVE,
  });
}

export function useLead(id: string | null) {
  return useQuery({
    queryKey: qk.lead(id ?? ""),
    queryFn: () => unwrap(api.GET("/api/leads/{lead_id}", { params: { path: { lead_id: id! } } })),
    enabled: Boolean(id),
    ...LIVE,
  });
}

export function useSources() {
  return useQuery({ queryKey: qk.sources, queryFn: () => unwrap(api.GET("/api/sources")), ...LIVE });
}

export function useFreeTier() {
  return useQuery({ queryKey: qk.freeTier, queryFn: () => unwrap(api.GET("/api/free-tier")), refetchInterval: 30_000 });
}

export function useGallery() {
  return useQuery({ queryKey: qk.gallery, queryFn: () => unwrap(api.GET("/api/gallery")), ...LIVE });
}

export function useSettings() {
  return useQuery({ queryKey: qk.settings, queryFn: () => unwrap(api.GET("/api/settings")) });
}

export function useCosts(days = 30) {
  return useQuery({
    queryKey: qk.costs(days),
    queryFn: () => unwrap(api.GET("/api/costs", { params: { query: { days } } })),
    ...LIVE,
  });
}

export function useActivity() {
  return useQuery({
    queryKey: qk.activity,
    queryFn: () => unwrap(api.GET("/api/activity", { params: { query: { limit: 60 } } })),
    ...LIVE,
  });
}

export function useSearch(q: string) {
  return useQuery({
    queryKey: qk.search(q),
    queryFn: () => unwrap(api.GET("/api/search", { params: { query: { q } } })),
    enabled: q.trim().length >= 2,
    staleTime: 10_000,
  });
}

export function useAudits(page = 1) {
  return useQuery({
    queryKey: qk.audits(page),
    queryFn: () => unwrap(api.GET("/api/audits", { params: { query: { page, page_size: 24 } } })),
    placeholderData: keepPreviousData,
    ...LIVE,
  });
}

export function useAudit(leadId: string) {
  return useQuery({
    queryKey: qk.audit(leadId),
    queryFn: () => unwrap(api.GET("/api/audits/{lead_id}", { params: { path: { lead_id: leadId } } })),
    retry: (count, err) => !(err instanceof Error && /not found/i.test(err.message)) && count < 1,
  });
}

function errorToast(title: string) {
  return (err: Error) => toast.error(title, { description: err.message });
}

export function usePatchSource() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ key, patch }: { key: string; patch: Schemas["SourcePatch"] }) =>
      unwrap(api.PATCH("/api/sources/{key}", { params: { path: { key } }, body: patch })),
    onSuccess: (data) => {
      qc.setQueryData(qk.sources, data);
    },
    onError: errorToast("Could not update source"),
  });
}

export function useRunSource() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (key: string) =>
      unwrap(api.POST("/api/sources/{key}/run", { params: { path: { key } } })),
    onSuccess: (_d, key) => toast.success("Run queued", { description: `Source "${key}" will run shortly.` }),
    onError: errorToast("Run not started"),
    onSettled: () => qc.invalidateQueries({ queryKey: qk.sources }),
  });
}

export function usePatchSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Schemas["SettingsPatch"]) => unwrap(api.PATCH("/api/settings", { body })),
    onSuccess: (data) => {
      qc.setQueryData(qk.settings, data);
      qc.invalidateQueries({ queryKey: qk.overview });
      toast.success("Settings saved");
    },
    onError: errorToast("Could not save settings"),
  });
}

export function useRerunLead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      unwrap(api.POST("/api/leads/{lead_id}/rerun", { params: { path: { lead_id: id } } })),
    onSuccess: (_d, id) => {
      toast.success("Pipeline re-run queued");
      qc.invalidateQueries({ queryKey: qk.lead(id) });
    },
    onError: errorToast("Could not queue re-run"),
  });
}
