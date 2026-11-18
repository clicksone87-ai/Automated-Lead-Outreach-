"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MotionConfig } from "framer-motion";
import { ThemeProvider } from "next-themes";
import { useState } from "react";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { LiveEvents } from "@/lib/live";

export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 5_000,
            retry: 1,
            refetchOnWindowFocus: true,
          },
        },
      }),
  );
  return (
    <ThemeProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange>
      <QueryClientProvider client={client}>
        <LiveEvents>
          <TooltipProvider delayDuration={250}>
            {/* Every framer-motion animation follows the OS "reduce motion" setting. */}
            <MotionConfig reducedMotion="user">
              {children}
              <Toaster />
            </MotionConfig>
          </TooltipProvider>
        </LiveEvents>
      </QueryClientProvider>
    </ThemeProvider>
  );
}
