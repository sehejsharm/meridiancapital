import type { ReliabilityReport } from "@/lib/types";

/** "up 40% on last week", "down 12%…", "flat", or why there is no comparison. */
export function trendText(trend: ReliabilityReport["api"]["trend"]): string {
  if (!trend) return "not enough history for a week-on-week comparison";
  if (trend.change_pct === null) return `${trend.this_week} this week, none last week`;
  if (trend.change_pct === 0) return `${trend.this_week} this week, the same as last week`;
  const dir = trend.change_pct > 0 ? "up" : "down";
  return `${trend.this_week} this week, ${dir} ${Math.abs(trend.change_pct).toFixed(0)}% on last week's ${trend.last_week}`;
}

/** Good at or above 98% of market hours, a warning below, critical below 90%. */
export function uptimeTone(pct: number | null | undefined): "good" | "warning" | "critical" | "neutral" {
  if (pct == null) return "neutral";
  if (pct >= 98) return "good";
  if (pct >= 90) return "warning";
  return "critical";
}

/** The kinds of Angel API trouble, in the order the chart stacks them. */
export const API_KINDS = [
  { key: "timeouts", label: "Timeouts", className: "bg-warning" },
  { key: "rate_limits", label: "Rate limits", className: "bg-serious" },
  { key: "upstream", label: "Upstream errors", className: "bg-critical" },
  { key: "connection", label: "Angel not answering", className: "bg-ink-muted" },
  { key: "other_errors", label: "Other errors", className: "bg-series" },
] as const;

/** A feed check after the 15:10 close touches no trade: the program is flat by then. */
export function isAfterClose(e: { ts: string; after_close?: boolean }): boolean {
  if (typeof e.after_close === "boolean") return e.after_close;
  return String(e.ts ?? "").slice(11, 16) >= "15:10";
}

/** The stream-vs-candle disagreements split into those inside trading hours and those after the close. */
export function splitDivergences(events: { ts: string; pts: number; after_close?: boolean }[]) {
  const live = events.filter((e) => !isAfterClose(e));
  const after = events.filter((e) => isAfterClose(e));
  return {
    live,
    after,
    liveMax: live.length ? Math.max(...live.map((e) => e.pts)) : null,
  };
}
