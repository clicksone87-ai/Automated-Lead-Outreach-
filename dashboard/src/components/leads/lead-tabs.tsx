"use client";

import { Building2, Globe, Inbox, LayoutList, MessageCircle, type LucideIcon } from "lucide-react";
import { ALL_FAMILIES, FAMILY_INFO, GOOD_LEADS, TIERS, type Family, type Tier } from "@/lib/families";
import { cn } from "@/lib/utils";
import type { LeadFamilyTierCount } from "@/lib/queries";

const FAMILY_ORDER: (Family | "all")[] = ["all", "social", "google", "registry", "other"];
const ICONS: Record<Family | "all", LucideIcon> = {
  all: LayoutList,
  social: MessageCircle,
  google: Globe,
  registry: Building2,
  other: Inbox,
};

const focusRing = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background";

/** Step 1: where the lead was found. One big, obvious choice. */
export function SourcePicker({ family, onChange, counts = [] }: {
  family: Family | undefined;
  onChange: (f: Family | undefined) => void;
  counts?: LeadFamilyTierCount[];
}) {
  return (
    <div role="group" aria-label="Where we found them" className="flex flex-wrap gap-1 rounded-lg border bg-surface-raised p-1">
      {FAMILY_ORDER.map((key) => {
        const active = (family ?? "all") === key;
        const info = key === "all" ? ALL_FAMILIES : FAMILY_INFO[key];
        const Icon = ICONS[key];
        const count = key === "all"
          ? counts.reduce((sum, row) => sum + row.high + row.medium + row.low + row.unscored, 0)
          : counts.find((row) => row.family === key);
        const countText = typeof count === "number"
          ? count
          : count
            ? count.high + count.medium + count.low + count.unscored
            : undefined;
        return (
          <button
            key={key}
            type="button"
            aria-pressed={active}
            title={info.hint}
            onClick={() => onChange(key === "all" ? undefined : key)}
            className={cn(
              "flex min-w-[130px] flex-1 items-center justify-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors",
              focusRing,
              active ? "bg-primary text-primary-foreground shadow-sm" : "text-muted-foreground hover:bg-muted hover:text-foreground",
            )}
          >
            <Icon className="size-4 shrink-0" aria-hidden />
            {info.label}
            {countText !== undefined && (
              <span className={cn("rounded-full px-1.5 py-0.5 text-[11px]", active ? "bg-primary-foreground/15" : "bg-muted")}>{countText}</span>
            )}
          </button>
        );
      })}
    </div>
  );
}

/** Coloured dots match the Interest bar in the table (green 75+, blue 60-74, grey below, hollow = no score yet). */
function Dots({ tier }: { tier: Tier | undefined }) {
  if (!tier) {
    return (
      <span className="flex -space-x-1" aria-hidden>
        <span className="size-2.5 rounded-full bg-success ring-2 ring-background" />
        <span className="size-2.5 rounded-full bg-primary ring-2 ring-background" />
      </span>
    );
  }
  if (tier === "unscored") {
    return <span className="size-2.5 rounded-full border-2 border-muted-foreground/60" aria-hidden />;
  }
  const colour = tier === "high" ? "bg-success" : tier === "medium" ? "bg-primary" : "bg-muted-foreground/50";
  return <span className={cn("size-2.5 rounded-full", colour)} aria-hidden />;
}

function Chip({ active, onClick, title, children, quiet }: {
  active: boolean; onClick: () => void; title: string; children: React.ReactNode; quiet?: boolean;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      title={title}
      onClick={onClick}
      className={cn(
        "flex items-center gap-2 rounded-full border px-3 py-1.5 text-sm transition-colors",
        focusRing,
        active
          ? "border-foreground/30 bg-muted font-medium text-foreground"
          : "border-transparent text-muted-foreground hover:bg-muted/60 hover:text-foreground",
        quiet && !active && "opacity-75",
      )}
    >
      {children}
    </button>
  );
}

/** Step 2: how interested they are. "Not interested yet" sits apart on purpose. */
export function InterestPicker({ tier, onChange, family, counts = [] }: {
  tier: Tier | undefined;
  onChange: (t: Tier | undefined) => void;
  family?: Family;
  counts?: LeadFamilyTierCount[];
}) {
  const selected = family ? counts.filter((row) => row.family === family) : counts;
  const tierCount = (key: Tier) => selected.reduce((sum, row) => {
    if (key === "high") return sum + row.high;
    if (key === "medium") return sum + row.medium;
    if (key === "low") return sum + row.low;
    return sum + row.unscored;
  }, 0);
  return (
    <div role="group" aria-label="How interested are they?" className="flex flex-wrap items-center gap-1">
      <span className="mr-1 text-sm text-muted-foreground">Interest:</span>
      <Chip active={!tier} onClick={() => onChange(undefined)} title={GOOD_LEADS.hint}>
        <Dots tier={undefined} /> {GOOD_LEADS.label} <span className="text-xs opacity-70">{selected.reduce((sum, row) => sum + row.high + row.medium + row.unscored, 0)}</span>
      </Chip>
      <Chip active={tier === "high"} onClick={() => onChange("high")} title={TIERS.high.hint}>
        <Dots tier="high" /> {TIERS.high.label} <span className="text-xs opacity-70">{tierCount("high")}</span>
      </Chip>
      <Chip active={tier === "medium"} onClick={() => onChange("medium")} title={TIERS.medium.hint}>
        <Dots tier="medium" /> {TIERS.medium.label} <span className="text-xs opacity-70">{tierCount("medium")}</span>
      </Chip>
      <Chip active={tier === "unscored"} onClick={() => onChange("unscored")} title={TIERS.unscored.hint}>
        <Dots tier="unscored" /> {TIERS.unscored.label} <span className="text-xs opacity-70">{tierCount("unscored")}</span>
      </Chip>
      <span className="mx-2 h-5 w-px bg-border" aria-hidden />
      <Chip quiet active={tier === "low"} onClick={() => onChange("low")} title={TIERS.low.hint}>
        <Dots tier="low" /> {TIERS.low.label} <span className="text-xs opacity-70">{tierCount("low")}</span>
      </Chip>
    </div>
  );
}
