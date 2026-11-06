"use client";

import { useLiveFeed } from "@/lib/LiveContext";
import { dateTimeLabel } from "@/lib/format";
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

/** When a figure was recorded, in the desk's standard format: "06 Nov 2026, 15:24". */
export function recordedAt(ts: string | null | undefined): string {
  return dateTimeLabel(ts);
}
