"use client";

import { Moon, Play, RefreshCw, Sun, User } from "lucide-react";
import { useRouter } from "next/navigation";
import { useTheme } from "next-themes";
import { useDeferredValue, useState } from "react";
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
  CommandShortcut,
} from "@/components/ui/command";
import { useRunSource, useSearch, useSources } from "@/lib/queries";
import { ALL_NAV_ITEMS } from "./nav";

export function CommandPalette({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const router = useRouter();
  const { setTheme, resolvedTheme } = useTheme();
  const [query, setQuery] = useState("");
  const deferred = useDeferredValue(query);
  const { data: hits, isFetching } = useSearch(deferred);
  const { data: sources } = useSources();
  const runSource = useRunSource();

  const go = (href: string) => {
    onOpenChange(false);
    setQuery("");
    router.push(href);
  };

  return (
    <CommandDialog open={open} onOpenChange={onOpenChange} title="Command palette" description="Search and run actions">
      <CommandInput placeholder="Search leads, jump to a page, run an action…" value={query} onValueChange={setQuery} />
      <CommandList>
        <CommandEmpty>{isFetching ? "Searching…" : "No results."}</CommandEmpty>
        {hits && hits.length > 0 && (
          <CommandGroup heading="Leads">
            {hits.map((hit) => (
              <CommandItem key={hit.id} value={`lead ${hit.title} ${hit.subtitle ?? ""}`} onSelect={() => go(hit.href)}>
                <User className="size-4" />
                <div className="min-w-0">
                  <div className="truncate">{hit.title}</div>
                  {hit.subtitle && <div className="truncate text-xs text-muted-foreground">{hit.subtitle}</div>}
                </div>
              </CommandItem>
            ))}
          </CommandGroup>
        )}
        <CommandGroup heading="Go to">
          {ALL_NAV_ITEMS.map((item) => (
            <CommandItem key={item.href} value={`page ${item.label} ${item.description}`} onSelect={() => go(item.href)}>
              <item.icon className="size-4" />
              <span>{item.label}</span>
              <span className="truncate text-xs text-muted-foreground">{item.description}</span>
              {item.shortcut && <CommandShortcut>{item.shortcut}</CommandShortcut>}
            </CommandItem>
          ))}
        </CommandGroup>
        <CommandSeparator />
        <CommandGroup heading="Actions">
          {(sources ?? [])
            .filter((s) => s.enabled)
            .map((s) => (
              <CommandItem
                key={s.key}
                value={`run source ${s.label}`}
                onSelect={() => {
                  // Sources on a limited free tier are confirmed on the Sources page.
                  if (s.manual_run.limited) go("/sources");
                  else runSource.mutate(s.key);
                  onOpenChange(false);
                }}
              >
                <Play className="size-4" />
                <span>Run now: {s.label}</span>
                {s.manual_run.limited && <span className="text-xs text-muted-foreground">confirm on Sources</span>}
              </CommandItem>
            ))}
          <CommandItem value="theme toggle dark light" onSelect={() => { setTheme(resolvedTheme === "dark" ? "light" : "dark"); onOpenChange(false); }}>
            {resolvedTheme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
            <span>Switch to {resolvedTheme === "dark" ? "light" : "dark"} mode</span>
          </CommandItem>
          <CommandItem value="reload refresh" onSelect={() => window.location.reload()}>
            <RefreshCw className="size-4" />
            <span>Reload dashboard</span>
          </CommandItem>
        </CommandGroup>
      </CommandList>
    </CommandDialog>
  );
}
