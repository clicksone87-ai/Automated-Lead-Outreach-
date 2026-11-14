"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { CommandPalette } from "./command-palette";
import { ALL_NAV_ITEMS } from "./nav";
import { Sidebar } from "./sidebar";
import { Topbar } from "./topbar";

const STORAGE_KEY = "leads.sidebar.collapsed";

function readCollapsed(): boolean {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

function isTyping(target: EventTarget | null) {
  const el = target as HTMLElement | null;
  return !!el && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName));
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [collapsed, setCollapsed] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const chord = useRef<number>(0);

  useEffect(() => {
    // Tablet widths start collapsed; otherwise remember the user's choice.
    setCollapsed(window.innerWidth < 1100 ? true : readCollapsed());
  }, []);

  const toggle = useCallback(() => {
    setCollapsed((c) => {
      try {
        window.localStorage.setItem(STORAGE_KEY, c ? "0" : "1");
      } catch {
        /* storage unavailable: preference just isn't remembered */
      }
      return !c;
    });
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((o) => !o);
        return;
      }
      if (isTyping(e.target) || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "[") {
        toggle();
        return;
      }
      // "G then X" navigation chords
      if (e.key.toLowerCase() === "g") {
        chord.current = Date.now();
        return;
      }
      if (Date.now() - chord.current < 1000) {
        const target = ALL_NAV_ITEMS.find((i) => i.shortcut?.toLowerCase() === `g ${e.key.toLowerCase()}`);
        if (target) {
          e.preventDefault();
          router.push(target.href);
        }
        chord.current = 0;
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [router, toggle]);

  return (
    <div className="flex h-dvh overflow-hidden bg-background">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-50 focus:rounded-md focus:bg-primary focus:px-3 focus:py-2 focus:text-primary-foreground"
      >
        Skip to content
      </a>
      <Sidebar collapsed={collapsed} onToggle={toggle} className="hidden md:flex" />
      <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
        <SheetContent side="left" className="w-[260px] p-0">
          <SheetTitle className="sr-only">Navigation</SheetTitle>
          <Sidebar collapsed={false} onNavigate={() => setMobileOpen(false)} className="w-full border-r-0" />
        </SheetContent>
      </Sheet>
      <div className="flex min-w-0 flex-1 flex-col">
        <Topbar onOpenSearch={() => setPaletteOpen(true)} onOpenMobileNav={() => setMobileOpen(true)} />
        <main id="main" className="scrollbar-thin flex-1 overflow-y-auto">
          <div className="mx-auto w-full max-w-[1440px] px-4 py-6 md:px-8 md:py-8">{children}</div>
        </main>
      </div>
      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
    </div>
  );
}
