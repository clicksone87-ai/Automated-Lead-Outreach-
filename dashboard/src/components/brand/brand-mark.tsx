"use client";

import { useId } from "react";
import { cn } from "@/lib/utils";

/**
 * Leads Engine system mark: a radar signal reaching out from the corner to a found lead.
 * `live` animates the signal rings and the lead dot (honours reduced motion via CSS).
 */
export function BrandMark({ className, live = false, title = "Leads Engine" }: { className?: string; live?: boolean; title?: string }) {
  const id = useId().replace(/:/g, "");
  return (
    <svg viewBox="0 0 32 32" className={cn("brand-mark", live && "is-live", className)} role="img" aria-label={title}>
      <defs>
        <linearGradient id={`bm-${id}`} x1="0" y1="0" x2="32" y2="32" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="var(--brand-from)" />
          <stop offset="1" stopColor="var(--brand-to)" />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="9" fill={`url(#bm-${id})`} />
      <g fill="none" stroke="#fff" strokeLinecap="round">
        <path className="bm-ring bm-ring-1" d="M8.5 16.5a7 7 0 0 1 7 7" strokeWidth="2.4" opacity="0.95" />
        <path className="bm-ring bm-ring-2" d="M8.5 10.5a13 13 0 0 1 13 13" strokeWidth="2.4" opacity="0.6" />
      </g>
      <circle cx="8.5" cy="23.5" r="2.4" fill="#fff" />
      <circle className="bm-lead" cx="22.5" cy="9.5" r="2.7" fill="#fff" />
    </svg>
  );
}

/** Wordmark lockup: mark + name in the display face. */
export function BrandLockup({ live = false, compact = false }: { live?: boolean; compact?: boolean }) {
  return (
    <div className="flex min-w-0 items-center gap-2.5">
      <BrandMark className="size-8 shrink-0 drop-shadow-[0_4px_12px_color-mix(in_oklch,var(--brand-from)_45%,transparent)]" live={live} />
      {!compact && (
        <div className="min-w-0 leading-tight">
          <div className="truncate font-display text-[15px] font-semibold tracking-tight text-sidebar-strong">Leads Engine</div>
          <div className="truncate text-[11px] text-sidebar-muted">Find · qualify · reach out</div>
        </div>
      )}
    </div>
  );
}
