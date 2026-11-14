"use client";

import { AlertTriangle, RefreshCw } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  className,
}: {
  icon: LucideIcon;
  title: string;
  description?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col items-center justify-center px-6 py-14 text-center", className)}>
      <div className="relative mb-4">
        <div className="absolute inset-0 -m-3 rounded-full bg-primary/10 blur-xl" aria-hidden />
        <div className="relative flex size-12 items-center justify-center rounded-2xl border bg-surface-raised shadow-soft">
          <Icon className="size-5 text-primary" aria-hidden />
        </div>
      </div>
      <h3 className="text-sm font-semibold">{title}</h3>
      {description && (
        <p className="mt-1 max-w-sm text-sm text-muted-foreground">{description}</p>
      )}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}

export function ErrorState({
  error,
  onRetry,
  className,
}: {
  error: unknown;
  onRetry?: () => void;
  className?: string;
}) {
  const message = error instanceof Error ? error.message : "Something went wrong.";
  const offline = /fetch|network|failed to fetch/i.test(message);
  return (
    <div
      role="alert"
      className={cn(
        "flex flex-col items-center justify-center gap-3 rounded-xl border border-dashed px-6 py-12 text-center",
        className,
      )}
    >
      <div className="flex size-10 items-center justify-center rounded-full bg-danger-soft">
        <AlertTriangle className="size-5 text-danger-soft-foreground" aria-hidden />
      </div>
      <div>
        <p className="text-sm font-semibold">
          {offline ? "Can't reach the API" : "Couldn't load this data"}
        </p>
        <p className="mt-1 max-w-md text-sm text-muted-foreground">
          {offline
            ? "Make sure the stack is running (`.\\make.ps1 up`) and the API is on localhost:8000."
            : message}
        </p>
      </div>
      {onRetry && (
        <Button variant="outline" size="sm" onClick={onRetry}>
          <RefreshCw className="size-3.5" /> Try again
        </Button>
      )}
    </div>
  );
}

export function RowsSkeleton({ rows = 6, className }: { rows?: number; className?: string }) {
  return (
    <div className={cn("space-y-2", className)} aria-busy aria-label="Loading">
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className="h-11 w-full rounded-lg" style={{ opacity: 1 - i * 0.12 }} />
      ))}
    </div>
  );
}

export function CardsSkeleton({ count = 4, className }: { count?: number; className?: string }) {
  return (
    <div className={cn("grid gap-4 sm:grid-cols-2 xl:grid-cols-4", className)} aria-busy>
      {Array.from({ length: count }, (_, i) => (
        <Skeleton key={i} className="h-28 rounded-xl" />
      ))}
    </div>
  );
}
