"use client";

import { useLiveFeed } from "@/lib/LiveContext";
import type { Snapshot } from "@/lib/types";

/**
 * The desk's snapshot, and whether it is live.
 *
 * With nothing running the server keeps sending the last snapshot any
 * algorithm published, so the boxes show the last recorded state instead of
 * dashes. It is live only while the process that published it still runs —
 * everything drawn from it says "last recorded" otherwise.
 */
export function useDeskSnapshot(): { snapshot: Snapshot | null; live: boolean } {
  const { snapshot, status } = useLiveFeed();
  const pid = snapshot?.engine?.pid ?? null;
  const live =
    pid != null && Boolean(status?.fleet?.algos?.some((a) => a.running && a.pid === pid));
  return { snapshot, live };
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "today 15:24" or "30 Sep 15:24", for a naive IST timestamp. */
export function recordedAt(ts: string | null | undefined, now: number = Date.now()): string {
  if (!ts) return "—";
  const [day, time = ""] = ts.split("T");
  const hhmm = time.slice(0, 5);
  const todayIst = new Date(now + 5.5 * 3600 * 1000).toISOString().slice(0, 10);
  if (day === todayIst) return `today ${hhmm}`;
  const [, m, d] = day.split("-");
  const month = MONTHS[Number(m) - 1];
  return month ? `${Number(d)} ${month} ${hhmm}` : `${day} ${hhmm}`;
}
