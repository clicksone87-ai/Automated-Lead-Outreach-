"use client";

import { AnimatePresence, motion } from "framer-motion";
import {
  Building2,
  ClipboardCheck,
  Contact,
  FileSearch,
  Hammer,
  Mail,
  Palette,
  Radar,
  Send,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import type { Overview } from "@/lib/api";
import { cn } from "@/lib/utils";

const ICONS: Record<string, LucideIcon> = {
  discovery: Radar,
  requirements: FileSearch,
  contact: Contact,
  audit: Building2,
  branding: Palette,
  builder: Hammer,
  qa: ClipboardCheck,
  proposal: Mail,
  outreach: Send,
};

function AnimatedNumber({ value }: { value: number }) {
  return (
    <AnimatePresence mode="popLayout" initial={false}>
      <motion.span
        key={value}
        initial={{ y: 10, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        exit={{ y: -10, opacity: 0 }}
        transition={{ duration: 0.25, ease: [0.22, 1, 0.36, 1] }}
        className="tabular inline-block"
      >
        {value}
      </motion.span>
    </AnimatePresence>
  );
}

function Connector({ active }: { active: boolean }) {
  return (
    <div className="relative mx-1 h-px w-6 shrink-0 self-center sm:w-8 lg:flex-1" aria-hidden>
      <div className="absolute inset-0 bg-border" />
      {active && (
        <div
          className="animate-flow absolute inset-0 opacity-90"
          style={{
            backgroundImage:
              "linear-gradient(90deg, transparent 0 40%, var(--primary) 40% 60%, transparent 60% 100%)",
            backgroundSize: "48px 1px",
          }}
        />
      )}
    </div>
  );
}

export function PipelineFlow({ stages }: { stages: Overview["stages"] }) {
  const maxCurrent = Math.max(1, ...stages.map((s) => s.current));
  return (
    <div className="scrollbar-thin -mx-1 overflow-x-auto px-1 pb-1">
      <ol className="flex min-w-max items-stretch lg:min-w-0" aria-label="Pipeline stages">
        {stages.map((stage, i) => {
          const Icon = ICONS[stage.key] ?? Radar;
          const hot = stage.current > 0;
          const intensity = stage.current / maxCurrent;
          return (
            <li key={stage.key} className="flex items-stretch lg:flex-1">
              <motion.div
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: i * 0.04, duration: 0.3 }}
                className="w-[118px] lg:w-full"
              >
                <Link
                  href={`/leads?stage=${stage.key}`}
                  className={cn(
                    "group relative flex h-full flex-col gap-2 overflow-hidden rounded-xl border bg-surface-raised p-3 transition-all hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-soft",
                    hot && "border-primary/25",
                  )}
                  aria-label={`${stage.label}: ${stage.current} now, ${stage.reached} reached`}
                >
                  {hot && (
                    <div
                      className="pointer-events-none absolute inset-x-0 bottom-0 h-1 bg-primary"
                      style={{ opacity: 0.25 + intensity * 0.75 }}
                      aria-hidden
                    />
                  )}
                  <div className="flex items-center gap-1.5">
                    <span
                      className={cn(
                        "flex size-6 items-center justify-center rounded-md",
                        hot ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground",
                      )}
                    >
                      <Icon className="size-3.5" aria-hidden />
                    </span>
                    <span className="truncate text-xs font-medium text-muted-foreground group-hover:text-foreground">
                      {stage.label}
                    </span>
                  </div>
                  <div className="text-2xl font-semibold leading-none tracking-tight">
                    <AnimatedNumber value={stage.current} />
                  </div>
                  <div className="tabular text-[11px] text-muted-foreground">{stage.reached} reached</div>
                </Link>
              </motion.div>
              {i < stages.length - 1 && <Connector active={hot} />}
            </li>
          );
        })}
      </ol>
    </div>
  );
}
