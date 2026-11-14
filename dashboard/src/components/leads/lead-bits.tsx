import { FlaskConical } from "lucide-react";
import { StatusPill } from "@/components/common/status-pill";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { flag, languageName } from "@/lib/format";
import { leadStatus, SERVICES } from "@/lib/status";
import { cn } from "@/lib/utils";

export function LeadStatusPill({ status }: { status: string }) {
  const meta = leadStatus(status);
  return <StatusPill tone={meta.tone}>{meta.label}</StatusPill>;
}

export function IntentBar({ score }: { score: number | null | undefined }) {
  if (score == null) return <span className="text-xs text-muted-foreground">—</span>;
  const tone = score >= 75 ? "bg-success" : score >= 60 ? "bg-primary" : score >= 40 ? "bg-warning" : "bg-muted-foreground/50";
  return (
    <div className="flex items-center gap-2" aria-label={`Intent score ${score}`}>
      <div className="h-1.5 w-14 overflow-hidden rounded-full bg-muted">
        <div className={cn("h-full rounded-full", tone)} style={{ width: `${score}%` }} />
      </div>
      <span className="tabular text-xs font-medium">{score}</span>
    </div>
  );
}

export function ServiceChips({ services }: { services: string[] }) {
  if (!services.length) return <span className="text-xs text-muted-foreground">—</span>;
  return (
    <div className="flex flex-wrap gap-1">
      {services.map((s) => (
        <span key={s} className="rounded-md border bg-surface-raised px-1.5 py-0.5 text-[11px] font-medium">
          {SERVICES[s] ?? s}
        </span>
      ))}
    </div>
  );
}

export function LocaleCell({ country, language }: { country: string | null; language: string | null }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs">
      <span aria-hidden>{flag(country)}</span>
      <span className="font-medium">{country ?? "—"}</span>
      <span className="text-muted-foreground">· {language ? language.toUpperCase() : "?"}</span>
      <span className="sr-only">{languageName(language)}</span>
    </span>
  );
}

export function DemoBadge() {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="inline-flex items-center gap-1 rounded-md bg-warning-soft px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-warning-soft-foreground">
          <FlaskConical className="size-3" aria-hidden /> Demo
        </span>
      </TooltipTrigger>
      <TooltipContent>Produced with mock adapters — can never be sent to a real recipient.</TooltipContent>
    </Tooltip>
  );
}
