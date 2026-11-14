import { cn } from "@/lib/utils";

/** The base surface used across the dashboard: soft shadow, hairline border, rounded-xl. */
export function Panel({
  className,
  children,
  as: Tag = "section",
  ...props
}: React.HTMLAttributes<HTMLElement> & { as?: "section" | "div" | "article" }) {
  return (
    <Tag
      className={cn("rounded-xl border bg-card text-card-foreground shadow-soft", className)}
      {...props}
    >
      {children}
    </Tag>
  );
}

export function PanelHeader({
  title,
  description,
  action,
  className,
}: {
  title: React.ReactNode;
  description?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex items-start justify-between gap-3 border-b px-5 py-4", className)}>
      <div className="min-w-0">
        <h3 className="text-sm font-semibold leading-tight">{title}</h3>
        {description && <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>}
      </div>
      {action}
    </div>
  );
}
