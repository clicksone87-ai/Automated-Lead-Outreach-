"use client";

import { Check, CircleDashed, Loader2, PackageCheck, Send, ThumbsDown, ThumbsUp, TriangleAlert, X } from "lucide-react";
import { useState } from "react";
import { StatusPill } from "@/components/common/status-pill";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";
import type { AssetKind, AssetsProgress, ManualOutreachItem } from "@/lib/outreach";

export const ASSET_LABELS: Record<AssetKind, string> = { logo: "Logo concepts", website: "Website preview" };

const STEP_LABELS: Record<string, string> = {
  audit: "Website audit",
  branding: "Logo concepts",
  website_builder: "Website preview",
  quality_check: "Quality check",
  proposal: "Final message",
};

const STATE_ICON = {
  done: <Check className="size-3.5 text-success" aria-hidden />,
  running: <Loader2 className="size-3.5 animate-spin text-primary" aria-hidden />,
  failed: <X className="size-3.5 text-destructive" aria-hidden />,
  skipped: <TriangleAlert className="size-3.5 text-warning" aria-hidden />,
  waiting: <CircleDashed className="size-3.5 text-muted-foreground" aria-hidden />,
} as const;

/** The order a person works an item in; the current one is highlighted. */
const TRACK: { key: string; label: string }[] = [
  { key: "confirm", label: "Confirm contact" },
  { key: "assets", label: "Prepare assets" },
  { key: "send", label: "Send and mark as sent" },
];

function trackIndex(step: ManualOutreachItem["step"]): number {
  if (step === "confirm_contact") return 0;
  if (step === "prepare_assets" || step === "preparing_assets" || step === "assets_failed") return 1;
  return 2;
}

export function AssetProgress({ progress }: { progress: AssetsProgress }) {
  return (
    <ol aria-label="Asset progress" className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
      {progress.steps.map((s) => (
        <li key={s.step} className="flex items-center gap-1.5" data-state={s.state}>
          {STATE_ICON[s.state] ?? STATE_ICON.waiting}
          <span>{STEP_LABELS[s.step] ?? s.step}</span>
          <span className="sr-only">: {s.state}</span>
        </li>
      ))}
    </ol>
  );
}

/** Choose logo / website preview / both; the lead's own requirement is preselected. */
export function AssetChooser({ suggested, disabled, onPrepare }: {
  suggested: AssetKind[];
  disabled?: boolean;
  onPrepare: (kinds: AssetKind[]) => void;
}) {
  const [kinds, setKinds] = useState<AssetKind[]>(suggested.length ? suggested : ["website"]);
  const toggle = (kind: AssetKind, on: boolean) =>
    setKinds((current) => (on ? [...new Set([...current, kind])] : current.filter((k) => k !== kind)));
  return (
    <div className="flex flex-wrap items-center gap-4">
      {(Object.keys(ASSET_LABELS) as AssetKind[]).map((kind) => (
        <div key={kind} className="flex items-center gap-2">
          <Checkbox id={`asset-${kind}`} checked={kinds.includes(kind)} onCheckedChange={(v) => toggle(kind, v === true)} />
          <Label htmlFor={`asset-${kind}`} className="text-sm">
            {ASSET_LABELS[kind]}
            {suggested.includes(kind) && <span className="ml-1 text-xs text-muted-foreground">(asked for)</span>}
          </Label>
        </div>
      ))}
      <Button size="sm" disabled={disabled || kinds.length === 0} onClick={() => onPrepare(kinds)}>
        <PackageCheck className="size-3.5" /> Prepare assets
      </Button>
    </div>
  );
}

/** The person's steps on one manual outreach item. Callbacks keep it free of data fetching. */
export function ManualSteps({ item, pending, onConfirm, onContactFailed, onPrepare, onMarkSent }: {
  item: ManualOutreachItem;
  pending?: boolean;
  onConfirm: () => void;
  onContactFailed: () => void;
  onPrepare: (kinds: AssetKind[]) => void;
  onMarkSent: () => void;
}) {
  const current = trackIndex(item.step);
  const progress = item.assets_progress;
  return (
    <section aria-label="Steps" className="mt-4 rounded-lg border bg-surface-raised/50 p-3">
      <ol className="mb-3 flex flex-wrap gap-2 text-xs">
        {TRACK.map((t, i) => (
          <li
            key={t.key}
            aria-current={i === current ? "step" : undefined}
            className={cn(
              "rounded-full px-2.5 py-1",
              i < current ? "bg-success-soft text-success-soft-foreground" : i === current ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground",
            )}
          >
            {i + 1}. {t.label}
          </li>
        ))}
      </ol>

      {item.step === "confirm_contact" && (
        <div className="flex flex-wrap items-center gap-2">
          <p className="mr-auto text-sm text-muted-foreground">Open the post or profile and check you can reach them there.</p>
          <Button variant="outline" size="sm" disabled={pending} onClick={onContactFailed}>
            <ThumbsDown className="size-3.5" /> Contact does not work
          </Button>
          <Button size="sm" disabled={pending} onClick={onConfirm}>
            <ThumbsUp className="size-3.5" /> This contact works
          </Button>
        </div>
      )}

      {item.step === "prepare_assets" && (
        <div className="grid gap-2">
          <AssetChooser suggested={item.assets_suggested} disabled={pending} onPrepare={onPrepare} />
          <p className="text-xs text-muted-foreground">Or send the drafts above as they are and mark the message as sent.</p>
        </div>
      )}

      {progress && (item.step === "preparing_assets" || item.step === "assets_failed" || item.step === "ready_to_send") && (
        <div className="mb-3 grid gap-2">
          <div className="flex items-center gap-2">
            {item.step === "preparing_assets" && <StatusPill tone="info" pulse>Preparing assets</StatusPill>}
            {item.step === "ready_to_send" && <StatusPill tone="success">Assets ready</StatusPill>}
            {item.step === "assets_failed" && <StatusPill tone="danger">Assets stopped{progress.reason ? `: ${progress.reason}` : ""}</StatusPill>}
          </div>
          <AssetProgress progress={progress} />
          {item.step === "assets_failed" && (
            <AssetChooser suggested={item.assets_requested?.kinds ?? item.assets_suggested} disabled={pending} onPrepare={onPrepare} />
          )}
        </div>
      )}

      {item.attachments.length > 0 && (
        <ul aria-label="Assets to send" className="mb-3 flex flex-wrap gap-2 text-xs">
          {item.attachments.map((a) => (
            <li key={a.url}>
              <a href={a.url} target="_blank" rel="noopener noreferrer" className="rounded border px-2 py-1 hover:bg-muted">
                {a.filename ?? a.kind ?? "file"}
              </a>
            </li>
          ))}
        </ul>
      )}

      {item.step !== "confirm_contact" && item.step !== "sent" && item.status === "pending" && (
        <div className="flex justify-end">
          <Button size="sm" disabled={pending} onClick={onMarkSent}>
            <Send className="size-3.5" /> Mark as sent
          </Button>
        </div>
      )}
    </section>
  );
}
