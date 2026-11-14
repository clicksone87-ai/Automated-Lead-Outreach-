"use client";

import { AnimatePresence, motion } from "framer-motion";
import {
  AlertCircle,
  Bot,
  CheckCircle2,
  Radar,
  RotateCw,
  Settings2,
  Sparkles,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { EmptyState } from "@/components/common/states";
import type { Activity } from "@/lib/api";
import { ago } from "@/lib/format";
import { cn } from "@/lib/utils";

const KIND: Record<string, { icon: LucideIcon; className: string }> = {
  lead_discovered: { icon: Sparkles, className: "bg-primary-soft text-primary-soft-foreground" },
  source_run: { icon: Radar, className: "bg-info-soft text-info-soft-foreground" },
  agent_started: { icon: Bot, className: "bg-muted text-muted-foreground" },
  agent_finished: { icon: CheckCircle2, className: "bg-success-soft text-success-soft-foreground" },
  agent_failed: { icon: AlertCircle, className: "bg-danger-soft text-danger-soft-foreground" },
  agent_retrying: { icon: RotateCw, className: "bg-warning-soft text-warning-soft-foreground" },
  source_updated: { icon: Settings2, className: "bg-muted text-muted-foreground" },
};

export function ActivityFeed({ events }: { events: Activity[] }) {
  if (!events.length) {
    return (
      <EmptyState
        icon={Radar}
        title="No activity yet"
        description="Agent runs, discovered leads and source runs will stream here live."
      />
    );
  }
  const ordered = [...events].reverse();
  return (
    <ol className="space-y-1" aria-live="polite" aria-label="Live activity">
      <AnimatePresence initial={false}>
        {ordered.slice(0, 40).map((event) => {
          const meta = KIND[event.kind] ?? { icon: Bot, className: "bg-muted text-muted-foreground" };
          const Icon = meta.icon;
          const body = (
            <div className="flex items-start gap-3 rounded-lg px-2 py-2 transition-colors hover:bg-muted/60">
              <span className={cn("mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-md", meta.className)}>
                <Icon className="size-3.5" aria-hidden />
              </span>
              <div className="min-w-0 flex-1">
                <p className="line-clamp-2 text-sm leading-snug">{event.message}</p>
                <p className="text-[11px] text-muted-foreground">{ago(event.created_at)}</p>
              </div>
            </div>
          );
          return (
            <motion.li
              key={event.id}
              layout
              initial={{ opacity: 0, x: -8 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.25 }}
            >
              {event.lead_id ? <Link href={`/leads/${event.lead_id}`}>{body}</Link> : body}
            </motion.li>
          );
        })}
      </AnimatePresence>
    </ol>
  );
}
