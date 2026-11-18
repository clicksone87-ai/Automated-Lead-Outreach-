"use client";

import { Check, Copy, ExternalLink } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Panel, PanelHeader } from "@/components/common/panel";
import { Button } from "@/components/ui/button";
import type { LeadDetail } from "@/lib/api";
import { titleCase } from "@/lib/format";

export type TrackB = {
  channel: string;
  route: string | null;
  comment: string;
  dm: string;
  contact_form: string;
};

const str = (v: unknown): string => (typeof v === "string" ? v : "");

/** Track B drafts of the latest proposal, or null when it went to an email route. */
export function latestTrackB(proposals: LeadDetail["proposals"]): TrackB | null {
  const latest = [...proposals].sort((a, b) => Number(b.version ?? 0) - Number(a.version ?? 0))[0];
  const draft = latest?.draft;
  if (!draft || typeof draft !== "object") return null;
  const tb = (draft as Record<string, unknown>).track_b;
  if (!tb || typeof tb !== "object") return null;
  const t = tb as Record<string, unknown>;
  const out = { channel: str(t.channel), route: str(t.route) || null, comment: str(t.comment), dm: str(t.dm), contact_form: str(t.contact_form) };
  return out.comment || out.dm || out.contact_form ? out : null;
}

const DRAFTS: { key: "comment" | "dm" | "contact_form"; title: string; hint: string }[] = [
  { key: "comment", title: "Post comment", hint: "Public reply under their post" },
  { key: "dm", title: "Direct message", hint: "Private message on the same platform" },
  { key: "contact_form", title: "Contact form", hint: "Paste into their website's form" },
];

function CopyButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <Button
      variant="outline"
      size="sm"
      aria-label={`Copy ${label}`}
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setCopied(true);
          toast.success(`${label} copied`);
          setTimeout(() => setCopied(false), 1500);
        } catch {
          toast.error("Could not copy — select the text and copy it by hand");
        }
      }}
    >
      {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />} {copied ? "Copied" : "Copy draft"}
    </Button>
  );
}

function safeHref(url: string | null): string | null {
  if (!url) return null;
  try {
    const u = new URL(url);
    return u.protocol === "https:" || u.protocol === "http:" ? u.toString() : null;
  } catch {
    return null;
  }
}

export function ManualDrafts({ trackB }: { trackB: TrackB }) {
  const href = safeHref(trackB.route);
  return (
    <Panel as="div">
      <PanelHeader
        title="Manual outreach drafts"
        description={`No verified email — reach them by hand via ${titleCase(trackB.channel || "their post")}.`}
        action={
          href ? (
            <a href={href} target="_blank" rel="noopener noreferrer nofollow" className="inline-flex items-center gap-1 text-xs text-primary hover:underline">
              Open route <ExternalLink className="size-3" aria-hidden />
            </a>
          ) : undefined
        }
      />
      <div className="grid gap-3 p-4">
        {DRAFTS.filter((d) => trackB[d.key]).map((d) => (
          <div key={d.key} className="space-y-2 rounded-lg border p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <p className="text-sm font-medium">{d.title}</p>
                <p className="text-xs text-muted-foreground">
                  {d.hint} · {trackB[d.key].length} chars
                </p>
              </div>
              <CopyButton text={trackB[d.key]} label={d.title} />
            </div>
            <p className="whitespace-pre-wrap rounded-md bg-muted/60 p-3 text-sm leading-relaxed">{trackB[d.key]}</p>
          </div>
        ))}
      </div>
    </Panel>
  );
}
