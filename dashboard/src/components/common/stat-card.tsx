"use client";

import { motion } from "framer-motion";
import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";
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
      <Panel className={cn("flex h-full flex-col gap-3 p-5", className)} as="div">
        <div className="flex items-center justify-between">
          <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            {label}
          </span>
          {Icon && (
            <span className="flex size-7 items-center justify-center rounded-lg bg-primary-soft">
              <Icon className="size-3.5 text-primary-soft-foreground" aria-hidden />
            </span>
          )}
        </div>
        <div className="tabular text-2xl font-semibold tracking-tight">{value}</div>
        {hint && <div className="text-xs text-muted-foreground">{hint}</div>}
        {footer && <div className="mt-auto">{footer}</div>}
      </Panel>
    </motion.div>
  );
}
