import { cn } from "@/lib/utils";
import type { Tone } from "@/lib/status";

export const toneClasses: Record<Tone, string> = {
  neutral: "bg-muted text-muted-foreground ring-border",
  accent: "bg-primary-soft text-primary-soft-foreground ring-primary/20",
  success: "bg-success-soft text-success-soft-foreground ring-success/25",
  warning: "bg-warning-soft text-warning-soft-foreground ring-warning/30",
  danger: "bg-danger-soft text-danger-soft-foreground ring-destructive/25",
  info: "bg-info-soft text-info-soft-foreground ring-info/25",
};

export const toneDot: Record<Tone, string> = {
  neutral: "bg-muted-foreground/60",
  accent: "bg-primary",
  success: "bg-success",
  warning: "bg-warning",
  danger: "bg-destructive",
  info: "bg-info",
};

export function StatusPill({
  tone = "neutral",
  children,
  dot = true,
  pulse = false,
  className,
}: {
  tone?: Tone;
  children: React.ReactNode;
  dot?: boolean;
  pulse?: boolean;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset",
        toneClasses[tone],
        className,
      )}
    >
      {dot && (
        <span
          aria-hidden
          className={cn("size-1.5 rounded-full", toneDot[tone], pulse && "animate-pulse-dot")}
        />
      )}
      {children}
    </span>
  );
}
