"use client";

import { useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useRef, useState } from "react";
import { API_URL, type Activity } from "./api";
import { qk } from "./queries";

type LiveState = { connected: boolean; last: Activity | null };
const LiveContext = createContext<LiveState>({ connected: false, last: null });

/**
 * Subscribes to the API's server-sent events and invalidates affected queries, so agent
 * activity shows up in real time. Falls back to the queries' own polling when disconnected.
 */
export function LiveEvents({ children }: { children: React.ReactNode }) {
  const qc = useQueryClient();
  const [state, setState] = useState<LiveState>({ connected: false, last: null });
  const pending = useRef<Set<string>>(new Set());
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    let source: EventSource | null = null;
    let retry: ReturnType<typeof setTimeout> | null = null;
    let closed = false;

    const flush = () => {
      timer.current = null;
      const keys = pending.current;
      pending.current = new Set();
      qc.invalidateQueries({ queryKey: qk.overview });
      qc.invalidateQueries({ queryKey: qk.activity });
      qc.invalidateQueries({ queryKey: ["leads"] });
      if (keys.has("sources")) qc.invalidateQueries({ queryKey: qk.sources });
      keys.forEach((k) => k.startsWith("lead:") && qc.invalidateQueries({ queryKey: qk.lead(k.slice(5)) }));
    };

    const connect = () => {
      source = new EventSource(`${API_URL}/api/events`);
      source.onopen = () => setState((s) => ({ ...s, connected: true }));
      source.addEventListener("activity", (ev) => {
        const event = JSON.parse((ev as MessageEvent<string>).data) as Activity;
        setState({ connected: true, last: event });
        if (event.lead_id) pending.current.add(`lead:${event.lead_id}`);
        if (event.kind.startsWith("source")) pending.current.add("sources");
        if (!timer.current) timer.current = setTimeout(flush, 600);
      });
      source.onerror = () => {
        setState((s) => ({ ...s, connected: false }));
        source?.close();
        if (!closed) retry = setTimeout(connect, 5000);
      };
    };
    connect();
    return () => {
      closed = true;
      source?.close();
      if (retry) clearTimeout(retry);
      if (timer.current) clearTimeout(timer.current);
    };
  }, [qc]);

  return <LiveContext.Provider value={state}>{children}</LiveContext.Provider>;
}

export const useLive = () => useContext(LiveContext);
