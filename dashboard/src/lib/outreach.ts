"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { API_URL } from "./api";

/** Approvals, inbox, deals, suppressions and autonomy endpoints. */

export interface Claim {
  text: string;
  evidence_id: string;
  source?: { title?: string; detail?: string; severity?: string };
}

export interface Attachment {
  kind: string;
  path: string;
  filename: string;
  content_type?: string;
}

export interface ProposalItem {
  id: string;
  lead_id: string;
  business_name: string | null;
  lead_status: string;
  country: string | null;
  language: string;
  version: number;
  subject: string;
  body_html: string;
  body_text: string;
  draft: Record<string, string | null>;
  claims: Claim[];
  attachments: Attachment[];
  to_email: string | null;
  approval_status: string;
  edited_by_human: boolean;
  created_at: string;
}

export interface InboxReply {
  id: string;
  from_email: string;
  subject: string | null;
  received_at: string;
  classification: string | null;
  reason: string | null;
  text: string;
}

export interface Deal {
  quote: Record<string, unknown>;
  contract_status: string;
  payment_status: string;
  delivery_status: string;
  notes: string | null;
}

export interface InboxItem {
  lead_id: string;
  business_name: string | null;
  lead_status: string;
  country: string | null;
  language: string | null;
  replies: InboxReply[];
  deal: Deal | null;
}

export interface SuppressionItem {
  id: string;
  email: string | null;
  domain: string | null;
  business_id: string | null;
  reason: string;
  created_at: string;
}

export interface ManualOutreachItem {
  id: string;
  lead_id: string;
  business_name: string | null;
  channel: string;
  post_link: string | null;
  comment: string;
  dm: string;
  contact_form: string;
  status: string;
  done_at: string | null;
  done_by: string | null;
  created_at: string;
}

export interface AutonomyCondition {
  key: string;
  label: string;
  met: boolean;
  value: string;
}

export interface AutonomyStatus {
  stage: number;
  can_move_up: boolean;
  next_stage: number | null;
  conditions: AutonomyCondition[];
  sandbox_only: boolean;
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
      if (body.detail) detail = String(body.detail);
    } catch {
      /* keep statusText */
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

const LIVE = { refetchInterval: 15_000 };

export function useApprovals(state = "pending") {
  return useQuery({ queryKey: ["approvals", state], queryFn: () => call<ProposalItem[]>(`/api/approvals?state=${state}`), ...LIVE });
}

export function useInbox() {
  return useQuery({ queryKey: ["inbox"], queryFn: () => call<InboxItem[]>("/api/inbox"), ...LIVE });
}

export function useSuppressions() {
  return useQuery({ queryKey: ["suppressions"], queryFn: () => call<SuppressionItem[]>("/api/suppressions") });
}

export function useManualOutreach(status = "pending") {
  return useQuery({
    queryKey: ["manual-outreach", status],
    queryFn: () => call<ManualOutreachItem[]>(`/api/manual-outreach?status=${status}`),
    ...LIVE,
  });
}

export function useManualOutreachDone() {
  const done = useInvalidate([["manual-outreach"], ["overview"], ["leads"]]);
  return useMutation({
    mutationFn: (id: string) =>
      call<ManualOutreachItem>(`/api/manual-outreach/${id}/done`, {
        method: "POST",
        body: JSON.stringify({ done_by: "dashboard" }),
      }),
    onSuccess: () => {
      toast.success("Manual outreach marked done");
      done();
    },
    onError: (e: Error) => toast.error("Could not update manual outreach", { description: e.message }),
  });
}

export function useManualOutreachSkip() {
  const done = useInvalidate([["manual-outreach"], ["overview"], ["leads"]]);
  return useMutation({
    mutationFn: (id: string) =>
      call<ManualOutreachItem>(`/api/manual-outreach/${id}/skip`, {
        method: "POST",
        body: JSON.stringify({ done_by: "dashboard" }),
      }),
    onSuccess: () => {
      toast.success("Manual outreach skipped");
      done();
    },
    onError: (e: Error) => toast.error("Could not update manual outreach", { description: e.message }),
  });
}

export function useAutonomy() {
  return useQuery({ queryKey: ["autonomy"], queryFn: () => call<AutonomyStatus>("/api/autonomy"), ...LIVE });
}

function useInvalidate(keys: string[][]) {
  const qc = useQueryClient();
  return () => keys.forEach((k) => qc.invalidateQueries({ queryKey: k }));
}

export function useApprove() {
  const done = useInvalidate([["approvals"], ["overview"], ["leads"]]);
  return useMutation({
    mutationFn: (id: string) => call<ProposalItem & { queued_for_sending: boolean }>(`/api/proposals/${id}/approve`, { method: "POST", body: JSON.stringify({ approved_by: "dashboard" }) }),
    onSuccess: (p) => {
      toast.success(p.queued_for_sending ? "Approved — queued for sending (Mailpit in testing mode)" : "Approved");
      done();
    },
    onError: (e: Error) => toast.error(e.message),
  });
}

export function useReject() {
  const done = useInvalidate([["approvals"], ["overview"], ["leads"]]);
  return useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) => call<ProposalItem>(`/api/proposals/${id}/reject`, { method: "POST", body: JSON.stringify({ reason }) }),
    onSuccess: () => {
      toast.success("Rejected");
      done();
    },
    onError: (e: Error) => toast.error(e.message),
  });
}

export function useEditProposal() {
  const done = useInvalidate([["approvals"]]);
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: Record<string, string> }) => call<ProposalItem>(`/api/proposals/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),
    onSuccess: () => {
      toast.success("Draft updated");
      done();
    },
    onError: (e: Error) => toast.error(e.message),
  });
}

export function usePatchDeal() {
  const done = useInvalidate([["inbox"]]);
  return useMutation({
    mutationFn: ({ leadId, patch }: { leadId: string; patch: Partial<Deal> }) => call<Deal>(`/api/deals/${leadId}`, { method: "PATCH", body: JSON.stringify(patch) }),
    onSuccess: () => {
      toast.success("Deal updated");
      done();
    },
    onError: (e: Error) => toast.error(e.message),
  });
}

export function useAddSuppression() {
  const done = useInvalidate([["suppressions"]]);
  return useMutation({
    mutationFn: (body: { email?: string; domain?: string; reason: string }) => call<SuppressionItem>("/api/suppressions", { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => {
      toast.success("Added to the suppression list (permanent)");
      done();
    },
    onError: (e: Error) => toast.error(e.message),
  });
}

export function useSetStage() {
  const done = useInvalidate([["autonomy"], ["overview"], ["settings"]]);
  return useMutation({
    mutationFn: (body: { stage: number; reason?: string }) => call<AutonomyStatus>("/api/autonomy", { method: "POST", body: JSON.stringify(body) }),
    onSuccess: (s) => {
      toast.success(`Autonomy is now stage ${s.stage}`);
      done();
    },
    onError: (e: Error) => toast.error(e.message),
  });
}

