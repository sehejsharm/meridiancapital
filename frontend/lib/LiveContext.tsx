"use client";

import { createContext, useContext, type ReactNode } from "react";

import { useLive, type Live } from "@/lib/useLive";

const LiveContext = createContext<Live | null>(null);

/** One socket for the whole desk — every page reads the same feed. */
export function LiveProvider({ children }: { children: ReactNode }) {
  const live = useLive();
  return <LiveContext.Provider value={live}>{children}</LiveContext.Provider>;
}

export function useLiveFeed(): Live {
  const value = useContext(LiveContext);
  if (!value) throw new Error("useLiveFeed must be used inside LiveProvider");
  return value;
}
