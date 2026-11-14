"use client";

import { motion } from "framer-motion";
import { ChevronsLeft, Sparkles } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useOverview } from "@/lib/queries";
import { cn } from "@/lib/utils";
import { NAV } from "./nav";

export function isActive(pathname: string, href: string) {
  return href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(`${href}/`);
}

export function Sidebar({
  collapsed,
  onToggle,
  onNavigate,
  className,
}: {
  collapsed: boolean;
  onToggle?: () => void;
  onNavigate?: () => void;
  className?: string;
}) {
  const pathname = usePathname();
  const { data: overview } = useOverview();
  const badges: Record<string, number | undefined> = {
    "/leads": overview?.leads_today || undefined,
  };

  return (
    <motion.aside
      animate={{ width: collapsed ? 68 : 248 }}
      transition={{ duration: 0.25, ease: [0.22, 1, 0.36, 1] }}
      className={cn(
        "flex h-full shrink-0 flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground",
        className,
      )}
      aria-label="Main navigation"
    >
      <div className={cn("flex h-14 items-center gap-2.5 px-4", collapsed && "justify-center px-0")}>
        <div className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground shadow-glow">
          <Sparkles className="size-4" aria-hidden />
        </div>
        {!collapsed && (
          <div className="min-w-0 leading-tight">
            <div className="truncate text-sm font-semibold text-foreground">Leads Engine</div>
            <div className="truncate text-[11px] text-muted-foreground">Agency automation</div>
          </div>
        )}
      </div>

      <nav className="scrollbar-thin flex-1 space-y-5 overflow-y-auto px-3 py-3">
        {NAV.map((group) => (
          <div key={group.label}>
            {!collapsed && (
              <div className="px-2 pb-1.5 text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">
                {group.label}
              </div>
            )}
            <ul className="space-y-0.5">
              {group.items.map((item) => {
                const active = isActive(pathname, item.href);
                const badge = badges[item.href];
                const link = (
                  <Link
                    href={item.href}
                    onClick={onNavigate}
                    aria-current={active ? "page" : undefined}
                    className={cn(
                      "group relative flex h-9 items-center gap-3 rounded-lg px-2.5 text-sm font-medium transition-colors",
                      "hover:bg-sidebar-active hover:text-foreground",
                      active && "bg-sidebar-active text-foreground shadow-xs ring-1 ring-sidebar-border",
                      collapsed && "justify-center px-0",
                    )}
                  >
                    {active && (
                      <motion.span
                        layoutId="nav-active"
                        className="absolute inset-y-2 left-0 w-0.5 rounded-full bg-primary"
                        transition={{ duration: 0.25 }}
                      />
                    )}
                    <item.icon
                      className={cn("size-4 shrink-0", active ? "text-primary" : "text-muted-foreground group-hover:text-foreground")}
                      aria-hidden
                    />
                    {!collapsed && <span className="truncate">{item.label}</span>}
                    {!collapsed && badge ? (
                      <span className="tabular ml-auto rounded-full bg-primary-soft px-1.5 text-[11px] font-semibold text-primary-soft-foreground">
                        {badge}
                      </span>
                    ) : null}
                  </Link>
                );
                return (
                  <li key={item.href}>
                    {collapsed ? (
                      <Tooltip>
                        <TooltipTrigger asChild>{link}</TooltipTrigger>
                        <TooltipContent side="right">{item.label}</TooltipContent>
                      </Tooltip>
                    ) : (
                      link
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>

      {onToggle && (
        <div className="border-t border-sidebar-border p-3">
          <button
            type="button"
            onClick={onToggle}
            className={cn(
              "flex h-8 w-full items-center gap-2 rounded-lg px-2.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-sidebar-active hover:text-foreground",
              collapsed && "justify-center px-0",
            )}
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          >
            <ChevronsLeft className={cn("size-4 transition-transform", collapsed && "rotate-180")} aria-hidden />
            {!collapsed && <span>Collapse</span>}
            {!collapsed && <kbd className="ml-auto rounded border bg-background px-1 font-mono text-[10px]">[</kbd>}
          </button>
        </div>
      )}
    </motion.aside>
  );
}
