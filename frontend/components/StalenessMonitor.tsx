"use client";

import { STALE_MS, useTickAge } from "@/lib/useTickAge";
import { useLiveFeed } from "@/lib/LiveContext";

/**
 * Says out loud when the feed stops arriving.
 *
 * Silence is the failure this catches. Everything on the deck still renders a
 * plausible number after the socket dies, so the danger is acting on a price
 * that stopped being true — the bar appears the moment the gap passes 3.5s.
 */
export function StalenessMonitor() {
  const { lastUpdate, connection, settlingUntil } = useLiveFeed();
  const { ageMs, state } = useTickAge(lastUpdate);

  if (state === "fresh") return null;
  // Opening the app, or coming back to it, takes a moment to reconnect; the
  // alarm is for a feed that has stopped, not one that is starting.
  if (Date.now() < settlingUntil && connection !== "live") return null;

  const seconds = ageMs === null ? null : (ageMs / 1000).toFixed(1);

  return (
    <div
      role="alert"
      aria-live="assertive"
      className={`flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border px-4 py-2.5 text-xs ${
        state === "dead"
          ? "border-critical bg-critical/15 text-critical stale-pulse"
          : "border-warning/50 bg-warning/10 text-warning"
      }`}
    >
      <span className="font-semibold uppercase tracking-[0.12em]">
        {state === "dead" ? "Feed dead" : "Feed stale"}
      </span>
      <span className="tabular-nums">
        {seconds === null
          ? "nothing has arrived yet"
          : `no update for ${seconds}s (threshold ${(STALE_MS / 1000).toFixed(1)}s)`}
      </span>
      <span className="ml-auto text-2xs opacity-80">
        {connection === "live"
          ? "socket open but silent"
          : connection === "polling"
            ? "polling fallback"
            : `connection ${connection}`}
      </span>
    </div>
  );
}

/** Compact form for the header — a dot that turns red and pulses. */
export function StalenessDot() {
  const { lastUpdate, connection, settlingUntil } = useLiveFeed();
  const { ageMs, state: raw } = useTickAge(lastUpdate);
  const settling = Date.now() < settlingUntil && connection !== "live";
  const state = settling && raw !== "fresh" ? "fresh" : raw;
  const seconds = ageMs === null || settling ? "" : `${(ageMs / 1000).toFixed(1)}s`;

  return (
    <span className="inline-flex items-center gap-1.5" title={`Last update ${seconds} ago`}>
      <span
        aria-hidden="true"
        className={`h-1.5 w-1.5 rounded-full ${
          state === "fresh"
            ? "bg-good"
            : state === "stale"
              ? "bg-warning stale-pulse"
              : "bg-critical stale-pulse"
        }`}
      />
      <span
        className={`text-2xs tabular-nums ${
          state === "fresh" ? "text-ink-muted" : "text-critical"
        }`}
      >
        {seconds}
      </span>
    </span>
  );
}
