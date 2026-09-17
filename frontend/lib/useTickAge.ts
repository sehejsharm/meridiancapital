"use client";

import { useEffect, useState } from "react";

/** Beyond this the feed is treated as stale and the desk says so loudly. */
export const STALE_MS = 1500;

export type Freshness = "fresh" | "stale" | "dead";

/**
 * How long since the last update landed.
 *
 * A trading dashboard that quietly stops updating is more dangerous than one
 * that is visibly broken: the numbers still look plausible, so you act on a
 * price that stopped being true a minute ago. This ticks on its own rather than
 * on incoming data, because the whole point is to notice the absence of data.
 */
export function useTickAge(lastUpdate: number | null): {
  ageMs: number | null;
  state: Freshness;
} {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(t);
  }, []);

  if (lastUpdate === null) return { ageMs: null, state: "dead" };

  const ageMs = Math.max(0, now - lastUpdate);
  const state: Freshness =
    ageMs > STALE_MS * 8 ? "dead" : ageMs > STALE_MS ? "stale" : "fresh";
  return { ageMs, state };
}
