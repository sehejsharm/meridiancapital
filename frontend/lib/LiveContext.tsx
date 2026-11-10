"use client";

import { createContext, useContext, type ReactNode } from "react";

import { useLive, type Live, type LiveClock } from "@/lib/useLive";

const LiveContext = createContext<Live | null>(null);
const LiveClockContext = createContext<LiveClock | null>(null);

/**
 * One socket for the whole desk — every page reads the same feed.
 *
 * Two contexts, so that what changes every second is not what every component
 * listens to. The data (snapshot, status, events, connection) changes when
 * something happens. The clock (when the feed last spoke) changes with every
 * message, the once-a-second heartbeat included; only the staleness indicators
 * read it. With one context, every heartbeat re-rendered every page.
 */
export function LiveProvider({ children }: { children: ReactNode }) {
  const { live, clock } = useLive();
  return (
    <LiveContext.Provider value={live}>
      <LiveClockContext.Provider value={clock}>{children}</LiveClockContext.Provider>
    </LiveContext.Provider>
  );
}

export function useLiveFeed(): Live {
  const value = useContext(LiveContext);
  if (!value) throw new Error("useLiveFeed must be used inside LiveProvider");
  return value;
}

/** When the feed last delivered anything. For the staleness indicators only: it changes every second. */
export function useLiveClock(): LiveClock {
  const value = useContext(LiveClockContext);
  if (!value) throw new Error("useLiveClock must be used inside LiveProvider");
  return value;
}
