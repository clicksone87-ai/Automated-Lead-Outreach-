"use client";

import { motion } from "framer-motion";
import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { CountUp } from "./count-up";
import { Panel } from "./panel";

export function StatCard({
  label,
  value,
  hint,
  icon: Icon,
  footer,
  className,
  index = 0,
}: {
  label: string;
  value: React.ReactNode;
  hint?: React.ReactNode;
  icon?: LucideIcon;
  footer?: React.ReactNode;
  className?: string;
  index?: number;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, delay: index * 0.05, ease: [0.22, 1, 0.36, 1] }}
    >
      <Panel className={cn("lift relative flex h-full flex-col gap-3 overflow-hidden p-5", className)} as="div">
        <div className="flex items-center justify-between">
          <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            {label}
          </span>
          {Icon && (
            <span className="flex size-8 items-center justify-center rounded-xl bg-primary-soft ring-1 ring-inset ring-primary/15">
              <Icon className="size-4 text-primary-soft-foreground" aria-hidden />
            </span>
          )}
        </div>
        <div className="tabular font-display text-[2rem] font-semibold leading-none tracking-tight">
          {typeof value === "number" ? <CountUp value={value} /> : value}
        </div>
        {hint && <div className="text-xs text-muted-foreground">{hint}</div>}
        {footer && <div className="mt-auto">{footer}</div>}
      </Panel>
    </motion.div>
  );
}
