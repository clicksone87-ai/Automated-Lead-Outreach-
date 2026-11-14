"use client";

import { useTheme } from "next-themes";
import { Toaster as Sonner } from "sonner";

export function Toaster() {
  const { resolvedTheme } = useTheme();
  return (
    <Sonner
      theme={resolvedTheme === "dark" ? "dark" : "light"}
      position="bottom-right"
      closeButton
      toastOptions={{
        classNames: {
          toast:
            "!rounded-xl !border !border-border !bg-popover !text-popover-foreground !shadow-elevated",
          description: "!text-muted-foreground",
        },
      }}
    />
  );
}
