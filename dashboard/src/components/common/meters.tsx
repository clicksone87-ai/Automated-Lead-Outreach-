"use client";

import { motion } from "framer-motion";
import { cn } from "@/lib/utils";

function toneFor(ratio: number) {
  if (ratio >= 1) return "var(--destructive)";
  if (ratio >= 0.8) return "var(--warning)";
  return "var(--primary)";
}

/** Horizontal usage meter (e.g. budget used vs cap). */
export function Meter({
  value,
  max,
  className,
  label,
}: {
  value: number;
  max: number;
  className?: string;
  label?: string;
}) {
  const ratio = max > 0 ? Math.min(value / max, 1) : 0;
  return (
    <div
      className={cn("h-1.5 w-full overflow-hidden rounded-full bg-muted", className)}
      role="meter"
      aria-valuemin={0}
      aria-valuemax={max}
      aria-valuenow={value}
      aria-label={label}
    >
      <motion.div
        className="h-full rounded-full"
        style={{ background: toneFor(max > 0 ? value / max : 0) }}
        initial={{ width: 0 }}
        animate={{ width: `${ratio * 100}%` }}
        transition={{ duration: 0.6, ease: [0.22, 1, 0.36, 1] }}
      />
    </div>
  );
}

/** Radial score gauge (0–100), used for audit scores and intent. */
export function Gauge({
  value,
  size = 88,
  stroke = 8,
  label,
  className,
}: {
  value: number | null | undefined;
  size?: number;
  stroke?: number;
  label?: string;
  className?: string;
}) {
  const v = value ?? 0;
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const arc = circumference * 0.75;
  const color =
    value == null ? "var(--muted-foreground)" : v >= 90 ? "var(--success)" : v >= 50 ? "var(--warning)" : "var(--destructive)";
  return (
    <div className={cn("relative inline-flex flex-col items-center", className)}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="rotate-[135deg]" aria-hidden>
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="var(--muted)"
          strokeWidth={stroke}
          strokeDasharray={`${arc} ${circumference}`}
          strokeLinecap="round"
        />
        <motion.circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={color}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={`${arc} ${circumference}`}
          initial={{ strokeDashoffset: arc }}
          animate={{ strokeDashoffset: arc - (arc * Math.min(v, 100)) / 100 }}
          transition={{ duration: 0.9, ease: [0.22, 1, 0.36, 1] }}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="tabular text-xl font-semibold leading-none">{value == null ? "—" : Math.round(v)}</span>
      </div>
      {label && <span className="mt-1 text-xs font-medium text-muted-foreground">{label}</span>}
      <span className="sr-only">
        {label}: {value == null ? "no score" : `${Math.round(v)} out of 100`}
      </span>
    </div>
  );
}
