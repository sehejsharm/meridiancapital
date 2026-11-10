"use client";

import { useEffect, useState } from "react";

/** Beyond this the feed is treated as stale and the desk says so loudly. The
 *  server sends at least one message every second (a heartbeat when nothing
 *  else changes), so 3.5s is three missed beats — not a phone network's jitter. */
export const STALE_MS = 3500;
/** Beyond this it is dead. */
export const DEAD_MS = 15_000;
/** How often the age is re-read. */
export const TICK_MS = 500;

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
    // Twice a second: a stalled feed is flagged within 3.5-4.0 s. At four times a
    // second this was the page's main steady cost (a re-layout of the age label
    // every 250 ms, on every page, all day).
    const t = setInterval(() => setNow(Date.now()), TICK_MS);
    return () => clearInterval(t);
  }, []);

  if (lastUpdate === null) return { ageMs: null, state: "dead" };

  const ageMs = Math.max(0, now - lastUpdate);
  const state: Freshness =
    ageMs > DEAD_MS ? "dead" : ageMs > STALE_MS ? "stale" : "fresh";
  return { ageMs, state };
}
