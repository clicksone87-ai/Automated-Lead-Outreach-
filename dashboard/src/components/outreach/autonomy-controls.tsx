"use client";

import { Check, X } from "lucide-react";
import { ConfirmDialog } from "@/components/common/confirm-dialog";
import { Button } from "@/components/ui/button";
import { useAutonomy, useSetStage } from "@/lib/outreach";
import { cn } from "@/lib/utils";

/** Live move-up conditions and the only control that changes the autonomy stage. */
export function AutonomyControls() {
  const { data } = useAutonomy();
  const setStage = useSetStage();
  if (!data) return null;
  return (
    <div className="mt-4 space-y-3 rounded-xl border p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm font-medium">
          Conditions for stage {data.next_stage ?? data.stage}
          {data.sandbox_only && <span className="ml-2 text-xs font-normal text-muted-foreground">(sandbox sends to Mailpit don&apos;t count)</span>}
        </p>
        <div className="flex gap-2">
          {data.stage > 1 && (
            <ConfirmDialog
              trigger={<Button variant="outline" size="sm">Move down to stage {data.stage - 1}</Button>}
              title={`Move down to stage ${data.stage - 1}?`}
              description="Moving down is always allowed and takes effect immediately."
              confirmLabel="Move down"
              onConfirm={() => setStage.mutate({ stage: data.stage - 1, reason: "moved down from dashboard" })}
            />
          )}
          {data.next_stage && (
            <ConfirmDialog
              trigger={<Button size="sm" disabled={!data.can_move_up}>Move up to stage {data.next_stage}</Button>}
              title={`Move up to stage ${data.next_stage}?`}
              description="The system will send more on its own. Every policy check, limit and budget cap still applies."
              confirmLabel="Move up"
              onConfirm={() => setStage.mutate({ stage: data.next_stage as number, reason: "moved up from dashboard" })}
            />
          )}
        </div>
      </div>
      <ul className="grid gap-2 sm:grid-cols-2">
        {data.conditions.map((c) => (
          <li key={c.key} className={cn("flex items-center justify-between gap-2 rounded-lg border px-3 py-2 text-xs", c.met ? "border-success/30" : "")}>
            <span className="flex items-center gap-1.5">
              {c.met ? <Check className="size-3.5 text-success" aria-label="met" /> : <X className="size-3.5 text-muted-foreground" aria-label="not met" />}
              {c.label}
            </span>
            <span className="tabular text-muted-foreground">{c.value}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
