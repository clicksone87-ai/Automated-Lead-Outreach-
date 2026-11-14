/** Visual vocabulary for statuses — one place so every page speaks the same language. */

export type Tone = "neutral" | "accent" | "success" | "warning" | "danger" | "info";

export const LEAD_STATUS: Record<string, { label: string; tone: Tone }> = {
  discovered: { label: "Discovered", tone: "neutral" },
  qualified: { label: "Qualified", tone: "accent" },
  rejected_low_intent: { label: "Low intent", tone: "neutral" },
  parked_language: { label: "Language parked", tone: "warning" },
  duplicate: { label: "Duplicate", tone: "neutral" },
  cooldown: { label: "Cooldown", tone: "warning" },
  suppressed: { label: "Suppressed", tone: "danger" },
  no_contact_route: { label: "No contact route", tone: "warning" },
  contact_found: { label: "Contact found", tone: "accent" },
  audited: { label: "Audited", tone: "accent" },
  branded: { label: "Branded", tone: "accent" },
  built: { label: "Preview built", tone: "accent" },
  qa_passed: { label: "QA passed", tone: "success" },
  needs_review: { label: "Needs review", tone: "warning" },
  awaiting_approval: { label: "Awaiting approval", tone: "info" },
  approved: { label: "Approved", tone: "success" },
  rejected_by_human: { label: "Rejected", tone: "neutral" },
  sent: { label: "Sent", tone: "info" },
  replied: { label: "Replied", tone: "success" },
  won: { label: "Won", tone: "success" },
  lost: { label: "Lost", tone: "neutral" },
  closed: { label: "Closed", tone: "neutral" },
  failed: { label: "Failed", tone: "danger" },
  paused_budget: { label: "Paused (budget)", tone: "warning" },
};

export const AGENT_STATE: Record<string, { label: string; tone: Tone }> = {
  running: { label: "Running", tone: "accent" },
  idle: { label: "Idle", tone: "neutral" },
  failed: { label: "Failed", tone: "danger" },
  needs_api_key: { label: "Needs API key", tone: "warning" },
  paused_budget: { label: "Paused (budget)", tone: "warning" },
};

export const SOURCE_STATUS: Record<string, { label: string; tone: Tone }> = {
  ok: { label: "Healthy", tone: "success" },
  error: { label: "Error", tone: "danger" },
  needs_api_key: { label: "Needs API key", tone: "warning" },
  paused_budget: { label: "Paused (budget)", tone: "warning" },
};

export const TRIGGERS: Record<string, string> = {
  explicit_request: "Explicit request",
  new_business: "New business",
  no_website: "No website",
  broken_or_outdated_site: "Broken / outdated site",
  profile_without_site: "Profile without site",
};

export const SERVICES: Record<string, string> = {
  logo: "Logo",
  website: "New website",
  redesign: "Redesign",
  seo: "SEO",
};

export const AGENT_LABELS: Record<string, string> = {
  discovery: "Discovery",
  requirements: "Requirements",
  contact_enrichment: "Contact enrichment",
  audit: "Audit & SEO",
  branding: "Branding",
  website_builder: "Website builder",
  quality_check: "Quality check",
  proposal: "Proposal",
  outreach: "Outreach",
};

export function leadStatus(status: string) {
  return LEAD_STATUS[status] ?? { label: status, tone: "neutral" as Tone };
}
