"use client";

import { cn } from "@/lib/utils";
import type { NotUsefulCounts } from "@/lib/not-useful";

/** Reasons the backend knows, so the tabs show before the counts arrive. */
export const KNOWN_REASONS: { reason: string; label: string }[] = [
  { reason: "no_contact", label: "No contact route" },
  { reason: "low_intent", label: "Low intent" },
  { reason: "older_than_8h", label: "Older than 8 h" },
  { reason: "excluded_market", label: "Excluded market" },
  { reason: "freelance", label: "Freelance platform" },
];

const focusRing = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background";

const sentence = (label: string) => label.charAt(0).toUpperCase() + label.slice(1);

/** One tab per reason with its count from the database. A count that is not known (still
 * loading, or the request failed) shows "—", never a guess. */
export function ReasonPicker({ reason, counts, onChange }: {
  reason: string | undefined;
  counts: NotUsefulCounts | undefined;
  onChange: (reason: string | undefined) => void;
}) {
  const known = new Map(KNOWN_REASONS.map((r) => [r.reason, r.label]));
  const tabs = [
    ...KNOWN_REASONS.map((r) => r.reason),
    ...(counts?.reasons ?? []).map((r) => r.reason).filter((r) => !known.has(r)),
  ];
  const countFor = (key: string | undefined): string => {
    if (!counts) return "—";
    if (key === undefined) return String(counts.total);
    const row = counts.reasons.find((r) => r.reason === key);
    return row ? String(row.count) : "—";
  };
  const labelFor = (key: string) =>
    known.get(key) ?? sentence(counts?.reasons.find((r) => r.reason === key)?.label ?? key.replace(/_/g, " "));
  return (
    <div role="group" aria-label="Why they are not useful" className="flex flex-wrap gap-1 rounded-lg border bg-surface-raised p-1">
      {[undefined, ...tabs].map((key) => {
        const active = reason === key;
        return (
          <button
            key={key ?? "all"}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(key)}
            className={cn(
              "flex min-w-[120px] flex-1 items-center justify-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors",
              focusRing,
              active ? "bg-primary text-primary-foreground shadow-sm" : "text-muted-foreground hover:bg-muted hover:text-foreground",
            )}
          >
            {key === undefined ? "All reasons" : labelFor(key)}
            <span
              data-testid={`count-${key ?? "all"}`}
              className={cn("rounded-full px-1.5 py-0.5 text-[11px]", active ? "bg-primary-foreground/15" : "bg-muted")}
            >
              {countFor(key)}
            </span>
          </button>
        );
      })}
    </div>
  );
}
